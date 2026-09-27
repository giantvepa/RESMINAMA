// Run npm run test:approval. No browser or application database is used.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';

const root=fileURLToPath(new URL('..',import.meta.url));
const server=await createServer({root,configFile:resolve(root,'vite.config.ts'),server:{middlewareMode:true,hmr:false},logLevel:'error'});
let count=0;
const check=(name,fn)=>{fn();count++;console.log('PASS '+name);};
try{
 const {seedState}=await server.ssrLoadModule('/lib/sed.ts');
 const {approvalModel,defaultReviewIndex}=await server.ssrLoadModule('/lib/approval.ts');
 const {ApprovalDiagram,ApprovalView}=await server.ssrLoadModule('/app/approval-view.tsx');
 const {Context}=await server.ssrLoadModule('/app/workspace.tsx');
 const state=seedState();const base=state.documents[0];
 const fixture=(mode='parallel')=>({...structuredClone(base),approvals:{},process:{id:'process-1',name:'Договор',templateId:'contract',templateVersion:1,state:'running',current:0,steps:[{id:'review',kind:'review',label:'Юрист, Финансы и IT',participants:['legal','finance','it'],controller:'secretary',mode,status:'active',decisions:{}},{id:'approval',kind:'approval',label:'Утверждение',participants:['director'],controller:'secretary',mode:'parallel',status:'waiting',decisions:{}}]}});
 const model=d=>approvalModel(d,d.process,defaultReviewIndex(d,d.process));
 const accept=(d,id)=>d.process.steps[0].decisions[id]={decision:'accept',comment:'Проверено',createdAt:'2026-09-26T10:00:00Z'};
 const render=(d,actor='legal',admin=false)=>renderToStaticMarkup(React.createElement(Context.Provider,{value:{data:{...state,documents:[d]},actor,session:{username:actor,actor,canSwitch:admin},act:async()=>null,busy:false,open:()=>{}}},React.createElement(ApprovalView,{doc:d,onRoute:()=>{}})));
 const hasButton=(markup,label)=>new RegExp('<button[^>]*>(?:(?!</button>)[\\s\\S])*?'+label+'</button>').test(markup);
 check('parallel branches remain open until every reviewer accepts',()=>{const d=fixture();assert.deepEqual(model(d).rows.map(r=>r.state),['active','active','active']);accept(d,'legal');assert.equal(model(d).joined,false);assert.deepEqual(model(d).rows.map(r=>r.state),['accepted','active','active']);});
 check('sequential queue enables only its first pending reviewer',()=>{const d=fixture('sequential');accept(d,'legal');assert.deepEqual(model(d).rows.map(r=>r.state),['accepted','active','waiting']);assert.equal(hasButton(render(d,'it'),'Согласовать'),false);assert.equal(hasButton(render(d,'finance'),'Согласовать'),true);});
 check('all decisions lead to the assigned approver',()=>{const d=fixture();for(const id of ['legal','finance','it'])accept(d,id);d.process.steps[0].status='done';d.process.current=1;d.process.steps[1].status='active';d.status='approval';assert.equal(model(d).joined,true);assert.equal(model(d).nextState,'active');assert.equal(hasButton(render(d,'director'),'Утвердить'),true);assert.equal(hasButton(render(d,'legal'),'Утвердить'),false);});
 check('rejection stops pending branches while retaining accepted decisions',()=>{const d=fixture();accept(d,'legal');d.process.steps[0].decisions.finance={decision:'reject',comment:'Уточнить бюджет',createdAt:'2026-09-26T10:05:00Z'};d.process.steps[0].status='rejected';d.process.state='revision';d.status='revision';assert.deepEqual(model(d).rows.map(r=>r.state),['accepted','rejected','stopped']);assert.equal(model(d).rejected,true);assert.equal(hasButton(render(d,'it'),'Согласовать'),false);});
 check('previous iteration keeps its own decisions',()=>{const old=fixture().process;old.steps[0].decisions.legal={decision:'reject',comment:'Старая редакция',createdAt:'2026-09-25T09:00:00Z'};old.state='revision';const d=fixture();d.iteration=2;d.processHistory=[old];const previous=approvalModel(d,old,0,true,1);assert.equal(previous.rows[0].comment,'Старая редакция');assert.equal(previous.historical,true);assert.equal(model(d).rows[0].state,'active');});
 check('archived document offers no process mutation controls',()=>{const d=fixture();d.status='archived';d.process.state='completed';const html=render(d,'secretary',true);for(const label of ['Согласовать','Отклонить','Изменить участников','Отменить процесс','Редактировать карточку'])assert.equal(hasButton(html,label),false,label);assert.ok(html.includes('только для чтения'));});
 check('legacy routes and approver rejection are represented',()=>{const d={...structuredClone(base),status:'revision',approvals:{legal:'approved',finance:'approved',it:'approved',director:'rejected'}};assert.equal(approvalModel(d).rejected,true);d.status='review';d.approvals={};assert.equal(hasButton(render(d,'legal'),'Согласовать'),true);assert.equal(hasButton(render(d,'finance',true),'Отменить процесс'),false);});
 check('author can change participants but cannot decide for another reviewer',()=>{const d=fixture();const html=render(d,'secretary');assert.equal(hasButton(html,'Изменить участников'),true);assert.equal(hasButton(html,'Отменить процесс'),true);assert.equal(hasButton(html,'Согласовать'),false);});
 check('multiple review stages and non-review routes select correctly',()=>{const d=fixture();d.process.steps.push({...structuredClone(d.process.steps[0]),id:'second-review'});d.process.current=2;assert.equal(defaultReviewIndex(d,d.process),2);d.process.steps=[{...d.process.steps[0],kind:'resolution'}];d.process.current=0;assert.equal(defaultReviewIndex(d,d.process),-1);assert.ok(render(d).includes('нет этапа согласования'));});
 check('SVG contains accessible reviewer nodes and both outcome branches',()=>{const markup=renderToStaticMarkup(React.createElement(ApprovalDiagram,{model:model(fixture())}));assert.equal((markup.match(/role="button"/g)||[]).length,3);assert.ok(markup.includes('На доработку'));assert.ok(markup.includes('На утверждение'));assert.ok(markup.includes('Все согласовали'));assert.ok(!markup.includes('undefined'));});
 if(process.env.RESMINAMA_APPROVAL_RENDER_DIR){
  const out=process.env.RESMINAMA_APPROVAL_RENDER_DIR;mkdirSync(out,{recursive:true});
  for(const mode of ['parallel','sequential']){
   const d=fixture(mode);accept(d,'it');if(mode==='sequential'){d.process.steps[0].decisions={};accept(d,'legal');}
   const markup=renderToStaticMarkup(React.createElement(ApprovalDiagram,{model:model(d),selected:mode==='parallel'?'legal':'finance'}));
   writeFileSync(resolve(out,mode+'.svg'),markup.match(/<svg[\s\S]*<\/svg>/)[0]);
  }
 }
 console.log(`${count} approval checks passed`);
}finally{await server.close();}
