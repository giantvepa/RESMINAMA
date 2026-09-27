"""Configurable document types and ordered workflow instances (schema 4)."""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json,re,sqlite3,time

KINDS={'review':'Согласование','approval':'Утверждение','resolution':'Резолюция','execution':'Исполнение','signature':'Регистрация подписи','acknowledge':'Ознакомление','reply':'Регистрация отправки / ответа','archive':'Архив'}
STATUSES={'review':'review','approval':'approval','resolution':'resolution','execution':'execution','signature':'signing','acknowledge':'reading','reply':'reply','archive':'complete'}
FIELD_KINDS={'text','textarea','date','decimal','choice','boolean','person'}

def field(key,label,kind='text',required=False,options=None):
    return {'key':key,'label':label,'kind':kind,'required':required,'options':options or []}

def defaults():
    def step(kind,participants,**kw):return {'kind':kind,'label':KINDS[kind],'participants':participants,'mode':'parallel','controller':'$author',**kw}
    routes=[
      ('incoming-flow','Входящее: руководитель → резолюция → исполнение → ответ', [step('resolution',['director']),step('execution',['it'],controller='director'),step('reply',['$author']),step('archive',['$author'])]),
      ('contract-flow','Договор: Юрист + Финансы + IT → утверждение → исполнение',[step('review',['legal','finance','it']),step('approval',['director']),step('execution',['it'],controller='director'),step('archive',['$author'])]),
      ('order-flow','Приказ: согласование → подпись → ознакомление',[step('review',['legal']),step('signature',['director']),step('acknowledge',['legal','finance','it']),step('archive',['$author'])]),
      ('memo-flow','Служебная записка: получатель → исполнение',[step('execution',['it'],label='Исполнение получателем'),step('archive',['$author'])]),
      ('outgoing-flow','Исходящее: утверждение → отправка',[step('approval',['director']),step('reply',['$author'],label='Регистрация отправленного письма'),step('archive',['$author'])]),
      ('internal-flow','Внутренний: согласование → утверждение',[step('review',['legal']),step('approval',['director']),step('archive',['$author'])]),
      ('request-flow','Заявка: резолюция → исполнение',[step('resolution',['director']),step('execution',['it'],controller='director'),step('archive',['$author'])]),
      ('protocol-flow','Протокол: утверждение → ознакомление',[step('approval',['director']),step('acknowledge',['finance','it']),step('archive',['$author'])]),
    ]
    templates=[]
    for rid,name,steps in routes:
        for n,s in enumerate(steps):s['id']='step-'+str(n+1)
        templates.append({'id':rid,'name':name,'active':True,'version':1,'steps':steps})
    common=[field('organization','Организация'),field('curator','Куратор','person'),field('basis','Основание','textarea')]
    types=[]
    for tid,name,prefix,family,route in [('incoming','Входящее письмо','ВХ','incoming','incoming-flow'),('outgoing','Исходящее письмо','ИСХ','outgoing','outgoing-flow'),('memo','Служебная записка','СЗ','internal','memo-flow'),('contract','Договор','ДГ','contract','contract-flow'),('order','Приказ','ПР','internal','order-flow'),('request','Заявка','ЗАЯВ','internal','request-flow'),('protocol','Протокол','ПРОТ','internal','protocol-flow'),('internal','Внутренний документ','ВН','internal','internal-flow')]:
        extra=[field('obligations_start','Начало обязательств','date'),field('obligations_end','Окончание обязательств','date'),field('contact','Контактное лицо'),field('vat','НДС','choice',options=['Без НДС','Включён в сумму','Начисляется отдельно'])] if family=='contract' else []
        types.append({'id':tid,'name':name,'prefix':prefix,'family':family,'active':True,'version':1,'defaultRouteId':route,'fields':deepcopy(common)+extra})
    return types,templates

def migrate(h):
    with h.connect() as db:
        if db.execute('PRAGMA user_version').fetchone()[0]>=4:return
        folder=h.DATA/'migration_backups';folder.mkdir(exist_ok=True)
        target=folder/('before_types_routes_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.sqlite3')
        with sqlite3.connect(target) as out:db.backup(out)
        db.execute('BEGIN IMMEDIATE');_,state=h.read_state(db)
        state['documentTypes'],state['processTemplates']=defaults();state['numberCounters']={}
        for d in state['documents']:
            # Existing cards keep their original route and data, even if already running.
            spec=next((t for t in state['documentTypes'] if t['id']==d['type']),None)
            if spec:d.setdefault('typeSnapshot',deepcopy(spec));d.setdefault('customFields',{})
        db.execute('UPDATE workspace SET revision=revision+1,data=? WHERE id=1',(json.dumps(state,ensure_ascii=False),))
        db.execute('PRAGMA user_version=4');db.commit()

def get_type(h,state,tid,active=True):
    value=next((t for t in state['documentTypes'] if t['id']==tid),None)
    if not value or (active and not value['active']):raise h.AppError('Тип документа недоступен')
    return value

def validate_fields(h,fields):
    if not isinstance(fields,list) or len(fields)>30:raise h.AppError('Допускается до 30 дополнительных полей')
    result=[];keys=set()
    for item in fields:
        if not isinstance(item,dict):raise h.AppError('Некорректное поле типа документа')
        key=h.clean(item.get('key'),40);label=h.clean(item.get('label'),120);kind=item.get('kind');required=item.get('required',False)
        if not re.fullmatch('[a-z][a-z0-9_]{0,39}',key) or key in keys or not label or kind not in FIELD_KINDS or type(required) is not bool:raise h.AppError('Проверьте код, название и формат поля; коды должны быть уникальны')
        options=item.get('options',[])
        if not isinstance(options,list) or any(not isinstance(x,str) or not h.clean(x,120) for x in options) or len(options)>50:raise h.AppError('Некорректные варианты поля')
        options=list(dict.fromkeys(h.clean(x,120) for x in options))
        if kind=='choice' and not options:raise h.AppError('Добавьте варианты выбора')
        keys.add(key);result.append(field(key,label,kind,required,options if kind=='choice' else []))
    return result

def custom_fields(h,spec,value,db):
    if not isinstance(value,dict):raise h.AppError('Некорректные дополнительные реквизиты')
    if set(value)-{f['key'] for f in spec['fields']}:raise h.AppError('Неизвестное дополнительное поле')
    result={}
    for f in spec['fields']:
        v=value.get(f['key'],'');kind=f['kind']
        if kind=='boolean':
            if v=='':v=False
            if type(v) is not bool:raise h.AppError('Поле «'+f['label']+'» должно иметь значение да/нет')
        else:
            if not isinstance(v,str):raise h.AppError('Некорректное значение: '+f['label'])
            v=h.clean(v,3000 if kind=='textarea' else 300)
            if v and kind=='date':v=h.valid_date(v)
            if v and kind=='choice' and v not in f['options']:raise h.AppError('Выберите значение: '+f['label'])
            if v and kind=='person':h.validate_people([v],db)
            if v and kind=='decimal':
                try:
                    n=Decimal(v.replace(',','.'))
                    if not n.is_finite():raise InvalidOperation()
                except InvalidOperation:raise h.AppError('Введите число: '+f['label'])
                v=str(n)
        if f['required'] and (v=='' or v is False):raise h.AppError('Заполните обязательное поле: '+f['label'])
        result[f['key']]=v
    if result.get('obligations_start') and result.get('obligations_end') and result['obligations_end']<result['obligations_start']:raise h.AppError('Окончание обязательств раньше начала')
    return result

def validate_steps(h,steps,db,placeholders=True):
    if not isinstance(steps,list) or not 1<=len(steps)<=20:raise h.AppError('Маршрут должен содержать от 1 до 20 этапов')
    result=[]
    for i,s in enumerate(steps):
        if not isinstance(s,dict) or s.get('kind') not in KINDS:raise h.AppError('Неизвестный вид этапа')
        kind=s['kind'];parts=s.get('participants',[])
        if not isinstance(parts,list) or not parts or not all(isinstance(p,str) for p in parts):raise h.AppError('Выберите участников каждого этапа')
        h.validate_people([p for p in parts if p!='$author' or not placeholders],db)
        parts=list(dict.fromkeys(parts))
        if kind in ['approval','resolution','signature','reply'] and len(parts)!=1:raise h.AppError('На этапе «'+KINDS[kind]+'» нужен один ответственный')
        controller=s.get('controller','$author')
        if kind=='execution' and (controller!='$author' or not placeholders):h.validate_people([controller],db)
        if kind=='archive' and i!=len(steps)-1:raise h.AppError('Архив может быть только последним этапом')
        if kind=='resolution' and (i+1==len(steps) or steps[i+1].get('kind')!='execution'):raise h.AppError('После резолюции должен идти этап исполнения')
        result.append({'id':'step-'+str(i+1),'kind':kind,'label':h.clean(s.get('label'),120) or KINDS[kind],'participants':parts,'controller':controller if kind=='execution' else '$author','mode':'sequential' if kind=='review' and s.get('mode')=='sequential' else 'parallel'})
    return result

def route_copy(h,state,rid):
    if not rid:return None
    route=next((r for r in state['processTemplates'] if r['id']==rid and r['active']),None)
    if not route:raise h.AppError('Шаблон маршрута недоступен')
    return deepcopy(route)

def init_card(h,state,p,doc,db):
    spec=get_type(h,state,doc['type']);doc['typeSnapshot']=deepcopy(spec)
    doc['customFields']=custom_fields(h,spec,p.get('customFields',{}),db)
    doc['routePlan']=route_copy(h,state,p.get('routeTemplateId',spec['defaultRouteId']))

def edit_card(h,state,p,doc,db):
    spec=doc.get('typeSnapshot') or get_type(h,state,doc['type'],False)
    if 'customFields' in p:doc['customFields']=custom_fields(h,spec,p['customFields'],db)
    if 'routeTemplateId' in p and p['routeTemplateId']!=(doc.get('routePlan') or {}).get('id',''):
        if doc.get('process'):raise h.AppError('Для этого документа маршрут уже запускался; используйте настройки участников процесса')
        doc['routePlan']=route_copy(h,state,p['routeTemplateId'])

def number(h,state,tid):
    prefix=get_type(h,state,tid)['prefix'];year=h.today()[:4];key=prefix+'/'+year
    n=state['numberCounters'].get(key,0)+1
    existing={d['number'] for d in state['documents']}
    while f'{prefix}-{year}-{n:04}' in existing:n+=1
    state['numberCounters'][key]=n
    return f'{prefix}-{year}-{n:04}'

def current(doc):
    p=doc.get('process')
    return p['steps'][p['current']] if p and p['state']=='running' and p['current']<len(p['steps']) else None

def participants(doc):
    result=set()
    for process in [doc.get('process')]+doc.get('processHistory',[]):
        if process:
            for s in process['steps']:
                result.update(s['participants'])
                if s.get('controller'):result.add(s['controller'])
    return result-{'$author'}

def log(h,state,d,user,actor,action,comment='',before=None,after=None,**extra):
    p=d.get('process') or {};s=current(d)
    event={'id':h.uid(),'documentId':d['id'],'actor':actor,'username':user['username'],'action':action,'comment':h.clean(comment),'kind':'workflow','createdAt':h.now(),'iteration':d.get('iteration',0),'processId':p.get('id'),'step':s['label'] if s else '',**extra}
    if before is not None or after is not None:event['transition']={'before':before,'after':after}
    state['events'].insert(0,event);d['updatedAt']=event['createdAt']

def activate(h,state,d,user,actor):
    p=d['process']
    if p['current']>=len(p['steps']):
        p['state']='completed';p['completedAt']=h.now();d['status']='complete';log(h,state,d,user,actor,'Маршрут завершён');return
    s=current(d);before=d['status'];d['status']=STATUSES[s['kind']];s['status']='active';s['startedAt']=h.now()
    if s['kind']=='execution':
        s['taskIds']=[]
        for assignee in s['participants']:
            tid=h.uid();s['taskIds'].append(tid)
            state['tasks'].insert(0,{'id':tid,'documentId':d['id'],'title':s.get('instruction') or s['label']+': '+d['title'],'author':s.get('assignedBy',actor),'controller':s['controller'],'assignee':assignee,'due':s.get('due') or d.get('due',''),'status':'new','createdAt':h.now(),'updatedAt':h.now(),'reports':[],'requiresAcceptance':True,'processId':p['id'],'processStepId':s['id']})
    log(h,state,d,user,actor,'Начат этап: '+s['label'],before=before,after=d['status'])

def advance(h,state,d,user,actor):
    s=current(d);s['status']='done';s['completedAt']=h.now();d['process']['current']+=1;activate(h,state,d,user,actor)

def after(h,state,b,user,actor):
    d=next((x for x in state['documents'] if x['id']==b.get('documentId')),None)
    if b.get('taskId'):
        t=next((t for t in state['tasks'] if t['id']==b['taskId']),None)
        if t:d=next((x for x in state['documents'] if x['id']==t['documentId']),None)
    if not d:return
    s=current(d)
    if s and s['kind']=='execution':
        tasks=[t for t in state['tasks'] if t['id'] in s.get('taskIds',[])]
        if tasks and all(t['status']=='done' for t in tasks):advance(h,state,d,user,actor)
    elif s and s['kind']=='archive' and d['status']=='archived':
        s['status']='done';s['completedAt']=h.now();d['process']['state']='completed';d['process']['completedAt']=h.now()
        log(h,state,d,user,actor,'Маршрут завершён в архиве',before='complete',after='archived')

def file_stage(doc,actor):
    s=current(doc)
    return s if s and s['kind'] in ['signature','reply'] and actor in s['participants'] else None

def apply(h,state,b,user,db,actor):
    action=b.get('action','')
    if action in ['documentTypeSave','processTemplateSave']:
        if user['role']!='admin':raise h.AppError('Справочники изменяет администратор',403)
        collection='documentTypes' if action=='documentTypeSave' else 'processTemplates'
        item=next((x for x in state[collection] if x['id']==b.get('id')),None)
        if b.get('id') and not item:raise h.AppError('Запись справочника не найдена',404)
        name=h.clean(b.get('name'),160);active=b.get('active',True)
        if not name or type(active) is not bool:raise h.AppError('Укажите название и состояние')
        if any(x['name'].casefold()==name.casefold() and x is not item for x in state[collection]):raise h.AppError('Название уже используется')
        values={'name':name,'active':active,'version':(item or {}).get('version',0)+1,'updatedAt':h.now()}
        if action=='documentTypeSave':
            prefix=h.clean(b.get('prefix'),12).upper();family=b.get('family','internal');rid=b.get('defaultRouteId','')
            if not re.fullmatch(r'[A-ZА-ЯЁ0-9_-]{1,12}',prefix) or family not in ['incoming','outgoing','contract','internal']:raise h.AppError('Проверьте префикс номера и группу документа')
            if any(x['prefix']==prefix and x is not item for x in state[collection]):raise h.AppError('Префикс уже используется другим типом')
            route_copy(h,state,rid)
            values.update(prefix=prefix,family=family,defaultRouteId=rid,fields=validate_fields(h,b.get('fields',[])))
        else:values['steps']=validate_steps(h,b.get('steps'),db)
        before=deepcopy(item)
        if item:item.update(values)
        else:item={'id':h.uid(),**values};state[collection].append(item)
        state['adminAudit'].insert(0,{'id':h.uid(),'actor':user['username'],'action':action,'target':name,'createdAt':h.now(),'before':before,'after':deepcopy(item)})
        return True
    d=next((d for d in state['documents'] if d['id']==b.get('documentId')),None)
    t=next((t for t in state['tasks'] if t['id']==b.get('taskId')),None)
    if t:d=next((d for d in state['documents'] if d['id']==t['documentId']),None)
    if not d:
        if action.startswith('process'):raise h.AppError('Документ не найден',404)
        return False
    if not h.can_view(state,d,user):raise h.AppError('Нет доступа к документу',403)
    if d['status']=='archived' and action not in ['restore','readEvents']:raise h.AppError('Архивный документ доступен только для чтения',403)
    s=current(d);p=d.get('process')
    if action in ['submit','participants','approve','reject','confirm','cancel'] and (p or d.get('routePlan')):raise h.AppError('Используйте действия текущего этапа универсального маршрута',403)
    if action in ['createTask','reading'] and s:raise h.AppError('Задания текущего процесса создаются его этапами',403)
    if action=='archive' and s and s['kind']!='archive':raise h.AppError('Сначала завершите все этапы маршрута',403)
    if action=='taskCancel' and t and t.get('processId') and s:raise h.AppError('Обязательное поручение маршрута можно переназначить; для отмены завершите весь процесс',403)
    if action=='processStart':
        if not h.v3.can_edit(h,d,user,actor) or s:raise h.AppError('Маршрут запускает автор или редактор в черновике/на доработке',403)
        plan=d.get('routePlan')
        if not plan:raise h.AppError('Сначала выберите шаблон маршрута')
        if any(l['documentId']==d['id'] and l['expires']>time.time() for l in state['fileLocks']):raise h.AppError('Завершите редактирование файлов')
        if any(t['documentId']==d['id'] and t['status'] not in h.v3.CLOSED for t in state['tasks']):raise h.AppError('Сначала завершите прежние поручения')
        if any(r['documentId']==d['id'] and set(r['recipients'])-set(r['done']) for r in state['readings']):raise h.AppError('Сначала завершите прежнее ознакомление')
        steps=validate_steps(h,plan['steps'],db)
        for step in steps:
            step['participants']=[d['author'] if x=='$author' else x for x in step['participants']]
            step['controller']=d['author'] if step['controller']=='$author' else step['controller']
            h.validate_people(step['participants']+([step['controller']] if step['kind']=='execution' else []),db)
            step.update(status='waiting',decisions={})
        if p:d.setdefault('processHistory',[]).append(deepcopy(p))
        d['iteration']+=1;d['process']={'id':h.uid(),'templateId':plan['id'],'templateVersion':plan['version'],'name':plan['name'],'state':'running','current':0,'steps':steps,'startedAt':h.now(),'startedBy':actor}
        d['approvers']=[];d['approvals']={};d['finalApprover']=d['author']
        log(h,state,d,user,actor,'Запущен универсальный маршрут',plan['name']);activate(h,state,d,user,actor);return True
    if action=='processCancel':
        if not s or not h.can_manage(d,user,actor):raise h.AppError('Отмена доступна автору или администратору',403)
        reason=h.clean(b.get('comment'))
        if not reason:raise h.AppError('Укажите причину отмены')
        for task in state['tasks']:
            if task.get('processId')==p['id'] and task['status'] not in h.v3.CLOSED:task.update(status='cancelled',cancelReason=reason,updatedAt=h.now())
        before=d['status'];log(h,state,d,user,actor,'Процесс отменён',reason,before=before,after='draft')
        s['status']='cancelled';p['state']='cancelled';d['status']='draft';return True
    if action=='processParticipants':
        if not s or not h.can_manage(d,user,actor):raise h.AppError('Участников изменяет автор или администратор',403)
        i=b.get('stepIndex');reason=h.clean(b.get('comment'))
        if type(i) is not int or i<p['current'] or i>=len(p['steps']) or not reason:raise h.AppError('Выберите текущий/будущий этап и укажите причину')
        target=p['steps'][i]
        if i==p['current'] and target['kind']=='execution':raise h.AppError('Измените исполнителя в карточке поручения')
        parts=h.validate_people(b.get('participants'),db)
        candidate={**target,'participants':parts,'controller':b.get('controller',target['controller'])}
        # Validate in its original position to retain resolution/execution adjacency.
        candidates=deepcopy(p['steps']);candidates[i]=candidate;valid=validate_steps(h,candidates,db,False)[i]
        old=list(target['participants']);target.update(participants=valid['participants'],controller=valid['controller'])
        if target['controller']=='$author':target['controller']=d['author']
        target['decisions']={k:v for k,v in target['decisions'].items() if k in parts}
        d['routePlan']['steps'][i].update(participants=parts,controller=target['controller'])
        log(h,state,d,user,actor,'Изменены участники этапа',reason,before=', '.join(old),after=', '.join(parts))
        if i==p['current'] and target['kind'] in ['review','approval','acknowledge'] and all(x in target['decisions'] for x in parts):advance(h,state,d,user,actor)
        return True
    if not action.startswith('process'):return False
    if not s or actor not in s['participants']:raise h.AppError('Нет активного этапа для этого сотрудника',403)
    comment=h.clean(b.get('comment'));kind=s['kind']
    if action=='processResolve':
        if kind!='resolution':raise h.AppError('Сейчас не этап резолюции',403)
        if not comment:raise h.AppError('Введите текст резолюции')
        assignees=h.validate_people(b.get('assignees'),db);controller=h.validate_people([b.get('controller') or actor],db)[0];due=h.valid_date(b.get('due'))
        if not assignees or not due:raise h.AppError('Выберите исполнителей и контрольный срок')
        target=p['steps'][p['current']+1];target.update(participants=assignees,controller=controller,due=due,instruction=comment,assignedBy=actor)
        d.setdefault('resolutions',[]).append({'actor':actor,'text':comment,'assignees':assignees,'controller':controller,'due':due,'iteration':d['iteration'],'createdAt':h.now()})
        log(h,state,d,user,actor,'Наложена резолюция',comment,decision='Назначено исполнение');advance(h,state,d,user,actor);return True
    if action=='processDecision':
        if kind not in ['review','approval','signature','acknowledge']:raise h.AppError('Для этого этапа требуется другое действие',403)
        if actor in s['decisions']:raise h.AppError('Решение уже принято')
        if s['mode']=='sequential' and next(x for x in s['participants'] if x not in s['decisions'])!=actor:raise h.AppError('Ожидается предыдущий согласующий',403)
        decision=b.get('decision','accept')
        if decision not in ['accept','reject'] or (kind=='acknowledge' and decision!='accept'):raise h.AppError('Недопустимое решение')
        if decision=='reject':
            if not comment:raise h.AppError('Укажите причину возврата')
            s['decisions'][actor]={'decision':'reject','comment':comment,'createdAt':h.now()}
            log(h,state,d,user,actor,'Возвращён на доработку',comment,before=d['status'],after='revision',decision='Возврат')
            s['status']='rejected';p['state']='revision';d['status']='revision';return True
        file=None
        if kind=='signature':
            file=next((f for f in state['attachments'] if f['id']==b.get('fileId') and f['documentId']==d['id'] and f.get('processId')==p['id'] and f.get('processStepId')==s['id'] and f['author']==actor),None)
            if not file:raise h.AppError('Загрузите подписанный PDF/DOCX на текущем этапе')
        s['decisions'][actor]={'decision':'accept','comment':comment,'createdAt':h.now(),'fileId':file['id'] if file else None}
        log(h,state,d,user,actor,{'review':'Согласовал документ','approval':'Утвердил документ','signature':'Зарегистрировал подписанный файл','acknowledge':'Ознакомился с документом'}[kind],comment,decision='Принято',fileId=file['id'] if file else None,fileVersion=file['version'] if file else None)
        if all(x in s['decisions'] for x in s['participants']):advance(h,state,d,user,actor)
        return True
    if action=='processReply':
        if kind!='reply':raise h.AppError('Сейчас не этап ответа/отправки',403)
        file=next((f for f in state['attachments'] if f['id']==b.get('fileId') and f['documentId']==d['id'] and f.get('processId')==p['id'] and f.get('processStepId')==s['id']),None)
        channel=h.clean(b.get('channel'),100);sent=h.valid_date(b.get('sentDate'));recipient=h.clean(b.get('recipient'),200)
        if not file or not comment or not channel or not sent or not recipient:raise h.AppError('Укажите содержание ответа, получателя, дату, способ отправки и приложите отправленный файл')
        dispatch={'date':sent,'channel':channel,'recipient':recipient,'comment':comment,'fileId':file['id'],'registeredBy':actor,'registeredAt':h.now()}
        s['dispatch']=dispatch
        family=d.get('typeSnapshot',{}).get('family',d['type'])
        if family!='outgoing':
            h.mutate(state,{'action':'create','actor':actor,'payload':{'type':'outgoing','title':'Ответ на '+d['number']+': '+d['title'],'description':comment,'correspondent':recipient,'relatedIds':[d['id']],'routeTemplateId':'','customFields':b.get('customFields',{})}},user,db)
            reply=state['documents'][0];reply['status']='complete';reply['dispatch']=dispatch;reply['access']=sorted(h.automatic_access(state,d));reply['replyToId']=d['id']
            copy={**file,'id':h.uid(),'documentId':reply['id'],'groupId':h.uid(),'version':1,'copiedFrom':file['id']};state['attachments'].insert(0,copy)
            reply['dispatch']={**dispatch,'fileId':copy['id']};d['relatedIds']=list(dict.fromkeys(d.get('relatedIds',[])+[reply['id']]));s['replyDocumentId']=reply['id'];d['replyDocumentId']=reply['id']
            log(h,state,reply,user,actor,'Зарегистрирован отправленный ответ',comment,fileId=copy['id'],fileVersion=1)
        else:d['dispatch']=dispatch
        log(h,state,d,user,actor,'Зарегистрирована отправка ответа' if family!='outgoing' else 'Зарегистрирована отправка письма',comment,decision=channel,fileId=file['id'],fileVersion=file['version'])
        advance(h,state,d,user,actor);return True
    raise h.AppError('Неизвестное действие процесса')
