"""Two distinct document routes use the same configurable workflow engine."""
import json,unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server
import test_workflow as base

class UniversalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):base.IntegrationTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls):base.IntegrationTests.tearDownClass.__func__(cls)
    request=base.IntegrationTests.request
    state=base.IntegrationTests.state
    action=base.IntegrationTests.action
    upload=base.IntegrationTests.upload
    task_status=base.IntegrationTests.task_status
    login_client=base.IntegrationTests.login_client
    def doc(self,did,user='secretary'):return next(d for d in self.state(user)['state']['documents'] if d['id']==did)
    def create(self,kind,route,title,**fields):return self.action('create',payload={'type':kind,'routeTemplateId':route,'title':title,**fields})['state']['documents'][0]['id']
    def step(self,did):return server.universal.current(self.doc(did))
    def finish_task(self,t,user,controller='secretary'):
        self.task_status(t,'progress',user=user)
        self.task_status(t,'verification',user=user,report='Работа выполнена, результат проверен')
        self.action('taskAccept',user=controller,taskId=t)

    def test_01_central_bank_letter_resolution_answer_archive(self):
        did=self.create('incoming','incoming-flow','О предоставлении отчётности',correspondent='Центральный банк',externalNumber='ЦБ-25/09',customFields={'organization':'Рысгалбанк','basis':'Запрос Центрального банка','curator':'director'})
        self.assertFalse(any(d['id']==did for d in self.state('director')['state']['documents']))
        self.action('processStart',documentId=did)
        self.assertEqual(self.doc(did)['status'],'resolution')
        for action in ['archive','confirm','approve','submit']:
            self.action(action,documentId=did,status=403)
        self.action('processResolve',user='finance',documentId=did,comment='Spoof',assignees=['it'],controller='finance',due='2026-10-05',status=403)
        self.action('processResolve',user='director',documentId=did,assignees=['it'],controller='director',due='2026-10-05',status=400)
        self.action('processResolve',user='director',documentId=did,comment='Подготовить данные и проект ответа',assignees=['it','finance'],controller='legal',due='2026-10-05')
        s=self.step(did);self.assertEqual(s['kind'],'execution');self.assertEqual(len(s['taskIds']),2)
        tasks=[t for t in self.state()['state']['tasks'] if t['id'] in s['taskIds']]
        self.action('processReply',documentId=did,comment='Too early',status=403)
        self.action('taskCancel',user='legal',taskId=tasks[0]['id'],comment='Skip mandatory work',status=403)
        for i,t in enumerate(tasks):
            self.task_status(t['id'],'progress',user=t['assignee'])
            self.task_status(t['id'],'verification',user=t['assignee'],report='Подготовлены данные')
            self.assertEqual(self.step(did)['kind'],'execution')
            self.action('taskAccept',user=t['assignee'],taskId=t['id'],status=403)
            if i==0:
                self.action('taskReturn',user='legal',taskId=t['id'],comment='Уточнить показатели')
                self.task_status(t['id'],'verification',user=t['assignee'],report='Показатели уточнены')
            self.action('taskAccept',user='legal',taskId=t['id'])
        self.assertEqual(self.step(did)['kind'],'reply')
        spec=next(t for t in self.state('admin')['state']['documentTypes'] if t['id']=='outgoing')
        fields=spec['fields']+[{'key':'dispatch_register','label':'Реестр отправки','kind':'text','required':True,'options':[]}]
        self.action('documentTypeSave',user='admin',**{**spec,'fields':fields})
        self.action('processReply',documentId=did,comment='Ответ направлен',recipient='Центральный банк',channel='Бумажное письмо',sentDate='2026-09-25',status=400)
        content=(server.ROOT/'templates'/'letter.docx').read_bytes()
        self.upload(did,content,user='it',filename='answer.docx',purpose='process',status=403)
        r=self.upload(did,content,filename='answer.docx',purpose='process',comment='Отправленная редакция');fid=r['state']['attachments'][0]['id']
        reply_data=dict(documentId=did,comment='Предоставляем запрошенную отчётность',recipient='Центральный банк',channel='Бумажное письмо',sentDate='2026-09-25',fileId=fid)
        self.action('processReply',**reply_data,status=400)
        self.assertEqual(self.step(did)['kind'],'reply')
        self.action('processReply',**reply_data,customFields={'dispatch_register':'Р-2026-01'})
        doc=self.doc(did);rid=doc['replyDocumentId'];reply=self.doc(rid)
        self.assertEqual(reply['type'],'outgoing');self.assertEqual(reply['status'],'complete');self.assertIn(did,reply['relatedIds']);self.assertIn(rid,doc['relatedIds'])
        self.assertEqual(reply['customFields']['dispatch_register'],'Р-2026-01')
        self.action('documentTypeSave',user='admin',**spec)
        self.assertTrue(reply['number'].startswith('ИСХ-'+server.today()[:4]+'-'));self.assertEqual(reply['dispatch']['recipient'],'Центральный банк')
        files=[f for f in self.state()['state']['attachments'] if f['documentId']==rid];self.assertEqual(len(files),1)
        self.assertEqual(self.request('/api/files?id='+files[0]['id'])[1],content)
        self.assertEqual(self.step(did)['kind'],'archive')
        self.action('archive',documentId=did,location='Дело входящих писем')
        self.assertEqual(self.doc(did)['status'],'archived');self.assertEqual(self.doc(did)['process']['state'],'completed')
        for action,params in [('processStart',{}),('reading',{'recipients':['it']}),('createTask',{'title':'New','assignees':['it']}),('access',{'isPublic':True,'access':[]}),('processReply',{})]:
            self.action(action,documentId=did,status=403,**params)
        events=[e for e in self.state()['state']['events'] if e['documentId']==did]
        self.assertTrue(any(e.get('transition',{}).get('after')=='reply' for e in events));self.assertTrue(any(e.get('fileId')==fid for e in events))
        self.assertTrue(any(e.get('iteration')==1 and e['action']=='Наложена резолюция' for e in events))

    def test_02_contract_parallel_review_approval_execution(self):
        did=self.create('contract','contract-flow','Договор поставки',amount='12500',currency='TMT',customFields={'obligations_start':'2026-09-25','obligations_end':'2026-12-31','vat':'Без НДС'})
        self.action('processStart',documentId=did)
        self.assertEqual(self.step(did)['kind'],'review')
        self.action('processDecision',user='director',documentId=did,decision='accept',status=403)
        for user in ['it','legal']:
            self.action('processDecision',user=user,documentId=did,decision='accept');self.assertEqual(self.step(did)['kind'],'review')
        self.action('processDecision',user='finance',documentId=did,decision='reject',comment='Уточните бюджет')
        self.assertEqual(self.doc(did)['status'],'revision')
        self.action('edit',documentId=did,payload={'title':'Договор поставки — исправлен','amount':'12000'})
        self.action('processStart',documentId=did);self.assertEqual(self.doc(did)['iteration'],2);self.assertEqual(len(self.doc(did)['processHistory']),1)
        for user in ['it','finance','legal']:self.action('processDecision',user=user,documentId=did,decision='accept')
        self.assertEqual(self.step(did)['kind'],'approval')
        self.action('processDecision',user='director',documentId=did,decision='accept')
        self.assertEqual(self.step(did)['kind'],'execution')
        self.finish_task(self.step(did)['taskIds'][0],'it','director')
        self.assertEqual(self.step(did)['kind'],'archive')
        self.action('archive',documentId=did)
        self.assertEqual(self.doc(did)['status'],'archived')

    def test_03_type_schema_and_template_snapshots(self):
        alltypes=self.state()['state']['documentTypes'];self.assertEqual(len(alltypes),8)
        values={'name':'Акт проверки','prefix':'АКТ','family':'internal','active':True,'defaultRouteId':'memo-flow','fields':[server.universal.field('object_name','Объект проверки',required=True),server.universal.field('count','Количество','decimal'),server.universal.field('date_of','Дата осмотра','date'),server.universal.field('result','Итог','choice',options=['Принято','Замечания'])]}
        self.action('documentTypeSave',**values,status=403)
        r=self.action('documentTypeSave',user='admin',**values);spec=r['state']['documentTypes'][-1];tid=spec['id']
        self.action('create',payload={'type':tid,'title':'Missing required'},status=400)
        self.action('create',payload={'type':tid,'title':'Invalid choice','customFields':{'object_name':'Office','result':'Other'}},status=400)
        did=self.create(tid,'memo-flow','Осмотр помещения',customFields={'object_name':'Кабинет 4','count':'12,5','date_of':'2026-09-25','result':'Принято'})
        original=self.doc(did);self.assertEqual(original['customFields']['count'],'12.5');self.assertEqual(original['typeSnapshot']['version'],1)
        self.action('documentTypeSave',user='admin',id=tid,**{**values,'name':'Акт обследования','fields':[server.universal.field('new_field','Новое поле',required=True)]})
        self.action('edit',documentId=did,payload={'title':'Прежний акт отредактирован','customFields':original['customFields']})
        self.assertEqual(self.doc(did)['typeSnapshot']['name'],'Акт проверки')
        # Editing the template and later the card cannot silently replace its snapshot.
        route=next(r for r in self.state()['state']['processTemplates'] if r['id']=='memo-flow')
        changed=json.loads(json.dumps(route));changed['steps'][0]['participants']=['finance']
        self.action('processTemplateSave',user='admin',**changed)
        self.action('edit',documentId=did,payload={'title':'Still old route','routeTemplateId':'memo-flow'})
        self.action('processStart',documentId=did);self.assertEqual(self.step(did)['participants'],['it'])
        nextid=self.create(tid,'memo-flow','Новый акт',customFields={'new_field':'Новые данные'})
        self.assertNotEqual(self.doc(nextid)['number'],original['number']);self.action('processStart',documentId=nextid)
        self.assertEqual(self.step(nextid)['participants'],['finance'])
        self.action('documentTypeSave',user='admin',id=tid,**{**values,'name':'Акт обследования','active':False,'fields':[server.universal.field('new_field','Новое поле',required=True)]})
        self.action('create',payload={'type':tid,'title':'Disabled'},status=400)
        self.action('processCancel',documentId=did,comment='Проверка отменена');self.assertEqual(self.doc(did)['status'],'draft')
        self.action('documentTypeSave',user='admin',**{**values,'name':'Дубликат префикса'},status=400)

    def test_04_order_signature_and_familiarization(self):
        did=self.create('order','order-flow','Приказ о назначении ответственных')
        self.action('processStart',documentId=did);self.action('processDecision',user='legal',documentId=did,decision='accept')
        self.assertEqual(self.step(did)['kind'],'signature')
        self.action('processDecision',user='director',documentId=did,decision='accept',status=400)
        payload=(server.ROOT/'templates'/'memo.docx').read_bytes()
        self.upload(did,payload,user='secretary',filename='signed.docx',purpose='process',status=403)
        r=self.upload(did,payload,user='director',filename='signed.docx',purpose='process');fid=r['state']['attachments'][0]['id']
        self.action('processDecision',user='director',documentId=did,decision='accept',fileId=fid)
        self.assertEqual(self.step(did)['kind'],'acknowledge')
        self.action('archive',documentId=did,status=403)
        for user in ['legal','finance','it']:self.action('processDecision',user=user,documentId=did,decision='accept')
        self.assertEqual(self.step(did)['kind'],'archive');self.action('archive',documentId=did)
        self.upload(did,payload,user='director',filename='signed.docx',purpose='process',status=403)

    def test_05_sequential_route_participants_and_cancel(self):
        steps=[{'kind':'review','label':'Проверка по очереди','mode':'sequential','participants':['legal','finance']},{'kind':'archive','label':'Архив','participants':['$author']}]
        r=self.action('processTemplateSave',user='admin',name='Последовательный процесс',active=True,steps=steps);rid=r['state']['processTemplates'][-1]['id']
        did=self.create('internal',rid,'Последовательный документ');self.action('processStart',documentId=did)
        self.action('processDecision',user='finance',documentId=did,decision='accept',status=403)
        self.action('processParticipants',user='legal',documentId=did,stepIndex=0,participants=['legal'],comment='Cannot modify',status=403)
        self.action('processDecision',user='legal',documentId=did,decision='accept')
        self.action('processParticipants',documentId=did,stepIndex=0,participants=['legal','it'],comment='Заменить финансовый отдел на IT')
        self.action('userActive',user='admin',username='it',active=False,status=400)
        self.action('processDecision',user='finance',documentId=did,decision='accept',status=403)
        self.action('processDecision',user='it',documentId=did,decision='accept')
        self.action('processParticipants',documentId=did,stepIndex=0,participants=['legal'],comment='Rewrite completed stage',status=400)
        self.action('processCancel',documentId=did,status=400)
        self.action('processCancel',documentId=did,comment='Начать заново')
        self.assertEqual(self.doc(did)['process']['state'],'cancelled')
        self.action('processStart',documentId=did)
        self.assertEqual(self.doc(did)['iteration'],2);self.assertEqual(self.step(did)['decisions'],{})

    def test_06_default_route_numbering_and_invalid_templates(self):
        # Real creation request without the legacy test helper's explicit manual-route setting.
        rev=self.state()['revision']
        code,raw,_=self.request('/api/sed',{'action':'create','revision':rev,'payload':{'type':'incoming','title':'Автоматический маршрут'}})
        self.assertEqual(code,200,raw);d=json.loads(raw)['state']['documents'][0]
        self.assertEqual(d['routePlan']['id'],'incoming-flow')
        pattern='ВХ-'+server.today()[:4]+'-';self.assertTrue(d['number'].startswith(pattern))
        a=self.create('incoming','incoming-flow','Следующее входящее');self.assertNotEqual(self.doc(a)['number'],d['number'])
        for steps in [[{'kind':'archive','participants':['$author']},{'kind':'review','participants':['it']}],[{'kind':'resolution','participants':['director']}],[{'kind':'review','participants':[]}],[{'kind':'unknown','participants':['it']}]]:
            self.action('processTemplateSave',user='admin',name='Некорректный маршрут',steps=steps,status=400)
        self.action('processStart',documentId=a);self.action('processStart',documentId=a,status=403)
        self.action('processParticipants',documentId=a,stepIndex=1,participants=['finance'],controller='director',comment='Новый исполнитель')
        self.assertEqual(self.doc(a)['process']['steps'][1]['participants'],['finance'])
        with server.connect() as db:revision,state=server.read_state(db)
        server.initialize()
        with server.connect() as db:self.assertEqual(server.read_state(db),(revision,state))

if __name__=='__main__':unittest.main()
