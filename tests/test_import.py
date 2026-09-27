import importlib.util,json,sqlite3,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from import_data import import_data

class ImportDataTests(unittest.TestCase):
    def test_copy_and_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);source=root/'Старая версия с пробелами'/'data';source.mkdir(parents=True)
            (source/'uploads').mkdir();(source/'uploads'/'file-key').write_bytes(b'original bytes')
            (source/'ACCOUNTS.txt').write_text('original passwords')
            with sqlite3.connect(source/'resminama.sqlite3') as db:
                db.executescript('CREATE TABLE users(username TEXT,password_hash TEXT,role TEXT);CREATE TABLE workspace(id INTEGER,revision INTEGER,data TEXT);')
                db.execute('INSERT INTO workspace VALUES (1,15,?)',(json.dumps({'documents':[{'id':'one'}],'attachments':[{'key':'file-key'}]}),))
                db.execute('INSERT INTO users VALUES (?,?,?)',('admin','hash unchanged','admin'))
            original=(source/'resminama.sqlite3').read_bytes();destination=root/'Новая версия'/'data'
            self.assertEqual(import_data(source.parent,destination),1)
            self.assertEqual((source/'resminama.sqlite3').read_bytes(),original)
            self.assertEqual((destination/'uploads'/'file-key').read_bytes(),b'original bytes')
            self.assertEqual((destination/'ACCOUNTS.txt').read_text(),'original passwords')
            with sqlite3.connect(destination/'resminama.sqlite3') as db:self.assertEqual(db.execute('SELECT password_hash FROM users').fetchone()[0],'hash unchanged')
            imported=(destination/'resminama.sqlite3').read_bytes()
            with self.assertRaises(ValueError):import_data(source,destination)
            self.assertEqual((destination/'resminama.sqlite3').read_bytes(),imported)
            with self.assertRaises(ValueError):import_data(source,source)
            # A missing attachment fails before making the destination visible.
            (source/'uploads'/'file-key').unlink()
            failed=root/'Incomplete'/'data'
            with self.assertRaises(ValueError):import_data(source,failed)
            self.assertFalse(failed.exists())
            self.assertFalse(list(failed.parent.glob('.resminama-import-*')))

if __name__=='__main__':unittest.main()
