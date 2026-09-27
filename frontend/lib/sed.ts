export type Person={id:string;name:string;initials:string;role:string;department:string;color:string;active?:boolean;systemRole?:string};
export let people:Person[] = [
 {id:"secretary",name:"Айна Мамедова",initials:"АМ",role:"Секретарь",department:"Канцелярия",color:"blue"},
 {id:"legal",name:"Мерет Оразов",initials:"МО",role:"Руководитель",department:"Юридический отдел",color:"purple"},
 {id:"finance",name:"Лейли Атаева",initials:"ЛА",role:"Руководитель",department:"Финансовый отдел",color:"amber"},
 {id:"it",name:"Сердар Аннаев",initials:"СА",role:"Руководитель",department:"Отдел IT",color:"teal"},
 {id:"director",name:"Довлет Керимов",initials:"ДК",role:"Директор",department:"Руководство",color:"navy"},
];
export function setPeople(value:Person[]){people=value;}
export const statusLabels:Record<string,string>={resolution:"На резолюции",signing:"На подписи",reading:"На ознакомлении",reply:"Подготовка ответа",draft:"Черновик",review:"На согласовании",revision:"На доработке",approval:"На утверждении",approved:"Утверждён",execution:"На исполнении",complete:"Исполнен",archived:"В архиве"};
export let typeLabels:Record<string,string>={incoming:"Входящий",outgoing:"Исходящий",internal:"Внутренний",contract:"Договор"};
export type ArchiveInfo={date:string;caseId:string;caseIndex:string;caseTitle:string;years:number|null;reviewAfter:string;location:string;previousStatus:string;actor:string;legacy?:boolean};
export type CaseFile={id:string;index:string;title:string;years:number;closed:boolean};
export type Doc={typeSnapshot?:DocumentType;customFields?:Record<string,string|boolean>;routePlan?:ProcessTemplate|null;process?:ProcessInstance;processHistory?:ProcessInstance[];replyDocumentId?:string;resolutions?:{text:string;actor:string;due:string;assignees:string[];controller:string;iteration:number}[];externalNumber?:string;externalDate?:string;registeredAt?:string;category?:string;tags?:string;priority?:string;caseId?:string;editors?:string[];relatedIds?:string[];archive?:ArchiveInfo|null;isPublic?:boolean;access?:string[];automaticAccess?:string[];id:string;number:string;title:string;type:string;correspondent:string;department:string;description:string;amount:string;currency:string;payment:string;date:string;due:string;status:string;author:string;approvers:string[];approvals:Record<string,string>;mode:string;iteration:number;finalApprover:string;createdAt:string;updatedAt:string;};
export type Attachment={comment?:string;processId?:string;processStepId?:string;id:string;documentId:string;name:string;groupId:string;version:number;size:number;mime:string;key:string;author:string;createdAt:string};
export type Task={controller?:string;author?:string;requiresAcceptance?:boolean;reports?:{id:string;text:string;actor:string;createdAt:string;decision?:string}[];cancelReason?:string;id:string;documentId:string;title:string;assignee:string;due:string;status:string;createdAt:string};
export type Event={iteration?:number;decision?:string;fileId?:string;fileVersion?:number;step?:string;transition?:{before?:string;after?:string};username?:string;id:string;documentId:string;actor:string;action:string;comment:string;kind:string;createdAt:string};
export type Reading={id:string;documentId:string;recipients:string[];done:string[];due:string;comment:string;author:string;createdAt:string};
export type RouteTemplate={id:string;name:string;owner:string;approvers:string[];mode:string;finalApprover:string;createdAt:string;updatedAt:string};
export type SedState={documentTypes?:DocumentType[];processTemplates?:ProcessTemplate[];cases?:CaseFile[];fileLocks?:{groupId:string;documentId:string;actor:string;username:string;baseVersion:number;expires:number}[];docxTemplates?:{id:string;name:string}[];people?:Person[];templates?:RouteTemplate[];adminAudit?:{id:string;actor:string;action:string;target:string;createdAt:string}[];documents:Doc[];tasks:Task[];events:Event[];attachments:Attachment[];readEvents:string[];readings:Reading[]};
export const person=(id:string)=>people.find(p=>p.id===id)||{id,name:id,initials:"?",role:"Сотрудник",department:"",color:"blue"};
export const dateText=(v:string)=>v?new Date(v.length===10?v+"T12:00:00":v).toLocaleDateString("ru-RU",{day:"2-digit",month:"2-digit",year:"numeric"}):"Без срока";
export const today=()=>{const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;};
export const pendingFor=(d:Doc,actor:string)=>d.status==="review"&&d.approvers.includes(actor)&&!d.approvals[actor]&&(d.mode!=="sequential"||d.approvers.find(p=>!d.approvals[p])===actor);
export const actionable=(d:Doc,actor:string)=>!!(currentStep(d)?.participants.includes(actor))||pendingFor(d,actor)||(d.status==="approval"&&d.finalApprover===actor)||(d.status==="revision"&&d.author===actor);
export function seedState():SedState {
 const now=new Date();const relative=(days:number)=>new Date(now.getTime()+days*86400000).toISOString().slice(0,10);
 const definitions=[
  ["Поставка компьютерного оборудования","contract","ИП «WEPA»","review",1,"Отдел IT","128 500"],
  ["О предоставлении квартальной отчётности","incoming","Центральный банк","review",0,"Финансовый отдел",""],
  ["Обновление системы электронного документооборота","internal","Отдел IT","execution",3,"Отдел IT",""],
  ["Техническое обслуживание серверов","contract","ИП «WEPA»","revision",-1,"Отдел IT","36 000"],
  ["О проведении внутреннего аудита","internal","Служба внутреннего аудита","approval",2,"Руководство",""],
  ["Ответ на запрос о сотрудничестве","outgoing","ООО «Аркач»","draft",4,"Канцелярия",""],
  ["Поставка офисной мебели","contract","Компания «Мебель»","review",5,"Финансовый отдел","72 000"],
  ["Регламент информационной безопасности","internal","Отдел IT","approved",6,"Отдел IT",""],
  ["О согласовании графика платежей","incoming","Компания «Огузхан»","review",-2,"Финансовый отдел",""],
  ["Установка системы видеонаблюдения","contract","ИП «WEPA»","draft",8,"Отдел IT","45 000"],
  ["Приказ о назначении ответственных лиц","internal","Руководство","complete",-3,"Канцелярия",""],
  ["Отчёт о выполнении плана за август","outgoing","Руководство","archived",-5,"Финансовый отдел",""],
 ];
 const documents:Doc[]=definitions.map((r,i):Doc=>({id:`demo-${i+1}`,number:`${{contract:"ДГ",incoming:"ВХ",outgoing:"ИСХ",internal:"ВН"}[r[1] as string]}-${String(142-i).padStart(4,"0")}`,title:r[0] as string,type:r[1] as string,correspondent:r[2] as string,status:r[3] as string,due:relative(r[4] as number),department:r[5] as string,amount:r[6] as string,currency:"TMT",payment:i===0?"Предоплата 50%. Окончательный расчёт после поставки.":"",description:i===0?"Поставка 10 рабочих станций и сетевого оборудования для обновления инфраструктуры. Необходимо согласовать технические требования, бюджет и условия поставки.":`Документ: ${r[0]}. Рассмотреть и подготовить решение в установленный срок.`,date:relative(-i-1),author:"secretary",approvers:["legal","finance","it"],approvals:i===0?{it:"approved"}:i===4?{legal:"approved",finance:"approved",it:"approved"}:{},mode:"parallel",iteration:r[3]==="draft"?0:1,finalApprover:"director",createdAt:new Date(now.getTime()-(i+1)*86400000).toISOString(),updatedAt:new Date(now.getTime()-i*7200000).toISOString()}));
 return {documents,tasks:[{id:"task-1",documentId:"demo-3",title:"Подготовить план внедрения новой СЭД",assignee:"it",due:relative(3),status:"progress",createdAt:now.toISOString()},{id:"task-2",documentId:"demo-2",title:"Собрать данные для квартальной отчётности",assignee:"finance",due:relative(0),status:"new",createdAt:now.toISOString()},{id:"task-3",documentId:"demo-4",title:"Уточнить условия технического обслуживания",assignee:"secretary",due:relative(-1),status:"new",createdAt:now.toISOString()}],events:[
 {id:"event-1",documentId:"demo-1",actor:"it",action:"Согласовал документ",comment:"Технические характеристики соответствуют требованиям отдела.",kind:"workflow",createdAt:new Date(now.getTime()-18*60000).toISOString()},
 {id:"event-2",documentId:"demo-5",actor:"finance",action:"Передан на утверждение",comment:"Все участники завершили согласование.",kind:"workflow",createdAt:new Date(now.getTime()-54*60000).toISOString()},
 {id:"event-3",documentId:"demo-4",actor:"legal",action:"Возвращён на доработку",comment:"Уточните срок реагирования на инцидент в пункте 3.2.",kind:"workflow",createdAt:new Date(now.getTime()-130*60000).toISOString()},
 {id:"event-4",documentId:"demo-2",actor:"secretary",action:"Отправлен на согласование",comment:"Пожалуйста, проверьте данные отчётности.",kind:"workflow",createdAt:new Date(now.getTime()-190*60000).toISOString()},
 ],attachments:[],readEvents:[],readings:[{id:"reading-1",documentId:"demo-8",recipients:["legal","finance","it"],done:["it"],due:relative(4),comment:"Прошу ознакомиться с новым регламентом.",author:"secretary",createdAt:now.toISOString()}]};
}

export type TypeField={key:string;label:string;kind:string;required:boolean;options:string[]};
export type DocumentType={id:string;name:string;prefix:string;family:string;active:boolean;version:number;defaultRouteId:string;fields:TypeField[]};
export type ProcessStep={id:string;kind:string;label:string;participants:string[];mode:string;controller:string;status?:string;decisions?:Record<string,{decision:string;comment:string;createdAt:string;fileId?:string}>;taskIds?:string[];replyDocumentId?:string;dispatch?:{date:string;channel:string;recipient:string;comment:string}};
export type ProcessTemplate={id:string;name:string;active:boolean;version:number;steps:ProcessStep[]};
export type ProcessInstance={id:string;name:string;templateId:string;templateVersion:number;state:string;current:number;steps:ProcessStep[]};
export const stepLabels:Record<string,string>={review:"Согласование",approval:"Утверждение",resolution:"Резолюция",execution:"Исполнение",signature:"Регистрация подписи",acknowledge:"Ознакомление",reply:"Ответ / отправка",archive:"Архив"};
export const currentStep=(d:Doc)=>d.process?.state==="running"?d.process.steps[d.process.current]:undefined;
export const docTypeName=(d:Doc)=>d.typeSnapshot?.name||typeLabels[d.type]||d.type;
export const docFamily=(d:Doc)=>d.typeSnapshot?.family||d.type;
export function setTypes(types:DocumentType[]){typeLabels=Object.fromEntries(types.map(t=>[t.id,t.name]));}
