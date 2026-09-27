"""Тесты ЭЦП (Ф12): контейнер, подпись PKCS#7, проверка, реестр подписей через HTTP API."""
import json, os, shutil, sqlite3, sys, tempfile, threading, time, unittest, urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import crypto_sign
import server


class CryptoSignUnit(unittest.TestCase):
    def setUp(self):
        if not shutil.which('openssl'):
            self.skipTest('openssl недоступен')
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_selfsigned_and_sign_verify(self):
        info = crypto_sign.generate_selfsigned(self.tmp, 'director', 'Dowlet Kerimov')
        self.assertIn('CN=Dowlet Kerimov', info['subject'])
        self.assertEqual(len(info['fingerprint']), 64)
        file = self.tmp / 'doc.pdf'
        file.write_bytes(server.crypto_sign_test_pdf('test'))
        sig = self.tmp / 'doc.p7s'
        result = crypto_sign.sign_file(self.tmp, 'director', file, sig)
        self.assertTrue(sig.exists() and sig.stat().st_size > 100)
        self.assertEqual(result['fileSha256'], crypto_sign.sha256_file(file))
        check = crypto_sign.verify_file(self.tmp, 'director', file, sig)
        self.assertTrue(check['valid'], check.get('detail'))
        # tamper detection
        file.write_bytes(file.read_bytes() + b'x')
        bad = crypto_sign.verify_file(self.tmp, 'director', file, sig)
        self.assertFalse(bad['valid'])

    def test_key_dir_sanitized(self):
        with self.assertRaises(crypto_sign.SignError):
            crypto_sign.key_dir(self.tmp, '../../etc')


class SignatureApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.original_data = server.DATA
        server.DATA = cls.tmp
        server.initialize(empty=True)
        with server.connect() as db:
            for login in ['admin'] + server.PEOPLE:
                db.execute('UPDATE users SET password_hash=? WHERE username=?',
                           (server.password_hash('Testing-Resminama-123'), login))
            db.commit()
        cls.httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.base = f'http://127.0.0.1:{cls.httpd.server_address[1]}'
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        server.DATA = cls.original_data
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def client(self, login):
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
        request = urllib.request.Request(self.base + '/login',
            urlencode({'username': login, 'password': 'Testing-Resminama-123'}).encode(),
            {'Origin': self.base, 'Content-Type': 'application/x-www-form-urlencoded'})
        opener.open(request)
        return opener

    def post(self, opener, path, payload):
        request = urllib.request.Request(self.base + path,
            json.dumps(payload).encode(), {'Origin': self.base, 'Content-Type': 'application/json'})
        try:
            with opener.open(request) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def revision(self, opener):
        with opener.open(self.base + '/api/sed') as response:
            return json.loads(response.read())['revision']

    def test_full_cycle(self):
        if not shutil.which('openssl'):
            self.skipTest('openssl недоступен')
        opener = self.client('director')
        status, body = self.post(opener, '/api/signature', {'action': 'certStatus'})
        self.assertEqual(status, 200)
        self.assertIsNone(body['certificate'])
        status, body = self.post(opener, '/api/signature', {'action': 'certCreate', 'commonName': 'Довлет Керимов'})
        self.assertEqual(status, 200, body)
        self.assertIn('Довлет', body['certificate']['subject'])
        rev = self.revision(opener)
        status, body = self.post(opener, '/api/signature', {'action': 'sign', 'revision': rev})
        self.assertEqual(status, 200, body)
        record = body['signature']
        self.assertEqual(record['owner'], 'director')
        self.assertEqual(len(record['fileSha256']), 64)
        # устаревшая revision отклоняется
        status, body = self.post(opener, '/api/signature', {'action': 'sign', 'revision': rev})
        self.assertEqual(status, 409)
        # проверка подписи
        status, body = self.post(opener, '/api/signature', {'action': 'verify', 'signatureId': record['id']})
        self.assertEqual(status, 200, body)
        self.assertTrue(body['valid'], body)
        # подпись видна в состоянии сессии
        with opener.open(self.base + '/api/sed') as response:
            state = json.loads(response.read())['state']
        self.assertEqual([s['id'] for s in state['signatures']], [record['id']])
        # чужая подпись не видна
        secretary = self.client('secretary')
        with secretary.open(self.base + '/api/sed') as response:
            other = json.loads(response.read())['state']
        self.assertEqual(other['signatures'], [])
        # событие в журнале
        self.assertTrue(any(e['kind'] == 'signature' for e in state['events']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
