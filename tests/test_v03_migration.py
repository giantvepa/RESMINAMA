"""Upgrade and backup recovery retain v0.2 data and credentials."""
import contextlib,io,json,sqlite3,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server
from import_data import import_data

class V02UpgradeTests(unittest.TestCase):
    def test_import_v02_preserves_acl_passwords_files_and_history(self):
        old=server.DATA
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'Stable v0.2'/'data';source.mkdir(parents=True);(source/'uploads').mkdir()
            state=server.initial_state();state.update(templates=[{'id':'route','name':'Saved route'}],adminAudit=[{'id':'original-audit'}])
            for d in state['documents']:d.update(isPublic=False,access=['finance'])
            did=state['documents'][0]['id'];state['attachments']=[{'id':'f1','documentId':did,'groupId':'g','version':1,'name':'original.txt','key':'original-file'}]
            (source/'uploads'/'original-file').write_bytes(b'unchanged original')
            password=server.password_hash('Existing v0.2 password')
            with sqlite3.connect(source/'resminama.sqlite3') as db:
                db.executescript("CREATE TABLE users(username TEXT PRIMARY KEY,password_hash TEXT NOT NULL,role TEXT NOT NULL,name TEXT NOT NULL,department TEXT NOT NULL,title TEXT NOT NULL,active INTEGER NOT NULL);CREATE TABLE sessions(token_hash TEXT PRIMARY KEY,username TEXT NOT NULL,expires REAL NOT NULL);CREATE TABLE workspace(id INTEGER PRIMARY KEY,revision INTEGER NOT NULL,data TEXT NOT NULL);PRAGMA user_version=2;")
                db.execute('INSERT INTO users VALUES (?,?,?,?,?,?,?)',('admin',password,'admin','Original Admin','Office','Manager',1))
                db.execute('INSERT INTO workspace VALUES (1,57,?)',(json.dumps(state,ensure_ascii=False),))
            stable=(source/'resminama.sqlite3').read_bytes();dest=root/'Version 0.3'/'data'
            import_data(source.parent,dest);server.DATA=dest
            try:
                with contextlib.redirect_stdout(io.StringIO()):server.initialize()
                with server.connect() as db:
                    revision,after=server.read_state(db)
                    self.assertEqual(revision,59);self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],4)
                    self.assertEqual(db.execute('SELECT password_hash FROM users').fetchone()[0],password)
                for before,current in zip(state['documents'],after['documents']):
                    self.assertEqual({k:current[k] for k in before},before)
                for before,current in zip(state['tasks'],after['tasks']):
                    self.assertEqual({k:current[k] for k in before},before)
                    self.assertFalse(current['requiresAcceptance'])
                for key in ['attachments','events','readings','templates','adminAudit']:self.assertEqual(after[key],state[key])
                archived=next(d for d in after['documents'] if d['status']=='archived')
                self.assertIsNone(archived['archive']['years'])
                self.assertEqual((dest/'uploads'/'original-file').read_bytes(),b'unchanged original')
                backups=list((dest/'migration_backups').glob('before_v0.3_*.sqlite3'));self.assertEqual(len(backups),1)
                with sqlite3.connect(backups[0]) as db:self.assertEqual(json.loads(db.execute('SELECT data FROM workspace').fetchone()[0]),state)
                with contextlib.redirect_stdout(io.StringIO()):server.initialize()
                with server.connect() as db:self.assertEqual(server.read_state(db),(revision,after))
                self.assertEqual((source/'resminama.sqlite3').read_bytes(),stable)
                # A v0.3 backup can be imported into a separate, clean installation.
                second=root/'Recovered v0.3'/'data';import_data(dest,second)
                with sqlite3.connect(second/'resminama.sqlite3') as db:self.assertEqual(json.loads(db.execute('SELECT data FROM workspace').fetchone()[0]),after)
                with sqlite3.connect(dest/'resminama.sqlite3') as db:db.execute('PRAGMA user_version=99')
                with self.assertRaises(ValueError):import_data(dest,root/'unsupported'/'data')
                with self.assertRaises(server.AppError):server.initialize()
            finally:server.DATA=old

class DocxValidationTests(unittest.TestCase):
    def test_schema3_active_routes_survive_types_migration(self):
        old=server.DATA
        with tempfile.TemporaryDirectory() as tmp:
            server.DATA=Path(tmp)
            try:
                with contextlib.redirect_stdout(io.StringIO()):server.initialize()
                with server.connect() as db:
                    revision,state=server.read_state(db)
                    for key in ['documentTypes','processTemplates','numberCounters']:state.pop(key,None)
                    for d in state['documents']:
                        d.pop('typeSnapshot',None);d.pop('customFields',None)
                    state['documents'][0].update(status='review',approvers=['legal','finance'],approvals={'legal':'approved'},iteration=3)
                    db.execute('UPDATE workspace SET data=?',(json.dumps(state),));db.execute('PRAGMA user_version=3')
                with contextlib.redirect_stdout(io.StringIO()):server.initialize()
                with server.connect() as db:
                    rev,after=server.read_state(db)
                    self.assertEqual(rev,revision+1);self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],4)
                self.assertEqual(len(after['documentTypes']),8);self.assertEqual(len(after['processTemplates']),8)
                for before,current in zip(state['documents'],after['documents']):
                    self.assertEqual({k:current[k] for k in before},before)
                    self.assertNotIn('routePlan',current);self.assertNotIn('process',current)
                for key in ['events','tasks','attachments','templates','readings']:self.assertEqual(after[key],state[key])
                backup=sorted((server.DATA/'migration_backups').glob('before_types_routes_*.sqlite3'))[-1]
                with sqlite3.connect(backup) as db:self.assertEqual(json.loads(db.execute('SELECT data FROM workspace').fetchone()[0]),state)
                with contextlib.redirect_stdout(io.StringIO()):server.initialize()
                with server.connect() as db:self.assertEqual(server.read_state(db),(rev,after))
            finally:server.DATA=old

    def test_untrusted_docx_packages(self):
        import docx_ops,zipfile
        original=(server.ROOT/'templates'/'memo.docx').read_bytes()
        def changed(name,contents):
            out=io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(original)) as src,zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as dst:
                for info in src.infolist():dst.writestr(info,contents if info.filename==name else src.read(info.filename))
            return out.getvalue()
        for payload in [b'not docx', changed('[Content_Types].xml',b'macroEnabled'),changed('word/document.xml',b'<!DOCTYPE x [<!ENTITY x "boom">]><x>&x;</x>'),changed('word/document.xml',b'<invalid/>')]:
            with self.assertRaises(ValueError):docx_ops.preview(payload)
        with zipfile.ZipFile(io.BytesIO(original)) as z:types=z.read('[Content_Types].xml')
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('[Content_Types].xml',types);z.writestr('word/document.xml',b' '*(8*1024*1024+1))
        with self.assertRaises(ValueError):docx_ops.preview(out.getvalue())

if __name__=='__main__':unittest.main()
