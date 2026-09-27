"""Run: python -m unittest discover -s tests -v"""
import contextlib, http.cookiejar, io, json, sqlite3, sys, tempfile, threading, unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPCookieProcessor, ProxyHandler
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server

class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.old_data=server.DATA;server.DATA=Path(cls.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):server.initialize(empty=True)
        with server.connect() as db:
            for login in ['admin']+server.PEOPLE:db.execute('UPDATE users SET password_hash=? WHERE username=?',(server.password_hash('Testing-RESMINAMA-123'),login))
        class QuietHandler(server.Handler):
            def log_message(self,*args):pass
        cls.http=server.ThreadingHTTPServer(('127.0.0.1',0),QuietHandler)
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True);cls.thread.start()
        cls.base='http://127.0.0.1:'+str(cls.http.server_port)
        cls.clients={}
        for login in ['admin']+server.PEOPLE:
            client=build_opener(ProxyHandler({}),HTTPCookieProcessor(http.cookiejar.CookieJar()))
            request=Request(cls.base+'/login',urlencode({'username':login,'password':'Testing-RESMINAMA-123'}).encode(),{'Origin':cls.base,'Content-Type':'application/x-www-form-urlencoded'})
            assert client.open(request).status==200;cls.clients[login]=client
        cls.anon=build_opener(ProxyHandler({}))
    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();cls.thread.join();server.DATA=cls.old_data;cls.temp.cleanup()
    def request(self,path,body=None,user='secretary',mime='application/json',origin=True):
        data=json.dumps(body).encode() if isinstance(body,dict) else body
        headers={'Content-Type':mime}
        if origin:headers['Origin']=self.base
        try:
            with (self.clients[user] if user else self.anon).open(Request(self.base+path,data,headers)) as r:return r.status,r.read(),r.headers
        except HTTPError as e:return e.code,e.read(),e.headers
    def state(self,user='secretary'):
        status,raw,_=self.request('/api/sed',user=user);self.assertEqual(status,200);return json.loads(raw)
    def action(self,action,user='secretary',status=200,**kwargs):
        if action=='create':kwargs['payload']={'routeTemplateId':'',**kwargs.get('payload',{})}
        b={'action':action,'actor':user if user!='admin' else 'secretary','revision':self.state(user)['revision'],**kwargs}
        result,raw,_=self.request('/api/sed',b,user);self.assertEqual(result,status,raw)
        return json.loads(raw)
    def upload(self,docid,contents,user='secretary',status=200,filename='document.txt',groupid=None,base=None,purpose=None,comment=''):
        boundary='RESMINAMATestBoundary';parts=[]
        snapshot=self.state(user)
        values={'actor':user,'revision':str(snapshot['revision']),'documentId':docid}
        if purpose:values.update(purpose=purpose,comment=comment)
        prior=next((f for f in snapshot['state']['attachments'] if f['documentId']==docid and (f['groupId']==groupid if groupid else f['name']==filename)),None)
        if prior and purpose!='process':values.update(groupId=prior['groupId'],baseVersion=str(base if base is not None else max(f['version'] for f in snapshot['state']['attachments'] if f['groupId']==prior['groupId'])))
        elif groupid:values['groupId']=groupid
        for key,value in values.items():parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: text/plain\r\n\r\n'.encode()+contents+b'\r\n')
        parts.append(f'--{boundary}--\r\n'.encode())
        code,raw,_=self.request('/api/files',b''.join(parts),user,'multipart/form-data; boundary='+boundary)
        self.assertEqual(code,status,raw);return json.loads(raw)
    def test_01_access_boundaries(self):
        self.assertEqual(self.request('/api/sed',user=None)[0],401)
        self.assertEqual(self.request('/api/sed',{'action':'readEvents'},origin=False)[0],403)
        self.assertEqual(self.request('/api/files?id=unknown',user=None)[0],401)
        self.assertEqual(self.request('/api/files?id=unknown')[0],404)
        self.assertEqual(self.request('/../server.py')[0],403)
        self.action('create',actor='director',status=403,payload={'title':'Spoof','type':'internal'})
        self.assertEqual(self.state('legal')['session']['actor'],'legal')
        self.assertFalse(self.state('legal')['session']['canSwitch'])
        self.assertTrue(self.state('admin')['session']['canSwitch'])
    def test_02_complete_workflow(self):
        r=self.action('create',payload={'title':'Workflow test','type':'contract','date':'2026-09-18','due':'2026-09-30','description':'Version one'})
        docid=r['state']['documents'][0]['id'];self.assertEqual(r['state']['documents'][0]['status'],'draft')
        old_revision=r['revision']
        r=self.upload(docid,b'First version');first_file=r['state']['attachments'][0]['id']
        r=self.upload(docid,b'Second version');self.assertEqual([f['version'] for f in r['state']['attachments']],[2,1])
        self.upload(docid,b'Unauthorized',user='legal',status=403)
        self.action('edit',documentId=docid,revision=old_revision,status=409,payload={'title':'stale'})
        self.action('submit',documentId=docid,approvers=['legal','finance'],mode='sequential',finalApprover='director')
        self.action('approve',user='finance',documentId=docid,status=403)
        self.action('reject',user='legal',documentId=docid,comment='',status=400)
        r=self.action('reject',user='legal',documentId=docid,comment='Correct section 3')
        self.assertEqual(r['state']['documents'][0]['status'],'revision')
        self.upload(docid,b'Revised version');self.assertEqual(self.state()['state']['attachments'][0]['version'],3)
        self.action('edit',documentId=docid,payload={'title':'Workflow test updated','date':'2026-09-18','due':'2026-09-30'})
        self.action('submit',documentId=docid,approvers=['legal','finance'],mode='sequential',finalApprover='director')
        self.action('approve',user='legal',documentId=docid)
        self.action('participants',documentId=docid,approvers=['legal','finance','it'],mode='sequential',comment='',status=400)
        r=self.action('participants',documentId=docid,approvers=['legal','finance','it'],mode='sequential',comment='Add IT reviewer')
        self.assertEqual(r['state']['documents'][0]['approvals']['legal'],'approved')
        self.action('approve',user='it',documentId=docid,status=403)
        self.action('approve',user='finance',documentId=docid)
        r=self.action('approve',user='it',documentId=docid)
        self.assertEqual(r['state']['documents'][0]['status'],'approval')
        self.action('confirm',documentId=docid,status=403)
        r=self.action('confirm',user='director',documentId=docid)
        self.assertEqual(r['state']['documents'][0]['status'],'approved')
        self.upload(docid,b'After approval',status=403)
        r=self.action('reading',documentId=docid,recipients=['legal','finance'],due='2026-09-30',comment='Read document');rid=r['state']['readings'][0]['id']
        self.action('acknowledge',user='it',readingId=rid,status=403)
        self.action('acknowledge',user='legal',readingId=rid)
        r=self.action('reading',documentId=docid,readingId=rid,recipients=['legal','it'],comment='Change recipients')
        self.assertEqual(r['state']['readings'][0]['done'],['legal'])
        r=self.action('createTask',documentId=docid,title='Implement document',assignee='it',due='2026-10-01');tid=r['state']['tasks'][0]['id']
        self.assertEqual(r['state']['documents'][0]['status'],'execution')
        self.action('taskStatus',user='finance',taskId=tid,**{'status':403})
        # `status` is the HTTP expectation in helper; explicitly send task status below.
        for new_status in ['progress','done']:
            state=self.state('it');code,raw,_=self.request('/api/sed',{'action':'taskStatus','actor':'it','revision':state['revision'],'taskId':tid,'status':new_status,'report':'Completed according to assignment'},'it');self.assertEqual(code,200,raw)
        self.action('taskAccept',taskId=tid)
        self.assertEqual(self.state()['state']['documents'][0]['status'],'complete')
        self.action('archive',documentId=docid,status=400)
        self.action('acknowledge',user='it',readingId=rid)
        r=self.action('archive',documentId=docid);self.assertEqual(r['state']['documents'][0]['status'],'archived')
        code,raw,headers=self.request('/api/files?id='+first_file);self.assertEqual(code,200);self.assertEqual(raw,b'First version');self.assertIn('attachment',headers['Content-Disposition'])
        self.action('readEvents');self.assertTrue(self.state()['state']['readEvents']);self.assertFalse(self.state('finance')['state']['readEvents'])
        with server.connect() as db:revision,state=server.read_state(db)
        self.assertEqual(state['documents'][0]['status'],'archived');self.assertGreater(len(state['events']),15)
    def test_03_static_bundle_and_backup(self):
        code,raw,_=self.request('/');self.assertEqual(code,200);self.assertIn(b'/assets/',raw)
        files=list((server.ROOT/'web'/'assets').glob('*.css'));self.assertTrue(files)
        css=files[0].read_text();self.assertNotIn('@tailwind',css);self.assertNotIn('@theme',css)
        # Consistent SQLite backup through a fresh connection.
        destination=server.DATA/'test-backup.sqlite3'
        with server.connect() as source,sqlite3.connect(destination) as target:source.backup(target)
        with sqlite3.connect(destination) as db:self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')

    def login_client(self,username,password):
        client=build_opener(ProxyHandler({}),HTTPCookieProcessor(http.cookiejar.CookieJar()))
        request=Request(self.base+'/login',urlencode({'username':username,'password':password}).encode(),{'Origin':self.base,'Content-Type':'application/x-www-form-urlencoded'})
        with client.open(request) as response:self.assertEqual(response.status,200)
        self.clients[username]=client
        return client

    def test_04_private_documents_and_files(self):
        r=self.action('create',payload={'title':'Private ACL test','type':'internal'})
        did=r['state']['documents'][0]['id']
        r=self.upload(did,b'confidential body');fid=r['state']['attachments'][0]['id']
        self.assertFalse(r['state']['documents'][0]['isPublic'])
        for outsider in ['it','legal','director']:
            state=self.state(outsider)['state']
            for key in ['documents','attachments','events','tasks','readings']:
                self.assertFalse(any(v.get('documentId',v.get('id'))==did for v in state[key]))
            self.assertEqual(self.request('/api/files?id='+fid,user=outsider)[0],403)
            self.action('comment',user=outsider,documentId=did,comment='spoof',status=403)
        self.assertTrue(any(d['id']==did for d in self.state('admin')['state']['documents']))
        self.action('access',documentId=did,isPublic=False,access=['legal'])
        self.assertEqual(self.request('/api/files?id='+fid,user='legal')[1],b'confidential body')
        self.action('comment',user='legal',documentId=did,comment='Read-only discussion allowed')
        self.action('access',user='legal',documentId=did,isPublic=True,access=[],status=403)
        self.action('reading',user='legal',documentId=did,recipients=['it'],status=403)
        self.action('createTask',user='legal',documentId=did,title='Expand access',assignee='it',status=403)
        self.action('access',documentId=did,isPublic=False,access=[])
        self.assertEqual(self.request('/api/files?id='+fid,user='legal')[0],403)
        self.action('access',documentId=did,isPublic=True,access=[])
        self.assertTrue(any(d['id']==did for d in self.state('it')['state']['documents']))
        self.action('access',documentId=did,isPublic=False,access=[])
        self.action('submit',documentId=did,approvers=['legal'],finalApprover='director',mode='parallel')
        self.action('access',documentId=did,isPublic=False,access=[])
        # Removing explicit access cannot remove an assigned participant.
        self.assertEqual(self.request('/api/files?id='+fid,user='legal')[0],200)
        self.action('participants',user='legal',documentId=did,approvers=['it'],finalApprover='director',comment='unauthorized route change',status=403)
        self.action('reading',documentId=did,recipients=['it'])
        self.assertEqual(self.request('/api/files?id='+fid,user='it')[0],200)
        self.action('createTask',documentId=did,title='Assignment grants access',assignee='finance')
        self.assertEqual(self.request('/api/files?id='+fid,user='finance')[0],200)
        for user in ['legal','finance','it']:
            state=self.state(user)['state']
            self.assertNotIn('adminAudit',state);self.assertNotIn('readEventsByUser',state)
            self.assertTrue(all('key' not in f for f in state['attachments']))

    def test_05_manage_users(self):
        values={'username':'new.person','name':'Новый Сотрудник','department':'Закупки','title':'Специалист','role':'employee'}
        self.action('userCreate',**values,status=403)
        self.action('userCreate',user='admin',**{**values,'username':'bad name'},status=400)
        r=self.action('userCreate',user='admin',**values);password=r['receipt']['password']
        self.assertEqual(r['receipt']['username'],'new.person')
        self.assertNotIn(password,json.dumps(self.state('admin')))
        self.action('userCreate',user='admin',**values,status=400)
        self.login_client('new.person',password)
        self.assertEqual(self.state('new.person')['session']['actor'],'new.person')
        self.assertFalse(self.state('new.person')['state']['documents'])
        self.action('create',user='new.person',actor='secretary',payload={'title':'spoof','type':'internal'},status=403)
        self.action('userEdit',user='admin',**{**values,'name':'Изменённое Имя','department':'Снабжение'})
        self.assertEqual(next(p for p in self.state()['state']['people'] if p['id']=='new.person')['name'],'Изменённое Имя')
        self.action('userActive',user='admin',username='new.person',active=False)
        self.assertEqual(self.request('/api/sed',user='new.person')[0],401)
        self.login_client('new.person',password)
        self.assertEqual(self.request('/api/sed',user='new.person')[0],401)
        r=self.action('create',payload={'title':'Disabled participant check','type':'internal'});disabled_doc=r['state']['documents'][0]['id']
        self.action('submit',documentId=disabled_doc,approvers=['new.person'],finalApprover='director',status=400)
        self.action('templateSave',name='Inactive recipient',approvers=['new.person'],finalApprover='director',status=400)
        self.assertEqual(self.state()['state']['documents'][0]['status'],'draft')
        self.action('userActive',user='admin',username='new.person',active=True)
        self.login_client('new.person',password);self.state('new.person')
        r=self.action('userReset',user='admin',username='new.person');newpassword=r['receipt']['password']
        self.assertNotEqual(newpassword,password)
        self.assertEqual(self.request('/api/sed',user='new.person')[0],401)
        self.login_client('new.person',password);self.assertEqual(self.request('/api/sed',user='new.person')[0],401)
        self.login_client('new.person',newpassword);self.state('new.person')
        r=self.action('create',user='new.person',payload={'title':'New employee document','type':'internal'})
        did=r['state']['documents'][0]['id']
        self.action('userActive',user='admin',username='new.person',active=False,status=400)
        self.action('userActive',user='admin',username='admin',active=False,status=400)
        self.action('userEdit',user='admin',username='admin',name='Admin',role='employee',status=400)
        # Newly created users participate in live approval routes, not just the directory.
        r=self.action('create',payload={'title':'Dynamic approval','type':'internal'});route=r['state']['documents'][0]['id']
        self.action('submit',documentId=route,approvers=['new.person'],mode='parallel',finalApprover='director')
        r=self.action('approve',user='new.person',documentId=route)
        self.assertEqual(next(d for d in r['state']['documents'] if d['id']==route)['status'],'approval')
        self.action('userEdit',user='admin',**{**values,'role':'admin'})
        self.assertEqual(self.request('/api/sed',user='new.person')[0],401)
        self.login_client('new.person',newpassword)
        self.assertTrue(self.state('new.person')['session']['canSwitch'])
        self.action('userEdit',user='admin',**values)
        self.assertEqual(self.request('/api/sed',user='new.person')[0],401)
        self.assertTrue(self.state('admin')['state']['adminAudit'])

    def test_06_route_templates(self):
        values={'name':'Договоры — стандартный','mode':'sequential','approvers':['legal','finance'],'finalApprover':'director'}
        r=self.action('templateSave',**values);t=r['state']['templates'][-1];tid=t['id']
        self.assertEqual(t['owner'],'secretary')
        self.assertEqual(self.state('it')['state']['templates'][-1]['id'],tid)
        self.action('templateSave',**values,status=400)
        self.action('templateSave',templateId=tid,user='legal',**values,status=403)
        self.action('templateDelete',templateId=tid,user='legal',status=403)
        self.action('templateSave',name='Unknown actor',approvers=['missing'],finalApprover='director',status=400)
        r=self.action('create',payload={'title':'Template flow','type':'contract'});did=r['state']['documents'][0]['id']
        self.action('submit',documentId=did,**{k:t[k] for k in ['approvers','mode','finalApprover']})
        self.action('templateSave',templateId=tid,**{**values,'approvers':['it']})
        self.assertEqual(next(d for d in self.state()['state']['documents'] if d['id']==did)['approvers'],['legal','finance'])
        self.action('approve',documentId=did,user='finance',status=403)
        self.action('approve',documentId=did,user='legal')
        self.action('templateDelete',templateId=tid,user='admin')
        self.assertFalse(any(t['id']==tid for t in self.state()['state']['templates']))
        self.action('approve',documentId=did,user='finance')

    def test_07_card_editors_links_and_audit(self):
        visible=self.action('create',payload={'title':'Linked document','type':'incoming'})['state']['documents'][0]['id']
        fields={'title':'Extended card','type':'internal','externalNumber':'EXT-2026-44','externalDate':'2026-09-24','registeredAt':'2026-09-25','category':'Приказ','tags':'IT, сеть','priority':'urgent','relatedIds':[visible]}
        did=self.action('create',payload=fields)['state']['documents'][0]['id']
        self.action('access',documentId=did,isPublic=False,access=['finance'],editors=['legal'])
        legal=next(d for d in self.state('legal')['state']['documents'] if d['id']==did)
        self.assertEqual(legal['relatedIds'],[])
        self.assertEqual(legal['externalNumber'],'EXT-2026-44')
        self.action('edit',user='finance',documentId=did,payload={'title':'Forbidden'},status=403)
        self.action('edit',user='legal',documentId=did,payload={'title':'Editor changed title','relatedIds':[]})
        self.assertEqual(next(d for d in self.state()['state']['documents'] if d['id']==did)['relatedIds'],[visible])
        self.action('access',user='legal',documentId=did,isPublic=True,access=[],editors=[],status=403)
        self.action('edit',documentId=did,payload={'title':'Wrong link','relatedIds':[did]},status=403)
        event=next(e for e in self.state()['state']['events'] if e['documentId']==did and e['action']=='Изменены реквизиты')
        self.assertIn('Extended card → Editor changed title',event['comment'])
        rights=next(e for e in self.state()['state']['events'] if e['documentId']==did and e['action']=='Изменены права доступа')
        self.assertEqual(rights['changes']['after']['editors'],['legal'])
        self.action('submit',user='legal',documentId=did,approvers=['finance'],finalApprover='director')
        self.action('edit',user='legal',documentId=did,payload={'title':'Too late'},status=403)

    def task_status(self,taskid,target,user='it',report='',expected=200):
        code,raw,_=self.request('/api/sed',{'action':'taskStatus','revision':self.state(user)['revision'],'taskId':taskid,'status':target,'report':report},user)
        self.assertEqual(code,expected,raw)
        return json.loads(raw)

    def test_08_task_acceptance_reassignment_and_visibility(self):
        did=self.action('create',payload={'title':'Controlled execution','type':'internal'})['state']['documents'][0]['id']
        self.action('access',documentId=did,isPublic=False,access=[],editors=['legal'])
        r=self.action('createTask',user='legal',documentId=did,title='Prepare reports',assignees=['it','finance'],controller='director',due='2026-09-30')
        tasks=[t for t in r['state']['tasks'] if t['documentId']==did];self.assertEqual(len(tasks),2)
        tid=next(t['id'] for t in tasks if t['assignee']=='it')
        self.action('access',documentId=did,isPublic=False,access=[],editors=[])
        self.assertTrue(any(d['id']==did for d in self.state('legal')['state']['documents']))
        self.task_status(tid,'done',expected=400)
        self.task_status(tid,'progress',user='finance',expected=403)
        self.task_status(tid,'progress')
        self.task_status(tid,'done',expected=400)
        self.task_status(tid,'verification',report='Work delivered')
        self.action('taskAccept',user='it',taskId=tid,status=403)
        self.action('taskReturn',user='director',taskId=tid,status=400)
        self.action('taskReturn',user='director',taskId=tid,comment='Add measurements')
        self.task_status(tid,'verification',report='Measurements attached')
        self.action('taskEdit',user='director',taskId=tid,assignee='finance',controller='director',due='2026-10-01',comment='Reassigned for final checks')
        self.task_status(tid,'progress',expected=403)
        self.task_status(tid,'progress',user='finance')
        self.task_status(tid,'verification',user='finance',report='Checks complete')
        result=self.action('taskAccept',user='director',taskId=tid)
        self.assertEqual(next(t for t in result['state']['tasks'] if t['id']==tid)['status'],'done')
        self.action('taskCancel',user='director',taskId=tid,comment='Cannot cancel closed task',status=400)
        other=next(t['id'] for t in tasks if t['id']!=tid)
        self.action('taskCancel',user='legal',taskId=other,comment='No longer required')
        standalone=self.action('createTask',title='Private standalone',assignees=['it'],controller='legal')['state']['tasks'][0]['id']
        for user in ['finance','director']:
            self.assertFalse(any(t['id']==standalone for t in self.state(user)['state']['tasks']))
            self.action('taskEdit',user=user,taskId=standalone,comment='Unauthorized',status=403)
        self.task_status(standalone,'progress')
        self.task_status(standalone,'verification',report='Standalone report')
        self.action('taskAccept',user='legal',taskId=standalone)

    def test_09_archive_roles_retention_and_reopening(self):
        r=self.action('userCreate',user='admin',username='archive.user',name='Архивариус',department='Архив',title='Архивариус',role='archivist')
        self.login_client('archive.user',r['receipt']['password'])
        self.action('caseSave',index='02-01',title='Постоянное хранение',years=0,status=403)
        caseid=self.action('caseSave',user='archive.user',index='02-01',title='Постоянное хранение',years=0)['state']['cases'][-1]['id']
        did=self.action('create',payload={'title':'Archive record','type':'internal','caseId':caseid})['state']['documents'][0]['id']
        self.assertFalse(any(d['id']==did for d in self.state('archive.user')['state']['documents']))
        self.action('submit',documentId=did,approvers=['legal'],finalApprover='director')
        self.action('approve',user='legal',documentId=did)
        self.action('confirm',user='director',documentId=did)
        tid=self.action('createTask',documentId=did,title='Execute before archiving',assignees=['it'],controller='finance')['state']['tasks'][0]['id']
        self.action('archive',documentId=did,status=403)
        self.task_status(tid,'progress');self.task_status(tid,'verification',report='Result ready')
        self.action('archive',documentId=did,status=403)
        self.action('taskAccept',user='finance',taskId=tid)
        rid=self.action('reading',documentId=did,recipients=['legal'])['state']['readings'][0]['id']
        self.action('archive',documentId=did,status=400)
        self.action('acknowledge',user='legal',readingId=rid)
        d=self.action('archive',documentId=did,caseId=caseid,location='Шкаф 2, полка 3')['state']['documents'][0]
        self.assertEqual(d['archive']['years'],0);self.assertEqual(d['archive']['reviewAfter'],'')
        self.assertTrue(any(x['id']==did for x in self.state('archive.user')['state']['documents']))
        self.action('caseSave',user='archive.user',caseId=caseid,index='02-01',title='Архивное дело',years=7,closed=True)
        d=next(x for x in self.state()['state']['documents'] if x['id']==did)
        self.assertEqual(d['archive']['years'],0)
        for action,kwargs in [('edit',{'payload':{'title':'Invalid'}}),('comment',{'comment':'Invalid'}),('createTask',{'title':'Invalid','assignees':['it']}),('reading',{'recipients':['finance']})]:
            self.action(action,documentId=did,status=403,**kwargs)
        self.action('acknowledge',user='legal',readingId=rid,status=403)
        self.upload(did,b'Archive mutation',status=403)
        self.action('restore',documentId=did,comment='Invalid user',status=403)
        self.action('restore',user='archive.user',documentId=did,status=400)
        self.action('restore',user='archive.user',documentId=did,comment='Further work required')
        self.action('reopen',documentId=did,status=400)
        self.action('reopen',documentId=did,comment='Correct details after archive')
        self.action('edit',documentId=did,payload={'title':'Revised archive document','caseId':'general'})
        self.action('submit',documentId=did,approvers=['legal'],finalApprover='director',onlyPending=True)
        self.assertEqual(next(d for d in self.state()['state']['documents'] if d['id']==did)['approvals'],{})
        self.action('approve',user='legal',documentId=did);self.action('confirm',user='director',documentId=did)
        d=self.action('archive',documentId=did)['state']['documents'][0]
        self.assertEqual(d['archive']['reviewAfter'],str(int(server.today()[:4])+6)+'-01-01')

    def test_10_docx_and_concurrent_file_versions(self):
        import docx_ops,zipfile
        did=self.action('create',payload={'title':'DOCX & <test>','type':'internal','description':'Строка один\nСтрока два'})['state']['documents'][0]['id']
        self.action('access',documentId=did,isPublic=False,access=['finance'],editors=['legal'])
        self.action('generateDocx',user='finance',documentId=did,templateId='memo',status=403)
        r=self.action('generateDocx',user='legal',documentId=did,templateId='memo')
        f=r['state']['attachments'][0];fid=f['id'];group=f['groupId']
        code,raw,_=self.request('/api/files?id='+fid);self.assertEqual(code,200)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            self.assertIn(b'<w:br',z.read('word/document.xml'))
        self.assertIn('DOCX & <test>', '\n'.join(docx_ops.preview(raw)['paragraphs']))
        self.assertEqual(self.request('/api/docx-preview?id='+fid,user='it')[0],403)
        self.assertEqual(self.request('/api/card-docx?id='+did,user='it')[0],403)
        self.assertEqual(self.request('/api/docx-preview?id='+fid,user='finance')[0],200)
        card=self.request('/api/card-docx?id='+did,user='finance')
        self.assertEqual(card[0],200);self.assertIn('DOCX & <test>','\n'.join(docx_ops.preview(card[1])['paragraphs']))
        self.action('fileCheckout',user='legal',documentId=did,fileId=fid)
        self.action('fileCheckout',documentId=did,fileId=fid,status=409)
        self.action('userActive',user='admin',username='legal',active=False,status=400)
        self.action('access',documentId=did,isPublic=False,access=[],editors=[],status=400)
        self.upload(did,raw,filename='revised.docx',groupid=group,status=409)
        self.action('submit',documentId=did,approvers=['finance'],finalApprover='director',status=400)
        self.action('fileRelease',user='admin',documentId=did,fileId=fid,status=400)
        self.action('fileRelease',user='admin',documentId=did,fileId=fid,comment='Administrator releases abandoned lease')
        self.action('fileCheckout',user='legal',documentId=did,fileId=fid)
        r=self.upload(did,raw,user='legal',filename='revised.docx',groupid=group,base=1)
        self.assertEqual(r['state']['attachments'][0]['version'],2)
        self.assertFalse(any(l['groupId']==group for l in r['state']['fileLocks']))
        self.upload(did,raw,user='legal',filename='revised.docx',groupid=group,base=1,status=409)
        self.action('fileCheckout',user='legal',documentId=did,fileId=fid,status=409)
        self.upload(did,b'not a zip',filename='invalid.docx',status=400)
        self.assertEqual(self.request('/api/files?id='+fid)[1],raw)
        self.action('submit',documentId=did,approvers=['finance'],finalApprover='director')
        self.action('generateDocx',documentId=did,templateId='memo',status=403)


class MigrationTests(unittest.TestCase):
    def test_v01_upgrade_preserves_records_passwords_and_uploads(self):
        old=server.DATA
        with tempfile.TemporaryDirectory() as directory:
            server.DATA=Path(directory);(server.DATA/'uploads').mkdir()
            try:
                state=server.initial_state();original=json.loads(json.dumps(state))
                password=server.password_hash('Old-installation-password')
                attachment=b'original attachment bytes';(server.DATA/'uploads'/'test-file').write_bytes(attachment)
                with sqlite3.connect(server.DATA/'resminama.sqlite3') as db:
                    db.executescript('CREATE TABLE users(username TEXT PRIMARY KEY,password_hash TEXT NOT NULL,role TEXT NOT NULL);CREATE TABLE sessions(token_hash TEXT PRIMARY KEY,username TEXT NOT NULL,expires REAL NOT NULL);CREATE TABLE workspace(id INTEGER PRIMARY KEY,revision INTEGER NOT NULL,data TEXT NOT NULL);')
                    db.execute('INSERT INTO users VALUES (?,?,?)',('secretary',password,'secretary'))
                    db.execute('INSERT INTO users VALUES (?,?,?)',('admin',password,'admin'))
                    db.execute('INSERT INTO workspace VALUES (1,87,?)',(json.dumps(state),))
                server.initialize()
                with server.connect() as db:
                    rev,state=server.read_state(db)
                    self.assertEqual(rev,90);self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],4)
                    self.assertEqual(db.execute('SELECT password_hash FROM users WHERE username="secretary"').fetchone()[0],password)
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0],2)
                for before,after in zip(original['documents'],state['documents']):
                    self.assertEqual({k:after[k] for k in before},before)
                    self.assertTrue(after['isPublic']);self.assertEqual(after['access'],[])
                for key in ['attachments','readings','events']:self.assertEqual(original[key],state[key])
                self.assertEqual((server.DATA/'uploads'/'test-file').read_bytes(),attachment)
                backups=list((server.DATA/'migration_backups').glob('*.sqlite3'));self.assertEqual(len(backups),3);backups.sort(key=lambda p:(not p.name.startswith('before_v0.2_'),p.name))
                with sqlite3.connect(backups[0]) as db:
                    self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],0)
                    self.assertEqual(json.loads(db.execute('SELECT data FROM workspace').fetchone()[0]),original)
                server.initialize()
                with server.connect() as db:self.assertEqual(server.read_state(db),(rev,state))
                self.assertEqual(len(list((server.DATA/'migration_backups').glob('*.sqlite3'))),3)
                self.assertFalse((server.DATA/'ACCOUNTS.txt').exists())
            finally:server.DATA=old

if __name__=='__main__':unittest.main()

