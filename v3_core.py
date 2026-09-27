"""Document cards, controlled execution, records and editing leases for RESMINAMA 0.3."""
import json,sqlite3,time
from datetime import datetime
CLOSED={'done','cancelled'}
CARD_FIELDS={'externalNumber':100,'category':100,'tags':300,'location':200}

def migrate(h):
    with h.connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='workspace'").fetchone():return
        if db.execute('PRAGMA user_version').fetchone()[0]>=3:return
        directory=h.DATA/'migration_backups';directory.mkdir(exist_ok=True)
        target=directory/('before_v0.3_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.sqlite3')
        with sqlite3.connect(target) as out:db.backup(out)
        db.execute('BEGIN IMMEDIATE');_,state=h.read_state(db)
        state.setdefault('cases',[{'id':'general','index':'01-01','title':'Общие документы','years':5,'closed':False}])
        state.setdefault('fileLocks',[])
        for d in state['documents']:
            for key in CARD_FIELDS:d.setdefault(key,'')
            d.setdefault('externalDate','');d.setdefault('registeredAt',d['date']);d.setdefault('priority','normal')
            d.setdefault('editors',[]);d.setdefault('relatedIds',[]);d.setdefault('caseId','general')
            d.setdefault('archive',None)
            if d['status']=='archived' and not d['archive']:
                d['archive']={'date':d['updatedAt'][:10],'caseId':'general','caseIndex':'01-01','caseTitle':'Общие документы','years':None,'reviewAfter':'','previousStatus':'complete','legacy':True,'location':'','actor':d['author']}
        for t in state['tasks']:
            d=next((d for d in state['documents'] if d['id']==t['documentId']),{})
            t.setdefault('author',d.get('author',t['assignee']));t.setdefault('controller',t['author'])
            t.setdefault('reports',[]);t.setdefault('requiresAcceptance',False)
        db.execute('UPDATE workspace SET revision=revision+1,data=? WHERE id=1',(json.dumps(state,ensure_ascii=False),))
        db.execute('PRAGMA user_version=3');db.commit()

def can_edit(h,doc,user,actor):
    return doc['status'] in ['draft','revision'] and (h.can_manage(doc,user,actor) or actor in doc.get('editors',[]))

def can_assign(h,doc,user,actor):
    return doc['status']!='archived' and (h.can_manage(doc,user,actor) or actor in doc.get('editors',[]) or (doc.get('iteration',0)>0 and actor==doc['finalApprover']))

def card_fields(h,state,p,doc,user,db):
    for key,limit in CARD_FIELDS.items():
        if key in p:doc[key]=h.clean(p[key],limit)
        else:doc.setdefault(key,'')
    for key in ['externalDate','registeredAt']:
        doc[key]=h.valid_date(p.get(key,doc.get(key,''))) or (h.today() if key=='registeredAt' else '')
    priority=p.get('priority',doc.get('priority','normal'))
    if priority not in ['normal','high','urgent']:raise h.AppError('Укажите допустимый приоритет')
    doc['priority']=priority
    caseid=p.get('caseId',doc.get('caseId','general'))
    case=next((c for c in state['cases'] if c['id']==caseid and not c['closed']),None)
    if not case:raise h.AppError('Выберите открытое дело')
    doc['caseId']=caseid
    links=p.get('relatedIds',doc.get('relatedIds',[]))
    if not isinstance(links,list) or not all(isinstance(x,str) for x in links):raise h.AppError('Некорректные связи документов')
    if 'relatedIds' in p:
        for id in links:
            target=next((d for d in state['documents'] if d['id']==id),None)
            if id==doc['id'] or not h.can_view(state,target,user):raise h.AppError('Связанный документ недоступен',403)
    hidden=[id for id in doc.get('relatedIds',[]) if not h.can_view(state,next((d for d in state['documents'] if d['id']==id),None),user)]
    doc['relatedIds']=list(dict.fromkeys(links+hidden));doc.setdefault('editors',[]);doc.setdefault('archive',None)

def enrich(h,state,user):
    visible={d['id'] for d in state['documents']}
    for d in state['documents']:
        d['relatedIds']=[id for id in d.get('relatedIds',[]) if id in visible]
    state['fileLocks']=[l for l in state.get('fileLocks',[]) if l['expires']>time.time() and l['documentId'] in visible]
    state['docxTemplates']=[{'id':'memo','name':'Служебная записка'},{'id':'letter','name':'Деловое письмо'}]

def live_lock(state,groupid):
    return next((l for l in state.get('fileLocks',[]) if l['groupId']==groupid and l['expires']>time.time()),None)

def apply(h,state,b,user,db,actor):
    action=b.get('action');stamp=h.now()
    d=next((d for d in state['documents'] if d['id']==b.get('documentId')),None)
    task=next((t for t in state['tasks'] if t['id']==b.get('taskId')),None)
    if task:d=next((d for d in state['documents'] if d['id']==task['documentId']),None)
    reading=next((r for r in state['readings'] if r['id']==b.get('readingId')),None)
    if reading:d=next((d for d in state['documents'] if d['id']==reading['documentId']),None)
    if d and not h.can_view(state,d,user):raise h.AppError('Нет доступа к документу',403)
    if d and d['status']=='archived' and action not in ['access','restore','readEvents']:
        raise h.AppError('Архивный документ доступен только для чтения. Сначала оформите возврат из архива.',403)
    def log(label,comment='',kind='workflow'):
        state['events'].insert(0,{'id':h.uid(),'documentId':d['id'] if d else '', 'actor':actor,'username':user['username'],'action':label,'comment':h.clean(comment),'kind':kind,'createdAt':stamp,'audience':list({actor,task.get('author',''),task.get('controller',''),task['assignee']}) if task else [actor]})
        if d:d['updatedAt']=stamp
    def require_doc():
        if not d:raise h.AppError('Документ не найден',404)
    def require_controller():
        if not task or not (user['role']=='admin' or actor in [task.get('author'),task.get('controller')]):raise h.AppError('Действие доступно инициатору или контролёру поручения',403)
    def reason():
        comment=h.clean(b.get('comment'))
        if not comment:raise h.AppError('Укажите причину')
        return comment
    def sync_tasks():
        if d and not h.universal.current(d) and d['status']=='execution' and all(t['status'] in CLOSED for t in state['tasks'] if t['documentId']==d['id']):d['status']='complete'
    if action=='caseSave':
        if user['role'] not in ['admin','archivist']:raise h.AppError('Делами управляет архивариус или администратор',403)
        case=next((c for c in state['cases'] if c['id']==b.get('caseId')),None)
        if b.get('caseId') and not case:raise h.AppError('Дело не найдено',404)
        index=h.clean(b.get('index'),50);title=h.clean(b.get('title'),150);years=b.get('years');closed=b.get('closed',False)
        if not index or not title or type(years) is not int or years<0 or years>100 or type(closed) is not bool:raise h.AppError('Укажите индекс, название и срок от 0 до 100 лет; 0 — постоянно')
        if any(c['index'].casefold()==index.casefold() and c is not case for c in state['cases']):raise h.AppError('Индекс дела уже занят')
        if closed and any(x.get('caseId')==(case or {}).get('id') and x['status']!='archived' for x in state['documents']):raise h.AppError('В деле есть неархивные документы; сначала завершите их')
        value={'index':index,'title':title,'years':years,'closed':closed,'updatedAt':stamp}
        if case:case.update(value)
        else:state['cases'].append({'id':h.uid(),**value})
        state['adminAudit'].insert(0,{'id':h.uid(),'actor':user['username'],'action':'caseSave','target':index,'createdAt':stamp})
    elif action=='archive':
        require_doc()
        if d['status'] not in ['approved','complete'] or not (user['role'] in ['admin','archivist'] or actor in [d['author'],d['finalApprover']] or (h.universal.current(d) and h.universal.current(d)['kind']=='archive' and actor in h.universal.current(d)['participants'])):raise h.AppError('Документ не готов к архивированию или недостаточно прав',403)
        if any(t['documentId']==d['id'] and t['status'] not in CLOSED for t in state['tasks']):raise h.AppError('Сначала завершите и примите все поручения')
        if any(r['documentId']==d['id'] and set(r['recipients'])-set(r['done']) for r in state['readings']):raise h.AppError('Сначала завершите ознакомление')
        case=next((c for c in state['cases'] if c['id']==b.get('caseId',d.get('caseId')) and not c['closed']),None)
        if not case:raise h.AppError('Выберите открытое дело')
        if any(l['documentId']==d['id'] and l['expires']>time.time() for l in state['fileLocks']):raise h.AppError('Сначала завершите редактирование файлов')
        years=case['years'];date=h.today();year=int(date[:4])+1+years
        d['archive']={'date':date,'caseId':case['id'],'caseIndex':case['index'],'caseTitle':case['title'],'years':years,'reviewAfter':f'{year}-01-01' if years else '', 'previousStatus':d['status'],'location':h.clean(b.get('location'),200),'actor':actor}
        d['caseId']=case['id'];d['status']='archived';log('Передан в архив',f"Дело {case['index']}: {case['title']}")
    elif action=='restore':
        require_doc()
        if user['role'] not in ['admin','archivist'] or d['status']!='archived':raise h.AppError('Вернуть документ может архивариус или администратор',403)
        comment=reason();d['status']=(d.get('archive') or {}).get('previousStatus','complete');d['lastArchive']=d.get('archive');d['archive']=None;log('Возвращён из архива',comment)
    elif action=='reopen':
        require_doc()
        if d['status'] not in ['approved','complete'] or not h.can_manage(d,user,actor):raise h.AppError('Доработку завершённого документа открывает автор или администратор',403)
        if any(t['documentId']==d['id'] and t['status'] not in CLOSED for t in state['tasks']):raise h.AppError('Сначала завершите поручения')
        if any(r['documentId']==d['id'] and set(r['recipients'])-set(r['done']) for r in state['readings']):raise h.AppError('Сначала завершите ознакомление')
        comment=reason();d['status']='revision';d['approvals']={};log('Открыта доработка завершённого документа',comment)
    elif action=='createTask':
        if b.get('documentId'):require_doc()
        if d and not can_assign(h,d,user,actor):raise h.AppError('Нет права назначать поручения',403)
        title=h.clean(b.get('title'),200)
        if not title:raise h.AppError('Укажите поручение')
        assignees=h.validate_people(b.get('assignees',[b.get('assignee')]),db)
        if not assignees:raise h.AppError('Выберите исполнителей')
        controller=h.validate_people([b.get('controller') or actor],db)[0];due=h.valid_date(b.get('due'));batch=h.uid()
        for assignee in assignees:
            task={'id':h.uid(),'documentId':d['id'] if d else '', 'title':title,'author':actor,'controller':controller,'assignee':assignee,'due':due,'status':'new','createdAt':stamp,'updatedAt':stamp,'batchId':batch,'reports':[],'requiresAcceptance':True}
            state['tasks'].insert(0,task);log('Назначено поручение',title+' → '+assignee)
        if d and d['status'] in ['approved','complete']:d['status']='execution'
    elif action=='taskStatus':
        if not task or task['assignee']!=actor:raise h.AppError('Поручение доступно исполнителю',403)
        target=b.get('status')
        if task['status']=='new' and target=='progress':task['status']='progress';log('Поручение взято в работу',task['title'])
        elif task['status']=='progress' and target in ['done','verification']:
            report=h.clean(b.get('report'),3000)
            if task.get('requiresAcceptance') and not report:raise h.AppError('Заполните отчёт об исполнении')
            task['reports'].append({'id':h.uid(),'text':report,'actor':actor,'createdAt':stamp})
            task['status']='verification' if task.get('requiresAcceptance') else 'done';log('Отчёт передан на приёмку' if task['status']=='verification' else 'Поручение исполнено',report or task['title']);sync_tasks()
        else:raise h.AppError('Недопустимый переход статуса поручения')
        task['updatedAt']=stamp
    elif action in ['taskAccept','taskReturn','taskCancel','taskEdit']:
        require_controller()
        if task['status'] in CLOSED:raise h.AppError('Поручение уже закрыто')
        if action in ['taskAccept','taskReturn']:
            if task['status']!='verification':raise h.AppError('Поручение ещё не передано на приёмку')
            comment=h.clean(b.get('comment')) if action=='taskAccept' else reason()
            task['status']='done' if action=='taskAccept' else 'progress'
            task['reports'].append({'id':h.uid(),'text':comment or 'Отчёт принят','actor':actor,'createdAt':stamp,'decision':action})
            log('Исполнение принято' if action=='taskAccept' else 'Отчёт возвращён на доработку',comment or task['title']);sync_tasks()
        elif action=='taskCancel':
            comment=reason();task['status']='cancelled';task['cancelReason']=comment;log('Поручение отменено',comment);sync_tasks()
        else:
            comment=reason();assignee=h.validate_people([b.get('assignee') or task['assignee']],db)[0];controller=h.validate_people([b.get('controller') or task['controller']],db)[0]
            due=h.valid_date(b.get('due'));old=task['assignee'];task.update(assignee=assignee,controller=controller,due=due)
            if old!=assignee:task['status']='new'
            log('Изменено поручение',f"{old} → {assignee}; срок {due or 'не задан'}. {comment}")
        task['updatedAt']=stamp
    elif action in ['fileCheckout','fileRelease']:
        require_doc()
        file=next((f for f in state['attachments'] if f['id']==b.get('fileId') and f['documentId']==d['id']),None)
        if not file:raise h.AppError('Файл не найден',404)
        if not can_edit(h,d,user,actor):raise h.AppError('Нет права редактировать вложения',403)
        lock=live_lock(state,file['groupId'])
        if action=='fileCheckout':
            if lock and lock['username']!=user['username']:raise h.AppError('Файл уже редактирует другой сотрудник',409)
            latest=max(f['version'] for f in state['attachments'] if f['groupId']==file['groupId'])
            if file['version']!=latest:raise h.AppError('Выберите последнюю версию файла',409)
            if lock:state['fileLocks'].remove(lock)
            state['fileLocks'].append({'groupId':file['groupId'],'documentId':d['id'],'actor':actor,'username':user['username'],'baseVersion':latest,'expires':time.time()+86400})
            log('Файл взят на редактирование',file['name'],'file')
        else:
            if not lock:raise h.AppError('Файл не заблокирован')
            if lock['username']!=user['username']:
                if user['role']!='admin':raise h.AppError('Блокировку снимает её владелец или администратор',403)
                reason()
            state['fileLocks'].remove(lock);log('Блокировка файла снята',file['name']+' '+h.clean(b.get('comment')),'file')
    else:return False
    return True
