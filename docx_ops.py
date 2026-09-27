"""Bounded DOCX text extraction and template filling. No Office installation needed."""
from io import BytesIO
from pathlib import Path
import re,zipfile
from xml.etree import ElementTree as ET
W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
ET.register_namespace('w',W)
TEMPLATES={'memo':('memo.docx','Служебная записка'),'letter':('letter.docx','Деловое письмо'),'card':('card.docx','Карточка документа')}

def package(blob):
    try:
        z=zipfile.ZipFile(BytesIO(blob))
        infos=z.infolist()
        if len(infos)>2000 or sum(i.file_size for i in infos)>40*1024*1024:raise ValueError('DOCX слишком большой после распаковки')
        if len({i.filename for i in infos})!=len(infos):raise ValueError('Повторяющиеся части DOCX')
        for name in ['[Content_Types].xml','word/document.xml']:
            if name not in z.namelist() or z.getinfo(name).file_size>8*1024*1024:raise ValueError('Некорректная структура DOCX')
        types=z.read('[Content_Types].xml')
        if b'macroEnabled' in types or any('vbaProject' in i.filename for i in infos):raise ValueError('DOCX с макросами не поддерживается')
        if b'wordprocessingml.document.main+xml' not in types:raise ValueError('Требуется документ DOCX')
        return z
    except (zipfile.BadZipFile,KeyError,RuntimeError) as e:raise ValueError('Файл не является корректным DOCX') from e

def xml(data):
    try:
        text=data.decode('utf-8-sig')
        if '\x00' in text or re.search(r'<!\s*(DOCTYPE|ENTITY)',text,re.I):raise ValueError('Небезопасная структура XML')
        return ET.fromstring(text)
    except (UnicodeError,ET.ParseError) as e:raise ValueError('Не удалось прочитать XML документа') from e

def preview(blob):
    try:
        with package(blob) as z:root=xml(z.read('word/document.xml'))
    except (zipfile.BadZipFile,RuntimeError,KeyError) as e:raise ValueError('Повреждённый DOCX') from e
    if root.tag!='{'+W+'}document':raise ValueError('Некорректная структура документа Word')
    paragraphs=[];length=0;truncated=False
    for p in root.iter('{'+W+'}p'):
        text=''.join((n.text or '') if n.tag=='{'+W+'}t' else '\n' if n.tag in ['{'+W+'}br','{'+W+'}cr'] else '\t' if n.tag=='{'+W+'}tab' else '' for n in p.iter())
        if text:
            length+=len(text)
            if len(paragraphs)>=2000 or length>300_000:truncated=True;break
            paragraphs.append(text)
    return {'paragraphs':paragraphs,'truncated':truncated,'notice':'Текстовый просмотр. Таблицы показаны как текст; изображения, комментарии и исправления проверяйте в Word или ONLYOFFICE Desktop.'}

def fill(template_dir:Path,kind:str,values:dict):
    if kind not in TEMPLATES:raise ValueError('Неизвестный шаблон DOCX')
    blob=(template_dir/TEMPLATES[kind][0]).read_bytes();out=BytesIO()
    with package(blob) as source,zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            data=source.read(info.filename)
            if info.filename=='word/document.xml':
                root=xml(data)
                root.attrib.pop('{http://schemas.openxmlformats.org/markup-compatibility/2006}Ignorable',None)
                for p in root.iter('{'+W+'}p'):
                    nodes=list(p.iter('{'+W+'}t'));text=''.join(n.text or '' for n in nodes)
                    if '{{' not in text:continue
                    def replace(match):
                        key=match[1]
                        if key not in values:raise ValueError('Неизвестное поле шаблона: '+key)
                        return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]','',str(values[key] or '—'))
                    replaced=re.sub(r'\{\{([a-zA-Z_]+)\}\}',replace,text)
                    if nodes:
                        parts=replaced.replace('\r\n','\n').replace('\r','\n').split('\n')
                        nodes[0].text=parts[0];nodes[0].set('{http://www.w3.org/XML/1998/namespace}space','preserve')
                        parent=next(x for x in p.iter() if nodes[0] in list(x));at=list(parent).index(nodes[0])+1
                        for line in parts[1:]:
                            parent.insert(at,ET.Element('{'+W+'}br'));at+=1
                            node=ET.Element('{'+W+'}t',{'{http://www.w3.org/XML/1998/namespace}space':'preserve'});node.text=line;parent.insert(at,node);at+=1
                        for n in nodes[1:]:n.text=''
                data=ET.tostring(root,encoding='utf-8',xml_declaration=True)
            target.writestr(info,data)
    return out.getvalue()

def values(doc,people,status_labels=None,state=None):
    def name(id):return next((p['name'] for p in people if p['id']==id),id)
    state=state or {};case=next((c for c in state.get('cases',[]) if c['id']==doc.get('caseId')),None);archive=doc.get('archive')
    latest={}
    for f in state.get('attachments',[]):
        if f['documentId']==doc['id'] and f['version']>latest.get(f['groupId'],{}).get('version',0):latest[f['groupId']]=f
    task_labels={'new':'Новое','progress':'В работе','verification':'На приёмке','done':'Исполнено','cancelled':'Отменено'}
    fields={**{k:doc.get(k,'') for k in ['title','number','description','correspondent','department','amount','currency','payment','externalNumber','externalDate','registeredAt','category','tags']},'external_number':doc.get('externalNumber',''),'external_date':doc.get('externalDate',''),'registered_at':doc.get('registeredAt',''),'date':doc['date'],'due':doc['due'],'author':name(doc['author']),'status':(status_labels or {}).get(doc['status'],doc['status'])}
    fields.update(priority={'normal':'Обычный','high':'Высокий','urgent':'Срочный'}.get(doc.get('priority','normal'),'Обычный'),case=(case['index']+' · '+case['title']) if case else '',editors=', '.join(name(p) for p in doc.get('editors',[])) or 'Автор и администратор',approval_route='; '.join(name(p)+' — '+{'approved':'согласовано','rejected':'доработка'}.get(doc['approvals'].get(p),'ожидает') for p in doc['approvers']),final_approver=name(doc['finalApprover']),archive_info=(archive['date']+'; дело '+archive['caseIndex']+'; хранение: '+('не установлено' if archive['years'] is None else 'постоянно' if archive['years']==0 else str(archive['years'])+' лет')+'; место: '+(archive.get('location') or 'не указано')) if archive else 'Не в архиве',files='\n'.join(f['name']+' · v'+str(f['version']) for f in latest.values()) or 'Вложения отсутствуют',tasks='\n'.join(t['title']+' — '+name(t['assignee'])+'; '+task_labels.get(t['status'],t['status'])+'; срок: '+(t.get('due') or 'не задан') for t in state.get('tasks',[]) if t['documentId']==doc['id']) or 'Поручения отсутствуют')
    process=doc.get('process') or doc.get('routePlan')
    if process:
        stage_labels={'waiting':'ожидает','active':'текущий','done':'завершён','rejected':'доработка','cancelled':'отменён'}
        def member(id):return name(doc['author'] if id=='$author' else id)
        fields['approval_route']='\n'.join(str(i+1)+'. '+s['label']+' — '+', '.join(member(p) for p in s['participants'])+'; '+stage_labels.get(s.get('status','waiting'),s.get('status','waiting')) for i,s in enumerate(process['steps']))
        fields['final_approver']=', '.join(dict.fromkeys(member(p) for s in process['steps'] if s['kind'] in ['approval','signature'] for p in s['participants'])) or 'Не предусмотрен маршрутом'
    if state and doc.get('typeSnapshot'):
        details=['Тип документа: '+doc['typeSnapshot']['name']]
        for f in doc['typeSnapshot']['fields']:
            v=doc.get('customFields',{}).get(f['key'],'')
            if v=='':continue
            value=('Да' if v else 'Нет') if f['kind']=='boolean' else name(v) if f['kind']=='person' else str(v)
            details.append(f['label']+': '+value)
        fields['description']='\n'.join([fields.get('description',''),'']+details).strip()
    return fields
