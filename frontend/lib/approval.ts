import {person,type Doc,type ProcessInstance,type ProcessStep} from './sed';

export type ReviewState='waiting'|'active'|'accepted'|'rejected'|'stopped';
export const reviewLabels:Record<ReviewState,string>={waiting:'Ожидает очереди',active:'На согласовании',accepted:'Согласовано',rejected:'Отклонено',stopped:'Не завершено'};
export type ReviewRow={id:string;name:string;department:string;state:ReviewState;comment:string;date:string};
export type ApprovalModel={rows:ReviewRow[];mode:string;title:string;author:string;authorDepartment:string;nextLabel:string;nextPeople:string;nextState:ReviewState;rejected:boolean;joined:boolean;started:boolean;iteration:number;historical:boolean;stepIndex:number};

export function reviewSteps(doc:Doc,process?:ProcessInstance){return (process?.steps||doc.routePlan?.steps||[]).map((step,index)=>({step,index})).filter(x=>x.step.kind==='review');}
export function defaultReviewIndex(doc:Doc,process?:ProcessInstance){
 const stages=reviewSteps(doc,process);
 return stages.find(x=>x.index===process?.current)?.index??stages.filter(x=>x.index<=(process?.current??0)).at(-1)?.index??stages[0]?.index??-1;
}
export function approvalModel(doc:Doc,process?:ProcessInstance,stepIndex=-1,historical=false,iteration=doc.iteration):ApprovalModel{
 const universal=!!(doc.process||doc.routePlan);const steps=process?.steps||doc.routePlan?.steps||[];const step=steps[stepIndex];
 const mode=universal?step?.mode||'parallel':doc.mode;
 const parts=universal?step?.participants||[]:doc.approvers;
 const resolve=(id:string)=>id==='$author'?doc.author:id;
 const running=universal?process?.state==='running'&&process.current===stepIndex:doc.status==='review';
 const started=universal?!!process:doc.iteration>0;
 const legacyDone=['approval','approved','execution','complete','archived'].includes(doc.status);
 const stopped=universal?['revision','cancelled'].includes(process?.state||'')&&step?.status!=='done':doc.status==='revision'||doc.status==='draft'&&started;
 const first=parts.find(id=>universal?!step?.decisions?.[id]:!doc.approvals[id]);
 const rows=parts.map(id=>{
  const p=person(resolve(id)),decision=step?.decisions?.[id];const accepted=universal?decision?.decision==='accept':doc.approvals[id]==='approved'||legacyDone;
  const rejected=universal?decision?.decision==='reject':doc.approvals[id]==='rejected';
  const state:ReviewState=accepted?'accepted':rejected?'rejected':stopped?'stopped':running&&(mode!=='sequential'||id===first)?'active':'waiting';
  return {id:resolve(id),name:p.name,department:p.department,state,comment:decision?.comment||'',date:decision?.createdAt||''};
 });
 const next:ProcessStep|undefined=steps[stepIndex+1];const joined=rows.length>0&&rows.every(r=>r.state==='accepted');
 const rejected=rows.some(r=>r.state==='rejected')||(universal?next?.status==='rejected':doc.approvals[doc.finalApprover]==='rejected');
 const nextState:ReviewState=universal?next?.status==='done'?'accepted':next?.status==='rejected'?'rejected':next?.status==='active'?'active':'waiting':doc.approvals[doc.finalApprover]==='rejected'?'rejected':doc.status==='approval'?'active':['approved','execution','complete','archived'].includes(doc.status)?'accepted':'waiting';
 return {rows,mode,title:universal?step?.label||'Согласование':'Согласование',author:person(doc.author).name,authorDepartment:person(doc.author).department,nextLabel:universal?(next?next.kind==='approval'?'На утверждение':next.label:'Завершение маршрута'):'На утверждение',nextPeople:universal?(next?.participants||[]).map(id=>person(resolve(id)).name).join(', '):person(doc.finalApprover).name,nextState:universal&&!next&&joined?'accepted':nextState,rejected,joined,started,iteration,historical,stepIndex};
}
