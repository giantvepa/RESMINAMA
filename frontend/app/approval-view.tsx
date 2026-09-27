import React,{useId,useState} from 'react';
import {Check,RotateCcw,Users,GitBranch,Printer,Send,X,ArrowRight} from 'lucide-react';
import {Dialog,DialogContent,DialogHeader,DialogTitle,DialogDescription,DialogFooter} from '@/components/ui/dialog';
import {currentStep,dateText,pendingFor,person,type Doc,type ProcessInstance} from '@/lib/sed';
import {approvalModel,defaultReviewIndex,reviewSteps,reviewLabels,type ApprovalModel,type ReviewState} from '@/lib/approval';
import {useApp} from './workspace';
import {MultiPeople} from './universal-ui';

const colors:Record<ReviewState,{fill:string;stroke:string;text:string}>={waiting:{fill:'#f4f7fb',stroke:'#c8d3e1',text:'#6b7d93'},active:{fill:'#e8f2ff',stroke:'#397bcc',text:'#245991'},accepted:{fill:'#e6f6ef',stroke:'#3f9772',text:'#246c50'},rejected:{fill:'#fff0ef',stroke:'#d36b68',text:'#a83e3a'},stopped:{fill:'#faf4e9',stroke:'#c3a979',text:'#8b6c36'}};
const short=(s:string,n=27)=>s.length>n?s.slice(0,n-1)+'…':s;

export function ApprovalDiagram({model:m,selected,onSelect}:{model:ApprovalModel;selected?:string;onSelect?:(id:string)=>void}){
 const token=useId().replace(/:/g,'');const n=m.rows.length;const sequential=m.mode==='sequential';const rowH=96;
 const ys=m.rows.map((_,i)=>158+i*rowH),last=ys.at(-1)||158,finish=last+rowH,joinX=sequential?410+(n-1)*224+232:712,endX=joinX+135,width=endX+216,height=finish+62;
 const arrow=(color:string)=>`url(#${token}-${color.slice(1)})`;
 const line=(d:string,color='#9eafc2',marker=true)=> <path d={d} fill="none" stroke={color} strokeWidth="1.6" markerEnd={marker?arrow(color):undefined}/>;
 const routeColor=m.joined?'#3f9772':'#9eafc2';
 return <div className="approval-canvas" tabIndex={0} aria-label="Схема согласования. При необходимости прокрутите по горизонтали."><svg xmlns="http://www.w3.org/2000/svg" width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="approval-svg" fontFamily="Arial, sans-serif" role="group" aria-label={`${m.title}: ${sequential?'последовательное':'параллельное'} согласование, итерация ${m.iteration||'не запущена'}`}>
 <defs>{['#9eafc2','#3f9772','#d36b68'].map(c=><marker id={`${token}-${c.slice(1)}`} key={c} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill={c}/></marker>)}</defs>
 <rect width={width} height={height} fill="white"/>
 {[{name:m.author,department:'Инициатор · '+m.authorDepartment},...m.rows.map(r=>({name:r.name,department:r.department})),{name:m.nextPeople||'Следующий этап',department:m.nextLabel}].map((p,i)=><g key={i}>
  <rect x="0" y={14+i*rowH} width={width} height={rowH} fill={i===0?'#fcfdff':i<=n?'#f2f9fb':'#fbfcfe'} stroke="#e2eaf2"/>
  <rect x="0" y={14+i*rowH} width="220" height={rowH} fill={i>0&&i<=n?'#eaf3f7':'#f3f6fa'} stroke="#e2eaf2"/>
  <title>{p.name+" · "+p.department}</title><text x="18" y={55+i*rowH} fill="#334c68" fontSize="13" fontWeight="600">{short(p.name,25)}</text><text x="18" y={77+i*rowH} fill="#70849d" fontSize="11">{short(p.department,29)}</text>
 </g>)}
 <circle cx="266" cy="62" r="9" fill={m.started?'#e6f6ef':'white'} stroke={m.started?'#3f9772':'#9eafc2'} strokeWidth="2"/><text x="248" y="90" fontSize="11" fill="#70849d">Начало</text>
 {sequential?line(`M 275 62 H 320 V ${ys[0]} H 410`):<>{line(`M 275 62 H 344 V ${last}`, '#9eafc2',false)}{ys.map(y=><g key={y}>{line(`M 344 ${y} H 410`)}</g>)}</>}
 {m.rows.map((r,i)=>{const x=sequential?410+i*224:410,y=ys[i],c=colors[r.state];return <g key={r.id}>
  {sequential?(i<n-1?line(`M ${x+184} ${y} H ${x+204} V ${ys[i+1]} H ${x+224}`,r.state==='accepted'?'#3f9772':'#9eafc2'):line(`M ${x+184} ${y} H ${joinX-13}`,routeColor)):line(`M ${x+184} ${y} H ${joinX} V ${last}`,r.state==='accepted'?'#3f9772':'#9eafc2',false)}
  <g role="button" tabIndex={0} aria-label={`${r.name}: ${reviewLabels[r.state]}`} aria-pressed={selected===r.id} onClick={()=>onSelect?.(r.id)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();onSelect?.(r.id);}}} style={{cursor:'pointer'}}>
   <title>{r.name+': '+reviewLabels[r.state]+(r.comment?' — '+r.comment:'')}</title><rect x={x} y={y-26} width="184" height="52" rx="6" fill={c.fill} stroke={c.stroke} strokeWidth={selected===r.id?3:1.5}/>
   <text x={x+92} y={y-3} textAnchor="middle" fontSize="12" fontWeight="600" fill={c.text}>{reviewLabels[r.state]}</text><text x={x+92} y={y+14} textAnchor="middle" fontSize="10" fill={c.text}>{sequential?`Очередь ${i+1}`:'Параллельный участник'}</text>
  </g>
 </g>})}
 {line(`M ${joinX+13} ${last} H ${joinX+63} V 62 H ${endX}`,m.rejected?'#d36b68':'#9eafc2')}
 {line(`M ${joinX+13} ${last} H ${joinX+63} V ${finish} H ${endX}`,routeColor)}
 <polygon points={`${joinX},${last-13} ${joinX+13},${last} ${joinX},${last+13} ${joinX-13},${last}`} fill={m.joined?'#e6f6ef':'white'} stroke={routeColor} strokeWidth="1.5"/><text x={joinX} y={last+4} textAnchor="middle" fill="#4c627c" fontSize="15">{m.joined?'✓':sequential?'›':'+'}</text>
 <text x={joinX+74} y="103" fill={m.rejected?'#a83e3a':'#7e8fa5'} fontSize="10">Отклонено</text><text x={joinX+74} y={finish-39} fill={m.joined?'#246c50':'#7e8fa5'} fontSize="10">Все согласовали</text>
 <rect x={endX} y="38" width="192" height="48" rx="5" fill={m.rejected?'#fff0ef':'#f5f7fa'} stroke={m.rejected?'#d36b68':'#c8d3e1'}/><text x={endX+96} y="67" textAnchor="middle" fontSize="12" fill={m.rejected?'#a83e3a':'#6b7d93'}>На доработку</text>
 <rect x={endX} y={finish-25} width="192" height="50" rx="5" fill={colors[m.nextState].fill} stroke={colors[m.nextState].stroke} strokeWidth={m.nextState==='active'?2:1}/><text x={endX+96} y={finish-3} textAnchor="middle" fontSize="12" fill={colors[m.nextState].text}>{short(m.nextLabel,24)}</text><text x={endX+96} y={finish+14} textAnchor="middle" fontSize="10" fill={colors[m.nextState].text}>{m.nextState==='accepted'?'Завершено':m.nextState==='active'?'Текущий этап':m.nextState==='rejected'?'Отклонено':'После согласования'}</text>
 </svg></div>;
}

export function ApprovalView({doc:d,onRoute}:{doc:Doc;onRoute:()=>void}){
 const {actor,session,act,busy,open,data}=useApp();const universal=!!(d.process||d.routePlan);
 const [instanceId,setInstance]=useState('current'),[stepChoice,setStep]=useState<number|null>(null),[selected,setSelected]=useState('');
 const historical=instanceId!=='current';const history=d.processHistory||[];const process=historical?history.find(p=>p.id===instanceId):d.process;
 const stages=reviewSteps(d,process);const index=stages.some(x=>x.index===stepChoice)?stepChoice!:defaultReviewIndex(d,process);
 const iteration=historical?d.iteration-history.length+history.findIndex(p=>p.id===instanceId):d.iteration;
 const model=approvalModel(d,process,index,historical,iteration);const current=currentStep(d);const managed=!!session?.canSwitch||d.author===actor;
 const canEdit=(managed||d.editors?.includes(actor))&&['draft','revision'].includes(d.status);
 const live=!historical&&d.status!=='archived';const mine=universal?!!current&&['review','approval'].includes(current.kind)&&current.participants.includes(actor)&&!current.decisions?.[actor]&&(current.mode!=='sequential'||current.participants.find(p=>!current.decisions?.[p])===actor):pendingFor(d,actor)||d.status==='approval'&&d.finalApprover===actor;
 const active=universal?!!current:['review','approval'].includes(d.status);
 const [dialog,setDialog]=useState<'accept'|'reject'|'cancel'|'participants'|null>(null),[comment,setComment]=useState(''),[members,setMembers]=useState<string[]>([]);
 const participantIndex=current?.kind==='approval'?d.process!.current:index;
 const canParticipants=live&&managed&&active&&(universal?participantIndex>=d.process!.current&&participantIndex>=0:true);
 function show(kind:typeof dialog){setComment('');setMembers(universal?d.process?.steps[participantIndex]?.participants||[]:d.approvers);setDialog(kind);}
 async function submit(e:React.FormEvent){e.preventDefault();let result;
  if(universal){const action=dialog==='participants'?'processParticipants':dialog==='cancel'?'processCancel':'processDecision';result=await act(action,{documentId:d.id,comment,...(dialog==='participants'?{stepIndex:participantIndex,participants:members}:{decision:dialog==='reject'?'reject':'accept'})});}
  else if(dialog==='participants'){open({kind:'participants',documentId:d.id});setDialog(null);return;}
  else result=await act(dialog==='cancel'?'cancel':dialog==='reject'?'reject':d.status==='approval'?'confirm':'approve',{documentId:d.id,comment});
  if(result)setDialog(null);
 }
 const found=model.rows.find(r=>r.id===selected);const legacyEvents=data.events.filter(e=>e.documentId===d.id);const start=legacyEvents.findIndex(e=>e.action==='Отправлен на согласование');const decision=!universal&&found?legacyEvents.slice(0,start<0?undefined:start).find(e=>e.actor===found.id&&e.action===(found.state==='accepted'?'Согласовал документ':'Возвращён на доработку')):undefined;const row=found?{...found,comment:found.comment||decision?.comment||'',date:found.date||decision?.createdAt||''}:undefined;const changedSelection=()=>setSelected('');
 return <div className="approval-view"><div className="approval-main"><div className="approval-toolbar"><div><h2><GitBranch size={18}/> Схема согласования</h2><p>{model.mode==='sequential'?'Последовательное согласование':'Параллельное согласование'} · {model.iteration?'Итерация '+model.iteration:'Маршрут ещё не запущен'}</p></div><div className="approval-selectors">{!!history.length&&<label>Итерация<select className="native-select" aria-label="Итерация согласования" value={instanceId} onChange={e=>{setInstance(e.target.value);setStep(null);changedSelection();}}><option value="current">{d.iteration} · текущая</option>{history.map((p,i)=><option key={p.id} value={p.id}>{d.iteration-history.length+i} · {p.state==='revision'?'доработка':p.state==='cancelled'?'отменена':'завершена'}</option>)}</select></label>}{stages.length>1&&<label>Этап<select className="native-select" aria-label="Этап согласования" value={index} onChange={e=>{setStep(Number(e.target.value));changedSelection();}}>{stages.map(x=><option key={x.index} value={x.index}>{x.index+1}. {x.step.label}</option>)}</select></label>}</div></div>
 {historical&&<p className="approval-notice">Предыдущая итерация доступна только для просмотра. Для действий выберите текущую.</p>}
 {model.rows.length?<><ApprovalDiagram model={model} selected={selected} onSelect={setSelected}/><div className="approval-legend">{(['waiting','active','accepted','rejected','stopped'] as ReviewState[]).map(state=><span key={state}><i style={{background:colors[state].fill,borderColor:colors[state].stroke}}/>{reviewLabels[state]}</span>)}</div><p className="approval-caption">Нажмите на блок сотрудника, чтобы увидеть его решение. {model.mode==='sequential'?'Следующий сотрудник получает право решения после предыдущего.':'Следующий этап открывается после согласования всеми участниками.'}</p>{row&&<section className="approval-selection"><div><h3>{row.name}</h3><span style={{color:colors[row.state].text}}>{reviewLabels[row.state]}</span></div><p>{row.comment||'Комментарий отсутствует.'}</p>{row.date&&<small>{dateText(row.date)} · {new Date(row.date).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'})}</small>}</section>}</>:<div className="approval-empty"><GitBranch size={30}/><h3>{universal?'В выбранном маршруте нет этапа согласования':'Согласующие пока не назначены'}</h3><p>{universal?'Действия по резолюции, исполнению и другим этапам находятся на вкладке «Маршрут».':'Назначьте участников и запустите согласование.'}</p>{universal&&<button className="btn secondary" onClick={onRoute}>Открыть маршрут<ArrowRight size={16}/></button>}</div>}
 </div><aside className="approval-actions"><h3>Действия</h3><p className="approval-actor">{person(actor).name}</p>
 {live&&mine&&<><button className="btn success" disabled={busy} onClick={()=>show('accept')}><Check size={17}/>{d.status==='approval'?'Утвердить':'Согласовать'}</button><button className="btn danger-soft" disabled={busy} onClick={()=>show('reject')}><RotateCcw size={17}/>Отклонить</button></>}
 {live&&canEdit&&<><button className="btn primary" disabled={busy} onClick={()=>universal?act('processStart',{documentId:d.id}):open({kind:'submit',documentId:d.id})}><Send size={17}/>{universal?(d.iteration?'Запустить новую итерацию':'Запустить маршрут'):(d.iteration?'Повторное согласование':'Начать согласование')}</button><button className="btn secondary" onClick={()=>open({kind:'edit',documentId:d.id})}>Редактировать карточку</button></>}
 {canParticipants&&<button className="btn secondary" disabled={busy} onClick={()=>universal?show('participants'):open({kind:'participants',documentId:d.id})}><Users size={17}/>Изменить участников</button>}
 {live&&(universal?managed:d.author===actor)&&active&&<button className="btn danger-soft" disabled={busy} onClick={()=>show('cancel')}><X size={17}/>Отменить процесс</button>}
 {(!live||!mine&&!canEdit)&&<p className="approval-hint">{historical?'История решений этой итерации.':d.status==='archived'?'Архивный документ доступен только для чтения.':universal&&current?'Текущий этап: '+current.label+'. Решение принимает назначенный сотрудник.':d.status==='review'?'Ожидаются решения согласующих.':d.status==='approval'?'Документ ожидает утверждения.':'Согласование сейчас не выполняется.'}</p>}
 {universal&&<button className="text-button" onClick={onRoute}>Все этапы маршрута<ArrowRight size={15}/></button>}<button className="btn secondary approval-print" onClick={()=>window.print()}><Printer size={16}/>Печать схемы</button><small>Решения сохраняются сразу и записываются в журнал.</small>
 </aside>
 {dialog&&<Dialog open onOpenChange={v=>!v&&setDialog(null)}><DialogContent className="app-dialog"><DialogHeader><DialogTitle>{dialog==='participants'?'Изменить участников':dialog==='cancel'?'Отменить процесс':dialog==='reject'?'Вернуть на доработку':d.status==='approval'?'Утвердить документ':'Согласовать документ'}</DialogTitle><DialogDescription>{dialog==='participants'?'Прошедшие этапы сохраняются. Укажите причину изменения.':dialog==='cancel'?'Процесс будет отменён, документ вернётся в черновики. История сохранится.':dialog==='reject'?'Укажите замечания для автора документа.':'Решение будет сохранено от имени текущего сотрудника.'}</DialogDescription></DialogHeader><form className="modal-form" onSubmit={submit}>{dialog==='participants'&&<MultiPeople value={members} onChange={setMembers}/>}<label className="form-field">{dialog==='accept'?'Комментарий':'Причина *'}<textarea required={dialog!=='accept'} maxLength={3000} value={comment} onChange={e=>setComment(e.target.value)}/></label><DialogFooter><button type="button" className="btn secondary" onClick={()=>setDialog(null)}>Назад</button><button className={`btn ${dialog==='reject'||dialog==='cancel'?'danger-soft':'primary'}`} disabled={busy||dialog==='participants'&&!members.length}>{dialog==='participants'?'Сохранить участников':dialog==='cancel'?'Отменить процесс':dialog==='reject'?'Вернуть на доработку':'Подтвердить решение'}</button></DialogFooter></form></DialogContent></Dialog>}
 </div>;
}
