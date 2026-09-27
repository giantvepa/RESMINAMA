#!/usr/bin/env python3
"""RESMINAMA SED 0.3.0: local Python application, standard library only."""
from __future__ import annotations
import argparse, base64, hashlib, hmac, html, ipaddress, json, mimetypes, os
import v3_core as v3
import docx_ops
import universal
import crypto_sign
import re, secrets, shutil, socket, sqlite3, sys, threading, time, uuid, webbrowser, zipfile
from datetime import datetime, timedelta, timezone
from email.parser import BytesParser
from email.policy import default as email_policy
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
PEOPLE = ['secretary', 'legal', 'finance', 'it', 'director']
NAMES = {'secretary':'Айна Мамедова','legal':'Мерет Оразов','finance':'Лейли Атаева','it':'Сердар Аннаев','director':'Довлет Керимов','admin':'Администратор'}
DEPTS = dict(zip(PEOPLE,['Канцелярия','Юридический отдел','Финансовый отдел','Отдел IT','Руководство']))
TYPES = {'incoming':'ВХ','outgoing':'ИСХ','internal':'ВН','contract':'ДГ'}
MAX_UPLOAD = 20 * 1024 * 1024
STATE_LOCK = threading.RLock()
FAILURES: dict[str, list[float]] = {}

class AppError(Exception):
    def __init__(self, message, status=400):
        self.status=status
        super().__init__(message)

def now(): return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def crypto_sign_test_pdf(text='RESMINAMA test file'):
    """Минимальный одностраничный PDF (stdlib) для демонстрации контура ЭЦП."""
    safe=text.replace('\\',r'\\').replace('(',r'\(').replace(')',r'\)')[:120]
    stream=f'BT /F1 14 Tf 60 720 Td ({safe}) Tj ET'.encode()
    objects=[b'%PDF-1.4\n',
        b'1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n',
        b'2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n',
        b'3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>\nendobj\n',
        b'4 0 obj\n<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream\nendobj\n',
        b'5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n']
    out=bytearray();xref=b'xref\n0 6\n0000000000 65535 f \n'
    offsets=[]
    body=b''
    # нумерация смещений: первый объект начинается после заголовка %PDF-1.4\n
    header=objects[0];pos=len(header)
    for i,obj in enumerate(objects[1:],start=1):
        offsets.append(pos);pos+=len(obj)
    trailer_len=pos
    for line in [b'0000000000 65535 f \n']+[f'{o:010d} 00000 n \n'.encode() for o in offsets]:
        xref+=line
    out+=header
    for obj in objects[1:]:out+=obj
    out+=xref
    out+=b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n'+str(trailer_len).encode()+b'\n%%EOF\n'
    return bytes(out)
def uid(): return str(uuid.uuid4())
def today(): return datetime.now().date().isoformat()
def clean(value, limit=3000): return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', value).strip()[:limit] if isinstance(value,str) else ''
def valid_date(value):
    value=clean(value,10)
    if value:
        try:
            if datetime.strptime(value,'%Y-%m-%d').strftime('%Y-%m-%d') != value: raise ValueError()
        except ValueError: raise AppError('Укажите корректную дату')
    return value

def password_hash(password, salt=None):
    salt=salt or secrets.token_hex(16)
    digest=hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),bytes.fromhex(salt),600_000)
    return salt+'$'+digest.hex()

def check_password(password, encoded):
    try: return hmac.compare_digest(password_hash(password,encoded.split('$')[0]),encoded)
    except (ValueError,TypeError): return False

def connect():
    db=sqlite3.connect(DATA/'resminama.sqlite3',timeout=20)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA busy_timeout=20000')
    return db

def initial_state(empty=False):
    state=json.loads((ROOT/'seed.json').read_text(encoding='utf-8'))
    if empty:
        return {k:[] for k in state}
    anchor=datetime.fromisoformat(state['documents'][0]['date']).date()+timedelta(days=1)
    delta=datetime.now().date()-anchor
    def shift(item):
        if isinstance(item,dict): return {k:shift(v) for k,v in item.items()}
        if isinstance(item,list): return [shift(v) for v in item]
        if isinstance(item,str) and len(item)>=10 and item[4:5]=='-' and item[7:8]=='-':
            try: return (datetime.fromisoformat(item[:10]).date()+delta).isoformat()+item[10:]
            except ValueError: pass
        return item
    return shift(state)

def initialize(empty=False):
    DATA.mkdir(parents=True,exist_ok=True)
    (DATA/'uploads').mkdir(exist_ok=True)
    credentials=[]
    migrate_existing()
    with connect() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, role TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS workspace(id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, data TEXT NOT NULL);
        ''')
        if not db.execute('SELECT 1 FROM workspace').fetchone():
            seed=initial_state(empty);seed.setdefault('signatures',[]);seed.setdefault('adminAudit',[])
            db.execute('INSERT INTO workspace VALUES (1,0,?)',(json.dumps(seed,ensure_ascii=False),))
        if not db.execute('SELECT 1 FROM users').fetchone():
            for login in ['admin']+PEOPLE:
                password=secrets.token_urlsafe(11)
                db.execute('INSERT INTO users(username,password_hash,role) VALUES (?,?,?)',(login,password_hash(password),login))
                credentials.append((login,password))
        db.execute('DELETE FROM sessions WHERE expires < ?',(time.time(),))
    migrate_existing()
    v3.migrate(sys.modules[__name__])
    universal.migrate(sys.modules[__name__])
    if credentials:
        content='RESMINAMA — первоначальные учётные записи\n\n'+ '\n'.join(f'{login:12} {password}    {NAMES[login]}' for login,password in credentials)
        content+='\n\nПароли созданы случайно при первом запуске. Смените их в меню профиля.\nПосле передачи паролей сотрудникам удалите этот файл.\n'
        (DATA/'ACCOUNTS.txt').write_text(content,encoding='utf-8')
        try: os.chmod(DATA/'ACCOUNTS.txt',0o600)
        except OSError: pass
        print('\nInitial credentials are in data/ACCOUNTS.txt')
        print('Administrator login: admin\nAdministrator password:',credentials[0][1])

def read_state(db):
    row=db.execute('SELECT revision,data FROM workspace WHERE id=1').fetchone()
    return row['revision'],json.loads(row['data'])

def migrate_existing():
    path=DATA/'resminama.sqlite3'
    if not path.exists(): return
    with connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='users'").fetchone(): return
        version=db.execute('PRAGMA user_version').fetchone()[0]
        if version>4:raise AppError('База создана более новой версией RESMINAMA. Используйте подходящую версию программы.')
        if version>=2:return
        # SQLite backup API includes committed WAL data; uploads remain untouched.
        directory=DATA/'migration_backups';directory.mkdir(exist_ok=True)
        target=directory/('before_v0.2_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.sqlite3')
        with sqlite3.connect(target) as out: db.backup(out)
        db.execute('BEGIN IMMEDIATE')
        columns={r['name'] for r in db.execute('PRAGMA table_info(users)')}
        for name,definition in [('name',"TEXT NOT NULL DEFAULT ''"),('department',"TEXT NOT NULL DEFAULT ''"),('title',"TEXT NOT NULL DEFAULT ''"),('active','INTEGER NOT NULL DEFAULT 1')]:
            if name not in columns: db.execute(f'ALTER TABLE users ADD COLUMN {name} {definition}')
        for row in db.execute('SELECT username,role FROM users').fetchall():
            login=row['username'];admin=row['role']=='admin'
            db.execute('UPDATE users SET name=?,department=?,title=?,role=? WHERE username=?',
                (NAMES.get(login,login),DEPTS.get(login,'Администрация'),'Администратор' if admin else 'Сотрудник','admin' if admin else 'employee',login))
        revision,state=read_state(db)
        for d in state['documents']:
            d.setdefault('isPublic',True)  # v0.1 documents were visible to everyone.
            d.setdefault('access',[])
        for task in state['tasks']:
            if not task['documentId']:task.setdefault('legacyPublic',True)
        state.setdefault('templates',[]);state.setdefault('adminAudit',[])
        db.execute('UPDATE workspace SET revision=revision+1,data=? WHERE id=1',(json.dumps(state,ensure_ascii=False),))
        db.execute('PRAGMA user_version=2')
        db.commit()

def directory(db):
    colors=['blue','purple','amber','teal','navy']
    return [{'id':r['username'],'name':r['name'],'department':r['department'],'role':r['title'],
             'systemRole':r['role'],'active':bool(r['active']),
             'initials':''.join(w[0] for w in r['name'].split()[:2]).upper() or '?','color':colors[i%len(colors)]}
            for i,r in enumerate(db.execute('SELECT * FROM users ORDER BY username'))]

def automatic_access(state,doc):
    result={doc['author'],*doc.get('editors',[]),*universal.participants(doc)}
    if doc.get('iteration',0): result.update(doc['approvers']);result.add(doc['finalApprover'])
    result.update(t['assignee'] for t in state['tasks'] if t['documentId']==doc['id'])
    result.update(t.get('author',t['assignee']) for t in state['tasks'] if t['documentId']==doc['id'])
    result.update(t.get('controller',t.get('author',t['assignee'])) for t in state['tasks'] if t['documentId']==doc['id'])
    for r in state['readings']:
        if r['documentId']==doc['id']:result.update(r['recipients']);result.add(r['author'])
    return result

def can_view(state,doc,user):
    return bool(doc) and (user['role']=='admin' or (user['role']=='archivist' and doc['status']=='archived') or doc.get('isPublic',False) or user['username'] in set(doc.get('access',[]))|automatic_access(state,doc))

def can_manage(doc,user,actor):
    return user['role']=='admin' or doc['author']==actor

def response_state(db,user):
    rev,state=read_state(db)
    if user['role']!='admin':
        visible={d['id'] for d in state['documents'] if can_view(state,d,user)}
        state['documents']=[d for d in state['documents'] if d['id'] in visible]
        state['signatures']=[s for s in state.get('signatures',[]) if s.get('owner')==user['username']]
        for key in ['attachments','readings']:
            state[key]=[v for v in state[key] if v['documentId'] in visible]
        state['tasks']=[t for t in state['tasks'] if t['documentId'] in visible or (not t['documentId'] and (user['username'] in [t['assignee'],t.get('author'),t.get('controller')] or t.get('legacyPublic',False)))]
        state['events']=[e for e in state['events'] if e['documentId'] in visible or (not e['documentId'] and (e.get('username')==user['username'] or user['username'] in e.get('audience',[])))]
        state.pop('adminAudit',None)
    for d in state['documents']:d['automaticAccess']=sorted(automatic_access(state,d))
    for f in state['attachments']:f.pop('key',None)
    v3.enrich(sys.modules[__name__],state,user)
    state.setdefault('signatures',[])
    state['people']=directory(db)
    return {'revision':rev,'state':state,'session':{'username':user['username'],'actor':user['username'],'canSwitch':user['role']=='admin','systemRole':user['role']}}

def effective_actor(user,body,db):
    actor=body.get('actor') or user['username']
    if user['role']!='admin' and actor!=user['username']:raise AppError('Нельзя выполнять действия от имени другого сотрудника',403)
    if not db.execute('SELECT 1 FROM users WHERE username=? AND active=1',(actor,)).fetchone():raise AppError('Выберите активного сотрудника')
    return actor

def validate_people(value,db,allow_inactive=False):
    if not isinstance(value,list) or not all(isinstance(p,str) for p in value):raise AppError('Некорректный список участников')
    known={r['username'] for r in db.execute('SELECT username FROM users'+('' if allow_inactive else ' WHERE active=1'))}
    if any(p not in known for p in value):raise AppError('В списке есть неизвестный или отключённый сотрудник')
    return list(dict.fromkeys(value))

def administer(state,b,user,db):
    if user['role']!='admin':raise AppError('Управление пользователями доступно администратору',403)
    action=b['action'];login=clean(b.get('username'),80);receipt=None
    row=db.execute('SELECT * FROM users WHERE username=?',(login,)).fetchone()
    if action=='userCreate':
        if not re.fullmatch(r'[a-z][a-z0-9_.-]{2,39}',login):raise AppError('Логин: 3–40 латинских букв, цифр, точек, дефисов или подчёркиваний; первая — буква')
        if row:raise AppError('Этот логин уже занят')
    elif not row:raise AppError('Пользователь не найден',404)
    if action in ['userCreate','userEdit']:
        name=clean(b.get('name'),120);department=clean(b.get('department'),100);title=clean(b.get('title'),100);role=b.get('role')
        if not name or role not in ['admin','employee','archivist']:raise AppError('Укажите имя и допустимую роль')
        if action=='userEdit':
            if login==user['username'] and role!='admin':raise AppError('Нельзя снять собственные права администратора')
            db.execute('UPDATE users SET name=?,department=?,title=?,role=? WHERE username=?',(name,department,title,role,login))
            if row['role']!=role:db.execute('DELETE FROM sessions WHERE username=?',(login,))
        else:
            password=secrets.token_urlsafe(12)
            db.execute('INSERT INTO users(username,password_hash,role,name,department,title,active) VALUES (?,?,?,?,?,?,1)',(login,password_hash(password),role,name,department,title))
            receipt={'username':login,'password':password}
    elif action=='userReset':
        password=secrets.token_urlsafe(12)
        db.execute('UPDATE users SET password_hash=? WHERE username=?',(password_hash(password),login))
        db.execute('DELETE FROM sessions WHERE username=?',(login,));receipt={'username':login,'password':password}
    elif action=='userActive':
        active=b.get('active')
        if not isinstance(active,bool):raise AppError('Укажите состояние пользователя')
        if not active:
            if login==user['username']:raise AppError('Нельзя отключить собственную учётную запись')
            if any(universal.current(d) and login in universal.participants(d) for d in state['documents']):raise AppError('Сотрудник участвует в действующем универсальном маршруте. Сначала замените его или завершите процесс.')
            if any(l['username']==login and l['expires']>time.time() for l in state.get('fileLocks',[])):raise AppError('Сначала завершите редактирование файлов сотрудника или снимите блокировки')
            if any((d['status']=='review' and login in [p for p in d['approvers'] if d['approvals'].get(p)!='approved']) or (d['status'] in ['review','approval'] and d['finalApprover']==login) or (d['status'] in ['draft','revision','review','approval','approved','execution','complete'] and d['author']==login) for d in state['documents']):raise AppError('У сотрудника есть незавершённые документы или согласования. Сначала завершите их или измените маршрут.')
            if any(login in [t['assignee'],t.get('controller')] and t['status'] not in v3.CLOSED for t in state['tasks']) or any(login in set(r['recipients'])-set(r['done']) for r in state['readings']):raise AppError('Сначала завершите поручения и ознакомления сотрудника')
        db.execute('UPDATE users SET active=? WHERE username=?',(int(active),login))
        db.execute('DELETE FROM sessions WHERE username=?',(login,))
    else:raise AppError('Неизвестное действие пользователя')
    state.setdefault('adminAudit',[]).insert(0,{'id':uid(),'actor':user['username'],'action':action,'target':login,'createdAt':now()})
    return receipt

def pending(doc,actor):
    unanswered=[p for p in doc['approvers'] if not doc['approvals'].get(p)]
    return doc['status']=='review' and actor in unanswered and (doc['mode']!='sequential' or (unanswered and unanswered[0]==actor))

def mutate(state,b,user,db):
    if str(b.get('action','')).startswith('user'):return administer(state,b,user,db)
    actor=effective_actor(user,b,db)
    if universal.apply(sys.modules[__name__],state,b,user,db,actor):return
    if v3.apply(sys.modules[__name__],state,b,user,db,actor):
        universal.after(sys.modules[__name__],state,b,user,actor)
        return
    action=b.get('action');stamp=now()
    doc=next((d for d in state['documents'] if d['id']==b.get('documentId')),None)
    if doc and not can_view(state,doc,user):raise AppError('Нет доступа к документу',403)
    def require_doc():
        if doc is None: raise AppError('Документ не найден',404)
        if not can_view(state,doc,user):raise AppError('Нет доступа к документу',403)
        return doc
    def log(docid,action,comment='',kind='workflow'):
        state['events'].insert(0,{'id':uid(),'documentId':docid,'actor':actor,'action':action,'comment':clean(comment),'kind':kind,'createdAt':stamp,'username':user['username']})
    def participants(value):
        return validate_people(value,db)
    if action=='create':
        p=b.get('payload') or {}
        if not isinstance(p,dict): raise AppError('Некорректные реквизиты')
        title=clean(p.get('title'),200);dtype=p.get('type')
        if not title: raise AppError('Укажите название документа')
        universal.get_type(sys.modules[__name__],state,dtype)
        new={'id':uid(),'number':universal.number(sys.modules[__name__],state,dtype),'title':title,'type':dtype,'correspondent':clean(p.get('correspondent'),200),'department':clean(p.get('department'),100) or db.execute('SELECT department FROM users WHERE username=?',(actor,)).fetchone()[0], 'description':clean(p.get('description')),'amount':clean(p.get('amount'),30),'currency':p.get('currency') if p.get('currency') in ['TMT','USD','RUB'] else 'TMT','payment':clean(p.get('payment')),'date':valid_date(p.get('date')) or today(),'due':valid_date(p.get('due')),'status':'draft','isPublic':False,'access':[],'author':actor,'approvers':[],'approvals':{},'mode':'parallel','iteration':0,'finalApprover':'director','createdAt':stamp,'updatedAt':stamp}
        v3.card_fields(sys.modules[__name__],state,p,new,user,db)
        universal.init_card(sys.modules[__name__],state,p,new,db)
        state['documents'].insert(0,new);log(new['id'],'Создан документ')
    elif action=='edit':
        d=require_doc();p=b.get('payload') or {}
        if not isinstance(p,dict):raise AppError('Некорректные реквизиты')
        if not v3.can_edit(sys.modules[__name__],d,user,actor): raise AppError('Редактирование доступно автору, редактору или администратору в черновике и на доработке',403)
        before=dict(d)
        if not clean(p.get('title'),200): raise AppError('Укажите название')
        for k in ['title','correspondent','department','description','amount','payment']: d[k]=clean(p.get(k),200 if k in ['title','correspondent'] else 3000)
        if p.get('currency') in ['TMT','USD','RUB']: d['currency']=p['currency']
        d['date']=valid_date(p.get('date')) or d['date'];d['due']=valid_date(p.get('due'))
        v3.card_fields(sys.modules[__name__],state,p,d,user,db)
        universal.edit_card(sys.modules[__name__],state,p,d,db)
        labels={'title':'Название','correspondent':'Корреспондент','department':'Подразделение','description':'Содержание','amount':'Сумма','currency':'Валюта','payment':'Условия оплаты','date':'Дата','due':'Срок','externalNumber':'Внешний номер','externalDate':'Внешняя дата','registeredAt':'Дата регистрации','category':'Категория','tags':'Метки','priority':'Приоритет','caseId':'Дело'}
        changes=[f'{label}: {before.get(k) or "—"} → {d.get(k) or "—"}' for k,label in labels.items() if before.get(k)!=d.get(k)]
        if before.get('relatedIds')!=d.get('relatedIds'):changes.append('Изменены связи документов')
        for f in d.get('typeSnapshot',{}).get('fields',[]):
            old=before.get('customFields',{}).get(f['key'],'');newvalue=d.get('customFields',{}).get(f['key'],'')
            if old!=newvalue:changes.append(f["label"]+': '+str(old)+' → '+str(newvalue))
        log(d['id'],'Изменены реквизиты','\n'.join(changes))
    elif action in ['submit','participants']:
        d=require_doc();editing=action=='participants'
        if editing:
            if d['status'] not in ['review','approval'] or not can_manage(d,user,actor): raise AppError('Изменение маршрута недоступно',403)
            if not clean(b.get('comment')): raise AppError('Укажите причину изменения участников')
        elif not v3.can_edit(sys.modules[__name__],d,user,actor): raise AppError('Согласование запускает автор, редактор или администратор после подготовки',403)
        if any(l['documentId']==d['id'] and l['expires']>time.time() for l in state.get('fileLocks',[])):raise AppError('Завершите редактирование файлов перед согласованием')
        approvers=participants(b.get('approvers'))
        if not approvers: raise AppError('Добавьте хотя бы одного согласующего')
        preserve=editing or b.get('onlyPending') is True
        d['approvals']={k:v for k,v in d['approvals'].items() if preserve and k in approvers and v=='approved'}
        d['approvers']=approvers;d['mode']='sequential' if b.get('mode')=='sequential' else 'parallel'
        d['finalApprover']=participants([b.get('finalApprover') or d['finalApprover']])[0]
        d['status']='approval' if all(d['approvals'].get(p)=='approved' for p in approvers) else 'review'
        if not editing: d['iteration']+=1
        log(d['id'],'Изменены участники согласования' if editing else 'Отправлен на согласование',b.get('comment'))
    elif action in ['approve','reject','confirm']:
        d=require_doc()
        can_review=pending(d,actor);can_confirm=d['status']=='approval' and actor==d['finalApprover']
        if action=='approve':
            if not can_review: raise AppError('Сейчас документ ожидает другого участника',403)
            d['approvals'][actor]='approved';log(d['id'],'Согласовал документ',b.get('comment'))
            if all(d['approvals'].get(p)=='approved' for p in d['approvers']):
                d['status']='approval';log(d['id'],'Передан на утверждение','Все участники согласовали документ.')
        elif action=='reject':
            if not (can_review or can_confirm): raise AppError('Нет активного задания на согласование',403)
            if not clean(b.get('comment')): raise AppError('Укажите причину возврата')
            d['approvals'][actor]='rejected';d['status']='revision';log(d['id'],'Возвращён на доработку',b['comment'])
        else:
            if not can_confirm: raise AppError('Утверждение доступно назначенному руководителю',403)
            d['status']='execution' if any(t['documentId']==d['id'] and t['status'] not in v3.CLOSED for t in state['tasks']) else 'approved';log(d['id'],'Утвердил документ',b.get('comment'))
    elif action=='cancel':
        d=require_doc()
        if d['author']!=actor or d['status'] not in ['review','approval']: raise AppError('Отменить маршрут может автор',403)
        d['status']='draft';d['approvals']={};log(d['id'],'Маршрут согласования отменён',b.get('comment'))
    elif action=='comment':
        d=require_doc()
        if not clean(b.get('comment')): raise AppError('Введите сообщение')
        log(d['id'],'Добавил комментарий',b['comment'],'comment')
    elif action=='reading':
        d=require_doc();recipients=participants(b.get('recipients'))
        if not v3.can_assign(sys.modules[__name__],d,user,actor):raise AppError('Ознакомление назначает автор или утверждающий',403)
        if not recipients: raise AppError('Выберите сотрудников')
        existing=next((r for r in state['readings'] if r['id']==b.get('readingId') and r['documentId']==d['id']),None)
        if existing:
            if existing['author']!=actor and user['role']!='admin': raise AppError('Изменить участников может инициатор',403)
            if not clean(b.get('comment')): raise AppError('Укажите причину изменения участников')
            existing.update(recipients=recipients,done=[p for p in existing['done'] if p in recipients],due=valid_date(b.get('due')),comment=clean(b.get('comment')))
            log(d['id'],'Изменены участники ознакомления',b.get('comment'))
        else:
            state['readings'].insert(0,{'id':uid(),'documentId':d['id'],'recipients':recipients,'done':[],'due':valid_date(b.get('due')),'comment':clean(b.get('comment')),'author':actor,'createdAt':stamp})
            log(d['id'],'Отправлен на ознакомление',b.get('comment'))
    elif action=='acknowledge':
        reading=next((r for r in state['readings'] if r['id']==b.get('readingId')),None)
        if not reading or actor not in reading['recipients'] or actor in reading['done']: raise AppError('Нет активного задания на ознакомление',403)
        reading['done'].append(actor);log(reading['documentId'],'Ознакомление завершено',b.get('comment'))
    elif action=='access':
        d=require_doc()
        if not can_manage(d,user,actor):raise AppError('Доступ изменяет автор или администратор',403)
        if not isinstance(b.get('isPublic'),bool):raise AppError('Укажите видимость документа')
        access=validate_people(b.get('access'),db,allow_inactive=True)
        editors=validate_people(b.get('editors',d.get('editors',[])),db,allow_inactive=True)
        removed=set(d.get('editors',[]))-set(editors)
        if any(l['documentId']==d['id'] and l['actor'] in removed and l['expires']>time.time() for l in state.get('fileLocks',[])):raise AppError('Сначала завершите редактирование файлов у удаляемого редактора или снимите его блокировку')
        before={'isPublic':d.get('isPublic',False),'access':d.get('access',[]),'editors':d.get('editors',[])}
        d['editors']=editors
        d['isPublic']=b['isPublic'];d['access']=access
        after={'isPublic':d['isPublic'],'access':access,'editors':editors}
        log(d['id'],'Изменены права доступа','Общий доступ: '+str(before['isPublic'])+' → '+str(d['isPublic'])+'; читатели: '+', '.join(access)+'; редакторы: '+', '.join(editors))
        state['events'][0]['changes']={'before':before,'after':after}
    elif action in ['templateSave','templateDelete']:
        templates=state.setdefault('templates',[])
        template=next((t for t in templates if t['id']==b.get('templateId')),None)
        if b.get('templateId') and not template:raise AppError('Шаблон не найден',404)
        if template and template['owner']!=user['username'] and user['role']!='admin':raise AppError('Шаблон изменяет его автор или администратор',403)
        if action=='templateDelete':
            if not template:raise AppError('Шаблон не найден',404)
            templates.remove(template)
        else:
            name=clean(b.get('name'),120);approvers=participants(b.get('approvers'));final=participants([b.get('finalApprover')])[0]
            if not name or not approvers:raise AppError('Укажите название и хотя бы одного согласующего')
            if any(t['name'].casefold()==name.casefold() and t is not template for t in templates):raise AppError('Шаблон с таким названием уже существует')
            values={'name':name,'approvers':approvers,'finalApprover':final,'mode':'sequential' if b.get('mode')=='sequential' else 'parallel','updatedAt':stamp}
            if template:template.update(values)
            else:templates.append({'id':uid(),'owner':user['username'],'createdAt':stamp,**values})
    elif action=='readEvents':
        state.setdefault('readEventsByUser',{})[user['username']]=[e['id'] for e in state['events']]
    else: raise AppError('Неизвестное действие')
    if doc: doc['updatedAt']=stamp

LOGIN_STYLE='''body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f1f5fb;font:16px "Segoe UI",sans-serif;color:#24354f}.box{width:min(380px,calc(100vw - 76px));background:#fff;border:1px solid #e2e9f3;border-radius:14px;padding:34px;box-shadow:0 14px 45px #253d6810}h1{margin:0 0 6px;color:#326ce5;font-size:32px}h2{font-size:20px;margin-top:28px}p{line-height:1.7;color:#8292a8;font-size:14px}label{display:block;font-size:14px;color:#647b98;margin-top:17px}input{box-sizing:border-box;display:block;width:100%;padding:12px;border:1px solid #dce5f1;border-radius:7px;margin-top:7px;font-size:16px}button{background:#326ce5;color:white;border:0;border-radius:7px;width:100%;padding:13px;font-size:15px;margin-top:25px;cursor:pointer}.error{color:#bd524d;background:#fff2ee;padding:10px;border-radius:6px}a{color:#326ce5}'''

class Handler(BaseHTTPRequestHandler):
    server_version='RESMINAMA/0.3.0'
    def log_message(self, fmt,*args):
        print(self.log_date_time_string(),self.client_address[0],fmt%args)
    def send_bytes(self,payload,status=200,mime='application/json; charset=utf-8',headers=None):
        self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(payload)))
        self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','same-origin')
        self.send_header('X-Frame-Options','DENY');self.send_header('Cache-Control','no-store')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
        for k,v in (headers or {}).items():self.send_header(k,v)
        self.end_headers()
        if self.command!='HEAD': self.wfile.write(payload)
    def json(self,payload,status=200,headers=None):
        self.send_bytes(json.dumps(payload,ensure_ascii=False).encode(),status,headers=headers)
    def redirect(self,path,headers=None):
        self.send_bytes(b'',303,headers={'Location':path,**(headers or {})})
    def host_valid(self):
        try:
            host=urlsplit('http://'+self.headers.get('Host','')).hostname
            if host in ['localhost',socket.gethostname().lower()]: return True
            address=ipaddress.ip_address(host or '')
            return address.is_loopback or (self.server.server_address[0]=='0.0.0.0' and address.is_private)
        except ValueError: return False
    def require_origin(self):
        origin=self.headers.get('Origin','')
        if not origin or urlsplit(origin).netloc.lower()!=self.headers.get('Host','').lower():
            raise AppError('Запрос с другого сайта отклонён',403)
    def user(self,db):
        cookie=SimpleCookie()
        try:cookie.load(self.headers.get('Cookie',''))
        except Exception: return None
        token=cookie.get('resminama_session')
        if not token:return None
        return db.execute('SELECT u.* FROM sessions s JOIN users u ON u.username=s.username WHERE s.token_hash=? AND s.expires>? AND u.active=1',(hashlib.sha256(token.value.encode()).hexdigest(),time.time())).fetchone()
    def session_payload(self,db,user):
        result=response_state(db,user)
        visible_events={e['id'] for e in result['state']['events']}
        result['state']['readEvents']=[eid for eid in result['state'].get('readEventsByUser',{}).get(user['username'],[]) if eid in visible_events]
        result['state'].pop('readEventsByUser',None)
        return result
    def body(self,limit=1048576):
        try:size=int(self.headers.get('Content-Length','0'))
        except ValueError:raise AppError('Некорректный размер запроса')
        if size<=0 or size>limit: raise AppError('Превышен размер запроса',413)
        payload=self.rfile.read(size)
        if len(payload)!=size: raise AppError('Запрос получен не полностью')
        return payload
    def form_page(self,account=False,error='',success=False):
        title='Сменить пароль' if account else 'Вход в систему'
        fields='<label>Текущий пароль<input name="old" type="password" required autocomplete="current-password"></label><label>Новый пароль (не менее 10 символов)<input name="password" type="password" minlength="10" maxlength="200" required autocomplete="new-password"></label>' if account else '<label>Имя пользователя<input name="username" required autofocus autocomplete="username" maxlength="80"></label><label>Пароль<input name="password" type="password" required autocomplete="current-password" maxlength="200"></label>'
        notice='<p>Пароль изменён. <a href="/">Открыть рабочий стол</a></p>' if success else ''
        document=f'<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>RESMINAMA — {title}</title><style>{LOGIN_STYLE}</style><div class="box"><h1>RESMINAMA</h1><p>Система электронного документооборота</p><h2>{title}</h2>{notice}{"<p class=error>"+html.escape(error)+"</p>" if error else ""}<form method="post" action="{"/account" if account else "/login"}">{fields}<button>{"Сохранить пароль" if account else "Войти"}</button></form><p>{"<a href=/ >Назад к документам</a>" if account else "Первоначальные пароли находятся в файле data/ACCOUNTS.txt на компьютере сервера."}</p></div></html>'
        self.send_bytes(document.encode(),mime='text/html; charset=utf-8')
    def do_GET(self):
        try:
            if not self.host_valid():raise AppError('Недопустимый адрес сервера',403)
            url=urlsplit(self.path);path=url.path
            with connect() as db:
                user=self.user(db)
                if path=='/login':
                    if user:self.redirect('/')
                    else:self.form_page()
                    return
                if not user:
                    if path.startswith('/api/'):raise AppError('Требуется вход',401)
                    self.redirect('/login');return
                if path=='/account':self.form_page(account=True);return
                if path=='/api/sed':self.json(self.session_payload(db,user));return
                if path in ['/api/docx-preview','/api/card-docx']:
                    _,state=read_state(db);query=parse_qs(url.query)
                    if path=='/api/docx-preview':
                        fid=query.get('id',[''])[0];file=next((f for f in state['attachments'] if f['id']==fid),None)
                        if not file:raise AppError('Файл не найден',404)
                        doc=next((d for d in state['documents'] if d['id']==file['documentId']),None)
                        if not can_view(state,doc,user):raise AppError('Нет доступа к файлу',403)
                        if not file['name'].lower().endswith('.docx'):raise AppError('Просмотр поддерживает только DOCX')
                        source=(DATA/'uploads'/file['key']).resolve()
                        if not source.is_relative_to((DATA/'uploads').resolve()) or not source.is_file():raise AppError('Файл недоступен',404)
                        try:result=docx_ops.preview(source.read_bytes())
                        except ValueError as e:raise AppError(str(e))
                        self.json(result);return
                    doc=next((d for d in state['documents'] if d['id']==query.get('id',[''])[0]),None)
                    if not can_view(state,doc,user):raise AppError('Нет доступа к документу',403)
                    labels={'draft':'Черновик','review':'На согласовании','revision':'На доработке','approval':'На утверждении','approved':'Утверждён','execution':'На исполнении','complete':'Исполнен','archived':'В архиве','resolution':'На резолюции','signing':'На подписи','reading':'На ознакомлении','reply':'Подготовка ответа / отправки'}
                    payload=docx_ops.fill(ROOT/'templates','card',docx_ops.values(doc,directory(db),labels,state))
                    self.send_bytes(payload,mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(doc['number']+'_card.docx')});return
                if path=='/api/files':
                    _,state=read_state(db);fid=parse_qs(url.query).get('id',[''])[0]
                    file=next((f for f in state['attachments'] if f['id']==fid),None)
                    if not file:raise AppError('Файл не найден',404)
                    doc=next((d for d in state['documents'] if d['id']==file['documentId']),None)
                    if not can_view(state,doc,user):raise AppError('Нет доступа к файлу',403)
                    source=(DATA/'uploads'/file['key']).resolve()
                    if not source.is_relative_to((DATA/'uploads').resolve()) or not source.is_file():raise AppError('Файл недоступен',404)
                    self.send_bytes(source.read_bytes(),mime='application/octet-stream',headers={'Content-Disposition':f"attachment; filename=\"document-v{file['version']}\"; filename*=UTF-8''{quote(file['name'],safe='')}"});return
                if path=='/api/signatures':
                    query=parse_qs(url.query);actor=user['username']
                    if query.get('actor',[''])[0]:actor=effective_actor(user,{'actor':query['actor'][0]},db)
                    if not crypto_sign.has_certificate(DATA/'keys',actor):raise AppError('У вас нет ключевого контейнера. Создайте его в разделе «Электронная подпись».',409)
                    with STATE_LOCK,connect() as db2:
                        revision,state=read_state(db2)
                        info=crypto_sign.cert_info(DATA/'keys',actor) or {}
                        target=DATA/'prepared'/f'{revision}.pdf'
                        target.parent.mkdir(exist_ok=True)
                        if not target.exists():target.write_bytes(crypto_sign_test_pdf(f'RESMINAMA signing test file revision {revision}'))
                        sha=crypto_sign.sha256_file(target)
                    self.json({'ok':True,'revision':revision,'file':'prepared.pdf','sha256':sha,'size':target.stat().st_size,'certificate':info})
                    return
                if path=='/api/prepared':
                    revision=parse_qs(url.query).get('revision',[''])[0]
                    if not re.fullmatch(r'\d{1,15}',revision):raise AppError('Некорректная версия')
                    target=(DATA/'prepared'/f'{revision}.pdf').resolve()
                    if not target.is_relative_to((DATA/'prepared').resolve()) or not target.is_file():raise AppError('Файл недоступен',404)
                    self.send_bytes(target.read_bytes(),mime='application/pdf',headers={'Content-Disposition':'attachment; filename="prepared.pdf"'});return
            root=(ROOT/'web').resolve();file=(root/unquote(path).lstrip('/')).resolve()
            if not file.is_relative_to(root):raise AppError('Доступ запрещён',403)
            if path=='/':file=root/'index.html'
            if not file.is_file():raise AppError('Страница не найдена',404)
            self.send_bytes(file.read_bytes(),mime={'.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml','.html':'text/html; charset=utf-8'}.get(file.suffix.lower()) or mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
        except AppError as e:self.json({'error':str(e)},e.status)
        except (BrokenPipeError,ConnectionResetError):pass
        except Exception as e:
            print('Request error:',type(e).__name__,str(e));self.json({'error':'Ошибка сервера. Подробности в окне запуска.'},500)
    def do_POST(self):
        try:
            if not self.host_valid():raise AppError('Недопустимый адрес сервера',403)
            self.require_origin();path=urlsplit(self.path).path
            if path in ['/login','/account']:
                fields=parse_qs(self.body(16384).decode('utf-8'));password=fields.get('password',[''])[0]
                with connect() as db:
                    if path=='/login':
                        ip=self.client_address[0];recent=[t for t in FAILURES.get(ip,[]) if t>time.time()-900]
                        FAILURES[ip]=recent
                        if len(recent)>=10:raise AppError('Слишком много попыток. Повторите через 15 минут.',429)
                        username=fields.get('username',[''])[0].strip();row=db.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
                        if not row or not row['active'] or not check_password(password,row['password_hash']):
                            FAILURES[ip].append(time.time());self.form_page(error='Неверное имя пользователя или пароль');return
                        FAILURES.pop(ip,None);token=secrets.token_urlsafe(32)
                        db.execute('INSERT INTO sessions VALUES (?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),username,time.time()+28800));db.commit()
                        self.redirect('/',{'Set-Cookie':f'resminama_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=28800'});return
                    user=self.user(db)
                    if not user:raise AppError('Требуется вход',401)
                    row=db.execute('SELECT * FROM users WHERE username=?',(user['username'],)).fetchone()
                    if not check_password(fields.get('old',[''])[0],row['password_hash']):self.form_page(account=True,error='Текущий пароль неверен');return
                    if len(password)<10 or len(password)>200:self.form_page(account=True,error='Пароль должен содержать от 10 до 200 символов');return
                    db.execute('UPDATE users SET password_hash=? WHERE username=?',(password_hash(password),user['username']))
                    db.execute('DELETE FROM sessions WHERE username=?',(user['username'],))
                    self.redirect('/login',{'Set-Cookie':'resminama_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0'});return
            with STATE_LOCK,connect() as db:
                user=self.user(db)
                if not user:raise AppError('Требуется вход',401)
                if path=='/api/logout':
                    cookie=SimpleCookie(self.headers.get('Cookie',''));token=cookie.get('resminama_session')
                    if token:db.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(token.value.encode()).hexdigest(),))
                    self.json({'ok':True},headers={'Set-Cookie':'resminama_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0'});return
                db.execute('BEGIN IMMEDIATE');revision,state=read_state(db)
                if path=='/api/signature':
                    try:b=json.loads(self.body().decode('utf-8'))
                    except (ValueError,UnicodeError):raise AppError('Некорректный запрос')
                    if not isinstance(b,dict):raise AppError('Некорректный запрос')
                    action=b.get('action');actor=user['username']
                    try:result=self.signature_api(db,user,revision,action,b)
                    except crypto_sign.SignError as e:raise AppError(str(e),500 if 'openssl' not in str(e) else 503)
                    db.commit();self.json(result)
                    return
                if path=='/api/files':
                    self.file_upload(db,user,revision,state);return
                if path!='/api/sed':raise AppError('Действие не найдено',404)
                try:b=json.loads(self.body().decode('utf-8'))
                except (ValueError,UnicodeError):raise AppError('Некорректный запрос')
                if not isinstance(b,dict):raise AppError('Некорректный запрос')
                if b.get('revision')!=revision:raise AppError('Данные изменились. Обновите страницу и повторите действие.',409)
                if b.get('action')=='generateDocx':
                    self.generate_docx(db,user,revision,state,b);return
                receipt=mutate(state,b,user,db)
                db.execute('UPDATE workspace SET revision=revision+1,data=? WHERE id=1',(json.dumps(state,ensure_ascii=False),))
                db.commit();result=self.session_payload(db,user)
                if receipt:result['receipt']=receipt
                self.json(result)
        except AppError as e:self.json({'error':str(e)},e.status)
        except (BrokenPipeError,ConnectionResetError):pass
        except Exception as e:
            print('Request error:',type(e).__name__,str(e));self.json({'error':'Ошибка сервера. Подробности в окне запуска.'},500)
    def signature_api(self,db,user,revision,action,b):
        """ЭЦП (Ф12): ключевой контейнер, отсоединённая PKCS#7-подпись, реестр подписей."""
        keys=DATA/'keys';actor=user['username'];stamp=now()
        def audit(kind,target,detail=''):
            state.setdefault('adminAudit',[]).insert(0,{'id':uid(),'actor':actor,'action':kind,'target':target,'comment':clean(detail),'createdAt':stamp})
        _,state=read_state(db)
        if action=='certStatus':
            return {'ok':True,'certificate':crypto_sign.cert_info(keys,actor)}
        if action=='certCreate':
            info=crypto_sign.generate_selfsigned(keys,actor,clean(b.get('commonName'),120) or user.get('name') or actor)
            audit('esignCertCreate',actor,info.get('subject',''))
            db.execute('UPDATE workspace SET revision=revision+1,data=? WHERE id=1',(json.dumps(state,ensure_ascii=False),));db.commit()
            return {'ok':True,'certificate':info}
        if action=='certDelete':
            crypto_sign.remove_keys(keys,actor);audit('esignCertDelete',actor)
            db.execute('UPDATE workspace SET revision=revision+1,data=? WHERE id=1',(json.dumps(state,ensure_ascii=False),));db.commit()
            return {'ok':True}
        if action=='sign':
            try:sent=int(b.get('revision',-1))
            except (TypeError,ValueError):raise AppError('Некорректная версия данных')
            if sent!=revision:raise AppError('Данные изменились. Обновите страницу и повторите действие.',409)
            target=(DATA/'prepared'/f'{sent}.pdf').resolve()
            if not target.is_relative_to((DATA/'prepared').resolve()):raise AppError('Некорректный путь файла',400)
            if not target.is_file():
                target.parent.mkdir(exist_ok=True);target.write_bytes(crypto_sign_test_pdf(f'RESMINAMA signing test file revision {sent}'))
            sig=target.with_suffix('.p7s')
            result=crypto_sign.sign_file(keys,actor,target,sig)
            verify=crypto_sign.verify_file(keys,actor,target,sig)
            if not verify['valid']:raise AppError('Подпись не прошла контрольную проверку: '+verify.get('detail',''))
            record={'id':uid(),'owner':actor,'username':user['username'],'fileName':'prepared.pdf','fileSha256':result['fileSha256'],
                    'algorithm':result['algorithm'],'documentId':clean(b.get('documentId'),80),'createdAt':stamp,'valid':True}
            cert=crypto_sign.cert_info(keys,actor) or {}
            record.update(certSubject=cert.get('subject',''),certFingerprint=cert.get('fingerprint',''))
            state.setdefault('signatures',[]).insert(0,record)
            state['events'].insert(0,{'id':uid(),'documentId':record['documentId'],'actor':actor,'username':user['username'],
                'action':'Документ подписан ЭЦП','comment':'SHA-256 '+record['fileSha256'][:16]+'…','kind':'signature','createdAt':stamp})
            db.execute('UPDATE workspace SET revision=revision+1,data=? WHERE id=1',(json.dumps(state,ensure_ascii=False),));db.commit()
            audit('esignSign',record['id'],record['fileSha256'])
            return {'ok':True,'signature':record,'revision':revision+1}
        if action=='verify':
            record=next((x for x in state.get('signatures',[]) if x['id']==b.get('signatureId')),None)
            if not record:raise AppError('Подпись не найдена',404)
            # находим исходный prepared-файл по хешу среди prepared/*.pdf
            ok=False;detail='исходный файл не найден на сервере'
            for cand in sorted((DATA/'prepared').glob('*.pdf')) if (DATA/'prepared').exists() else []:
                if crypto_sign.sha256_file(cand)==record['fileSha256']:
                    sig=cand.with_suffix('.p7s')
                    if sig.exists():
                        res=crypto_sign.verify_file(keys,record['owner'],cand,sig)
                        ok=res['valid'];detail=res.get('detail','')
                    else:detail='отсутствует файл подписи .p7s'
                    break
            record['lastCheck']={'at':stamp,'valid':ok,'by':user['username'],'detail':detail}
            db.execute('UPDATE workspace SET revision=revision+1,data=? WHERE id=1',(json.dumps(state,ensure_ascii=False),));db.commit()
            return {'ok':True,'valid':ok,'detail':detail}
        raise AppError('Неизвестное действие электронной подписи')

    def generate_docx(self,db,user,revision,state,b):
        actor=effective_actor(user,b,db);doc=next((d for d in state['documents'] if d['id']==b.get('documentId')),None)
        if not doc or not can_view(state,doc,user) or not v3.can_edit(sys.modules[__name__],doc,user,actor):raise AppError('Нет права создавать вложения',403)
        if b.get('templateId') not in ['memo','letter']:raise AppError('Выберите шаблон')
        try:payload=docx_ops.fill(ROOT/'templates',b['templateId'],docx_ops.values(doc,directory(db)))
        except ValueError as e:raise AppError(str(e))
        filename=doc['number']+'_'+b['templateId']+'_'+uid()[:6]+'.docx';key=uid();group=uid();stamp=now();target=DATA/'uploads'/key
        target.write_bytes(payload)
        try:
            state['attachments'].insert(0,{'id':uid(),'documentId':doc['id'],'name':filename,'groupId':group,'version':1,'size':len(payload),'mime':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','key':key,'author':actor,'createdAt':stamp})
            state['events'].insert(0,{'id':uid(),'documentId':doc['id'],'actor':actor,'username':user['username'],'action':'Создан DOCX из шаблона','comment':filename,'kind':'file','createdAt':stamp})
            doc['updatedAt']=stamp
            db.execute('UPDATE workspace SET revision=revision+1,data=? WHERE id=1',(json.dumps(state,ensure_ascii=False),));db.commit();self.json(self.session_payload(db,user))
        except Exception:
            if db.in_transaction:db.rollback();target.unlink(missing_ok=True)
            raise

    def file_upload(self,db,user,revision,state):
        content_type=self.headers.get('Content-Type','')
        if not content_type.startswith('multipart/form-data;'):raise AppError('Некорректный формат загрузки')
        raw=self.body(MAX_UPLOAD+1024*1024)
        message=BytesParser(policy=email_policy).parsebytes(('Content-Type: '+content_type+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+raw)
        fields={};payload=None;filename='';mime='application/octet-stream'
        if not message.is_multipart():raise AppError('Некорректный файл')
        for part in message.iter_parts():
            name=part.get_param('name',header='Content-Disposition');value=part.get_payload(decode=True) or b''
            if name=='file':payload=value;filename=part.get_filename() or '';mime=part.get_content_type()
            elif name:fields[name]=value.decode('utf-8')
        if not payload or len(payload)>MAX_UPLOAD:raise AppError('Выберите непустой файл размером до 20 МБ')
        filename=filename.replace('\\','/').rsplit('/',1)[-1][:220]
        if filename.lower().endswith('.docx'):
            try:docx_ops.preview(payload)
            except ValueError as e:raise AppError(str(e))
        if Path(filename).suffix.lower() not in ['.pdf','.doc','.docx','.xls','.xlsx','.txt','.png','.jpg','.jpeg','.sig','.p7s','.cms']:raise AppError('Неподдерживаемый тип файла')
        try:sent_revision=int(fields.get('revision','-1'))
        except ValueError:raise AppError('Некорректная версия данных')
        if sent_revision!=revision:raise AppError('Данные изменились. Обновите страницу и повторите загрузку.',409)
        actor=effective_actor(user,fields,db);doc=next((d for d in state['documents'] if d['id']==fields.get('documentId')),None)
        if not doc:raise AppError('Документ не найден',404)
        if not can_view(state,doc,user):raise AppError('Нет доступа к документу',403)
        stage=universal.file_stage(doc,actor) if fields.get('purpose')=='process' else None
        if fields.get('purpose')=='process' and not stage:raise AppError('Нет права загрузить результат текущего этапа',403)
        if stage:
            if Path(filename).suffix.lower() not in ['.pdf','.docx'] or fields.get('groupId'):raise AppError('На этапе принимается новый файл PDF/DOCX')
        elif not v3.can_edit(sys.modules[__name__],doc,user,actor):raise AppError('Вложения доступны редакторам в черновике или на доработке',403)
        prior=None if stage else next((f for f in state['attachments'] if f['documentId']==doc['id'] and (f['groupId']==fields['groupId'] if fields.get('groupId') else f['name']==filename)),None)
        if fields.get('groupId') and not prior:raise AppError('Группа версий не найдена',404)
        if prior:
            latest=max(f['version'] for f in state['attachments'] if f['groupId']==prior['groupId'])
            try:base=int(fields.get('baseVersion','-1'))
            except ValueError:raise AppError('Укажите исходную версию файла')
            if base!=latest:raise AppError('Файл уже изменён или не указана исходная версия. Скачайте последнюю версию.',409)
            lock=v3.live_lock(state,prior['groupId'])
            if lock and lock['username']!=user['username']:raise AppError('Файл редактирует другой сотрудник',409)
        groupid=prior['groupId'] if prior else uid();version=max([0]+[f['version'] for f in state['attachments'] if f['groupId']==groupid])+1;key=uid();stamp=now();target=DATA/'uploads'/key
        target.write_bytes(payload)
        try:
            state['attachments'].insert(0,{'id':uid(),'documentId':doc['id'],'name':filename,'groupId':groupid,'version':version,'size':len(payload),'mime':mime,'key':key,'author':actor,'createdAt':stamp})
            state['attachments'][0]['comment']=clean(fields.get('comment'))
            if stage:state['attachments'][0].update(processId=doc['process']['id'],processStepId=stage['id'])
            state['events'].insert(0,{'iteration':doc.get('iteration',0),'fileId':state['attachments'][0]['id'],'fileVersion':version,'id':uid(),'documentId':doc['id'],'actor':actor,'action':f'Загружено вложение · версия {version}','comment':filename,'kind':'file','createdAt':stamp,'username':user['username']})
            state['fileLocks']=[l for l in state['fileLocks'] if l['groupId']!=groupid]
            doc['updatedAt']=stamp;db.execute('UPDATE workspace SET revision=revision+1,data=? WHERE id=1',(json.dumps(state,ensure_ascii=False),))
            db.commit();self.json(self.session_payload(db,user))
        except Exception:
            if db.in_transaction:db.rollback();target.unlink(missing_ok=True)
            raise

def backup():
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S');directory=ROOT/'backups';directory.mkdir(exist_ok=True)
    temporary=directory/f'resminama_{stamp}.sqlite3';archive=directory/f'RESMINAMA_backup_{stamp}.zip'
    with STATE_LOCK, connect() as source, sqlite3.connect(temporary) as target:source.backup(target)
    try:
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
            z.write(temporary,'data/resminama.sqlite3')
            for path in (DATA/'uploads').glob('*'):
                if path.is_file():z.write(path,'data/uploads/'+path.name)
    finally:temporary.unlink(missing_ok=True)
    print('Backup saved:',archive)

def main():
    global DATA
    parser=argparse.ArgumentParser(description='RESMINAMA local SED')
    parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=8000)
    parser.add_argument('--no-browser',action='store_true');parser.add_argument('--empty',action='store_true',help='Start without sample documents on the FIRST launch only')
    parser.add_argument('--data-dir',type=Path);parser.add_argument('--backup',action='store_true');parser.add_argument('--reset-password',metavar='LOGIN')
    args=parser.parse_args()
    if args.data_dir:DATA=args.data_dir.resolve()
    if args.backup and not (DATA/'resminama.sqlite3').exists():parser.error('No database to back up. Run the application first.')
    initialize(empty=args.empty)
    if args.backup:backup();return
    if args.reset_password:
        with connect() as db:
            if not db.execute('SELECT 1 FROM users WHERE username=?',(args.reset_password,)).fetchone():parser.error('User not found')
            password=secrets.token_urlsafe(11);db.execute('UPDATE users SET password_hash=? WHERE username=?',(password_hash(password),args.reset_password));db.execute('DELETE FROM sessions WHERE username=?',(args.reset_password,))
        print('New password for',args.reset_password,':',password);return
    if not (ROOT/'web'/'index.html').exists():parser.error('The web folder is missing. Extract the complete ZIP archive.')
    try:server=ThreadingHTTPServer((args.host,args.port),Handler)
    except OSError as e:parser.error(f'Cannot start server: {e}. Try --port 8001')
    url=f'http://127.0.0.1:{server.server_port}'
    print('\nRESMINAMA SED 0.3.0\nOpen:',url,'\nData:',DATA,'\nStop: Ctrl+C\n',flush=True)
    if not args.no_browser:threading.Timer(1,lambda:webbrowser.open(url)).start()
    try:server.serve_forever()
    except KeyboardInterrupt:print('\nStopping RESMINAMA...')
    finally:server.server_close()
if __name__=='__main__':main()
