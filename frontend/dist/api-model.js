/** Server-owned trip state. No hotel engine, generated prices or local payment success. */
import {streamChat,requestCheckout,downloadCheckout,checkAgentHealth,ChatApiError} from './api.js';

export const visibleText=value=>String(value??'').replace(/\s*\[review_id:[^\]]*\]/g,'').replace(/\b(?:CART-[a-f0-9]{32}|DEMO-[A-F0-9]{32}|[a-f0-9]{32})\b/g,'[служебный номер скрыт]');
const copy=value=>structuredClone(value);
const STORAGE_KEY='na-maldivy-api-session-v1';
const idempotency=()=>globalThis.crypto?.randomUUID?.()||'web-'+Date.now()+'-'+Math.random().toString(36).slice(2);
export const createApiModel=()=>({sessionId:null,version:0,stage:'collecting',brief:{},sources:{},needs:[],options:[],optionKind:null,selected:null,cart:null,order:null,currentOrderId:null,orders:[],messages:[],question:null,profile:null,savings:null,statusText:'',busy:false,error:null,stale:false,notes:[],assumptions:[],draft:'',pendingRequest:null,syncNeeded:false,sessionLost:false,connection:'connecting',health:null,storageWarning:null});
const safeId=value=>typeof value==='string'&&/^[A-Za-z0-9_-]{1,64}$/.test(value);
const safeCheckoutId=value=>typeof value==='string'&&/^[A-Za-z0-9_-]{1,128}$/.test(value);
const stages=['collecting','destinations_shown','packages_shown','trains_shown','awaiting_confirmation','checkout_pending','booked'];
function checkedOrder(value,sessionId,cartId=null){
 if(!value||value.status!=='paid_demo'||value.is_simulated!==true||!safeCheckoutId(value.order_id)||!safeCheckoutId(value.cart_id)||(cartId&&value.cart_id!==cartId)||value.session_id!==sessionId||!value.package?.hotel||typeof value.package.hotel.name!=='string'||typeof value.package.hotel.check_in!=='string'||typeof value.package.hotel.check_out!=='string')throw new ChatApiError('Сервер не подтвердил результат демо-оплаты.',{code:'protocol'});
 return value;
}
const tripKeys=['sessionId','version','stage','brief','sources','needs','options','optionKind','selected','cart','order','currentOrderId','question','profile','savings','stale','notes','assumptions'];
const tripCopy=state=>Object.fromEntries(tripKeys.map(key=>[key,copy(state[key])]));
function storedTrip(state){
 const result=tripCopy(state);result.currentOrderId=state.order?.order_id||state.currentOrderId||null;result.order=null;
 if(result.cart){const {cart_id,status,version,option,is_simulated,expected_travelers}=result.cart;result.cart={cart_id,status,version,option,is_simulated,expected_travelers};}
 return result;
}
function validMessages(value){
 if(!Array.isArray(value)||value.length>5000)return [];
 return value.filter(row=>row&&['user','assistant'].includes(row.role)&&typeof row.text==='string'&&row.text.length<=128000).map(row=>({role:row.role,text:row.text,...(row.error?{error:true}:{}),...(row.interrupted?{interrupted:true}:{})}));
}
function restoredTrip(value){
 const result=tripCopy(createApiModel()),record=value&&typeof value==='object'&&!Array.isArray(value)?value:{};
 result.sessionId=safeId(record.sessionId)?record.sessionId:null;
 result.currentOrderId=safeCheckoutId(record.currentOrderId)?record.currentOrderId:null;
 result.version=Number.isInteger(record.version)&&record.version>=0?record.version:0;
 if(stages.includes(record.stage))result.stage=record.stage;
 for(const key of ['brief','sources'])if(record[key]&&typeof record[key]==='object'&&!Array.isArray(record[key]))result[key]=copy(record[key]);
 if(result.brief.children_ages&&!Array.isArray(result.brief.children_ages))result.brief.children_ages=[];
 if(result.brief.start_date&&typeof result.brief.start_date!=='string')delete result.brief.start_date;
 result.needs=Array.isArray(record.needs)?record.needs.filter(row=>row&&typeof row.label==='string'):[];
 result.notes=Array.isArray(record.notes)?record.notes.filter(row=>typeof row==='string'):[];
 result.assumptions=Array.isArray(record.assumptions)?record.assumptions.filter(row=>row&&typeof row.text==='string'):[];
 result.optionKind=['packages','destinations','trains'].includes(record.optionKind)?record.optionKind:null;
 const options=Array.isArray(record.options)?record.options:[];
 result.options=options.filter(row=>{
  if(!row||typeof row!=='object')return false;
  if(result.optionKind==='packages')return Number.isInteger(row.index)&&row.hotel&&typeof row.hotel.name==='string'&&typeof row.hotel.check_in==='string'&&typeof row.hotel.check_out==='string'&&row.outbound&&row.inbound&&typeof row.outbound.departure_at==='string'&&typeof row.inbound.departure_at==='string'&&(!row.issues||(Array.isArray(row.issues)&&row.issues.every(issue=>issue&&typeof issue.message==='string')))&&(!row.unpriced_components||Array.isArray(row.unpriced_components));
  if(result.optionKind==='destinations')return typeof row.name==='string'&&(!row.why||Array.isArray(row.why));
  return result.optionKind==='trains';
 });
 result.selected=Number.isInteger(record.selected)?record.selected:null;result.stale=!!record.stale;
 if(record.cart&&typeof record.cart.cart_id==='string'&&/^[A-Za-z0-9_-]{1,128}$/.test(record.cart.cart_id)){const {cart_id,status,version,option,is_simulated,expected_travelers}=record.cart;result.cart={cart_id,status,version,option,is_simulated,expected_travelers};}
 if(record.question&&typeof record.question==='object')result.question={...record.question,quick_replies:Array.isArray(record.question.quick_replies)?record.question.quick_replies.filter(row=>typeof row==='string'):[]};
 if(record.profile&&Array.isArray(record.profile.evidence)&&record.profile.evidence.every(row=>row&&typeof row.quote==='string'))result.profile=copy(record.profile);
 if(record.savings&&Array.isArray(record.savings.checked_dates)&&record.savings.checked_dates.every(row=>row&&typeof row.status==='string')&&(!record.savings.suggestions||(Array.isArray(record.savings.suggestions)&&record.savings.suggestions.every(row=>row&&typeof row.suggested_departure==='string'))))result.savings=copy(record.savings);
 return result;
}
function pendingFrom(value,messages){
 if(!value||!safeId(value.id)||typeof value.message!=='string'||!value.message.trim()||value.message.length>4000||!(value.sessionId===null||safeId(value.sessionId)))return null;
 if(!Number.isInteger(value.userIndex)||!Number.isInteger(value.replyIndex)||value.replyIndex!==value.userIndex+1||messages[value.userIndex]?.role!=='user'||messages[value.userIndex]?.text!==value.message||messages[value.replyIndex]?.role!=='assistant')return null;
 return {id:value.id,message:value.message,sessionId:value.sessionId,userIndex:value.userIndex,replyIndex:value.replyIndex,baseTrip:restoredTrip(value.baseTrip)};
}

export function applyAgentEvent(model,type,data){
 if(type==='brief'){
  model.brief=data.brief||{};model.sources=data.sources||{};model.needs=data.needs||[];
  if(data.changes?.length){model.selected=null;model.cart=null;model.order=null;model.currentOrderId=null;model.stale=true;model.options=[];model.savings=null;model.profile=null;}
 }else if(type==='options'){
  model.options=Array.isArray(data.items)?data.items:[];model.optionKind=data.kind;model.assumptions=data.assumptions||[];model.notes=data.notes||[];model.stale=false;
 }else if(type==='question')model.question=data;
 else if(type==='hotel_profile')model.profile=data;
 else if(type==='date_savings')model.savings=data;
 else if(type==='status')model.statusText=data.text||'';
 else if(type==='booking'&&data.cart_id){model.cart=copy(data);model.order=null;model.currentOrderId=null;}
 else if(type==='done'){
  if(data.session_id)model.sessionId=data.session_id;
  if(data.stage)model.stage=data.stage;
  if(Number.isInteger(data.version))model.version=data.version;
 }
 return model;
}

export class ApiTripController{
 constructor({fetchImpl=globalThis.fetch,onChange=()=>{},storage=null}={}){
  this.fetch=fetchImpl.bind(globalThis);this.onChange=onChange;this.storage=storage;this.state=createApiModel();this.abort=null;this.generation=0;this.pendingPayment=null;this.savedTravelers=null;this.orderRefs=[];this.reconnectPromise=null;this.probeEpoch=0;
 }
 notify(){this.remember();this.onChange(this.state);}
 remember(){
  if(!this.storage)return;
  try{
   const refs=[...this.orderRefs,...this.state.orders.map(row=>({sessionId:row.sessionId,orderId:row.order.order_id}))];
   this.orderRefs=[...new Map(refs.map(row=>[row.orderId,row])).values()];
   const pending=this.state.pendingRequest;
   const saved={schemaVersion:2,...storedTrip(this.state),messages:this.state.messages,draft:this.state.draft,pendingRequest:pending?{...pending,baseTrip:pending.baseTrip?storedTrip(pending.baseTrip):null}:null,syncNeeded:this.state.syncNeeded,sessionLost:this.state.sessionLost,orders:this.orderRefs};
   const payload=JSON.stringify(saved);if(payload.length>4000000)throw new Error('Storage size');
   this.storage.setItem(STORAGE_KEY,payload);this.state.storageWarning=null;
  }catch{this.state.storageWarning='Не удалось сохранить переписку в браузере. Пока вкладка открыта, сообщения остаются на странице.';}
 }
 setDraft(value){this.state.draft=String(value||'').slice(0,4000);this.remember();}
 keepOrder(order,sessionId,prepend=true){
  const entry=this.state.orders.find(row=>row.sessionId===sessionId&&row.order.order_id===order.order_id);
  if(entry)entry.order=order;else this.state.orders[prepend?'unshift':'push']({sessionId,order});
 }
 async sessionSnapshot(){
  const generation=this.generation,sessionId=this.state.sessionId;
  const current=()=>this.generation===generation&&this.state.sessionId===sessionId;
  if(!safeId(sessionId))return null;
  const response=await this.fetch('/api/session/'+encodeURIComponent(sessionId),{headers:{Accept:'application/json'},cache:'no-store',credentials:'same-origin',redirect:'error',signal:AbortSignal.timeout(5000)});
  if(!current())return null;
  if(!response.ok)throw new ChatApiError(response.status===404?'Сервер перезапустился. Переписка сохранена; начните новый поиск.':'Не удалось обновить состояние поездки. Проверьте связь.',{code:'session',status:response.status});
  const value=await response.json();if(!current())return null;
  if(!value||typeof value!=='object'||Array.isArray(value)||(value.session_id!==undefined&&value.session_id!==sessionId)||!Number.isInteger(value.version)||value.version<this.state.version||!stages.includes(value.stage)||(value.brief!==undefined&&(!value.brief||typeof value.brief!=='object'||Array.isArray(value.brief))))throw new ChatApiError('Не удалось проверить состояние поездки на сервере.',{code:'protocol'});
  let cart=null,order=null;
  if(value.booking?.cart_id&&value.stage==='checkout_pending'){
   cart=await requestCheckout({path:'/api/checkout/carts/'+encodeURIComponent(value.booking.cart_id),sessionId,fetchImpl:this.fetch});
   if(!current())return null;
   if(cart.cart_id!==value.booking.cart_id||(cart.session_id!==undefined&&cart.session_id!==sessionId))throw new ChatApiError('Сервер вернул другую корзину поездки.',{code:'protocol'});
  }else if(value.stage==='booked'&&value.booking?.order_id){
   const orderId=value.booking.order_id;
   if(!safeCheckoutId(orderId))throw new ChatApiError('Сервер не подтвердил демо-заказ.',{code:'protocol'});
   const saved=this.state.orders.find(row=>row.sessionId===sessionId&&row.order.order_id===orderId);
   order=checkedOrder(saved?.order||await requestCheckout({path:'/api/checkout/orders/'+encodeURIComponent(orderId),sessionId,fetchImpl:this.fetch}),sessionId,value.booking.cart_id);
   if(!current())return null;
   if(order.order_id!==orderId)throw new ChatApiError('Сервер вернул другой демо-заказ.',{code:'protocol'});
  }
  if(!current())return null;
  if(value.stale||value.version!==this.state.version)this.state.options=[];
  Object.assign(this.state,{brief:value.brief||{},sources:value.sources||{},version:value.version,stage:value.stage,selected:value.selected,stale:!!value.stale,cart,order,currentOrderId:order?.order_id||null});
  if(order)this.keepOrder(order,sessionId);
  return value;
 }
 markSessionLost(){this.state.sessionLost=true;this.state.syncNeeded=false;this.state.stale=true;this.state.error=this.state.order?'Поисковая сессия завершилась. Демо-заказ и документы сохранены; для продолжения начните новый поиск.':'Восстановить этот ответ уже нельзя. Переписка сохранена; начните новый поиск.';}
 async reconnect(){
  if(this.reconnectPromise)return this.reconnectPromise;
  const generation=this.generation,epoch=++this.probeEpoch;
  const current=()=>this.generation===generation&&this.probeEpoch===epoch;
  this.state.connection='connecting';this.notify();
  const work=(async()=>{
   try{
    const health=await checkAgentHealth({fetchImpl:this.fetch,signal:AbortSignal.timeout(5000)});
    if(!current())return null;this.state.health=health;this.state.connection='online';
    if(this.state.syncNeeded&&!this.state.pendingRequest&&!this.state.busy&&this.state.sessionId){
     try{await this.sessionSnapshot();if(!current())return null;this.state.syncNeeded=false;this.state.error=null;}
     catch(error){if(!current())return null;if(error.status===404)this.markSessionLost();else this.state.error='Ответ сохранён. Пока не удалось обновить состояние поездки; проверьте связь ещё раз.';}
    }
    return health;
   }catch(error){if(current())this.state.connection=[401,403].includes(error.status)?'auth':'offline';return null;}
   finally{if(current()){this.reconnectPromise=null;this.notify();}}
  })();
  this.reconnectPromise=work;return work;
 }
 async restore(){
  const generation=this.generation;
  let saved;try{const raw=this.storage?.getItem(STORAGE_KEY)||'null';if(raw.length>4000000)return;saved=JSON.parse(raw);}catch{return;}
  if(!saved||typeof saved!=='object')return;
  if(saved.schemaVersion===2){
   Object.assign(this.state,restoredTrip(saved));
   this.state.messages=validMessages(saved.messages);this.state.draft=typeof saved.draft==='string'?saved.draft.slice(0,4000):'';
   this.state.pendingRequest=pendingFrom(saved.pendingRequest,this.state.messages);this.state.sessionLost=!!saved.sessionLost;this.state.syncNeeded=!!saved.syncNeeded;
   if(saved.pendingRequest&&!this.state.pendingRequest)this.markSessionLost();
   if(this.state.pendingRequest){this.state.messages[this.state.pendingRequest.replyIndex].interrupted=true;this.state.error='Ответ прервался. Переписка сохранена; можно повторить сообщение.';}
  }else{this.state.sessionId=safeId(saved.sessionId)?saved.sessionId:null;this.state.options=Array.isArray(saved.options)?saved.options:[];this.state.optionKind=saved.optionKind;}
  this.orderRefs=Array.isArray(saved.orders)?saved.orders.filter(row=>safeId(row?.sessionId)&&typeof row.orderId==='string'&&/^[A-Za-z0-9_-]{1,128}$/.test(row.orderId)).slice(0,1000):[];
  this.notify();
  for(const row of this.orderRefs){
   if(this.generation!==generation)return;
   try{const order=checkedOrder(await requestCheckout({path:'/api/checkout/orders/'+encodeURIComponent(row.orderId),sessionId:row.sessionId,fetchImpl:this.fetch}),row.sessionId);if(this.generation!==generation)return;if(order.order_id!==row.orderId)continue;this.keepOrder(order,row.sessionId,false);}catch{if(this.generation!==generation)return;}
  }
  if(this.generation!==generation)return;
  const ownedOrders=this.state.orders.filter(row=>row.sessionId===this.state.sessionId);
  const paid=this.state.currentOrderId?ownedOrders.find(row=>row.order.order_id===this.state.currentOrderId):this.state.stage==='booked'?(this.state.cart?.cart_id?ownedOrders.find(row=>row.order.cart_id===this.state.cart.cart_id):ownedOrders.length===1?ownedOrders[0]:null):null;
  if(paid){this.state.order=paid.order;this.state.currentOrderId=paid.order.order_id;}
  if(this.state.sessionLost)this.markSessionLost();
  if(!this.state.sessionId||this.state.pendingRequest||this.state.sessionLost){this.notify();return;}
  try{const snapshot=await this.sessionSnapshot();if(this.generation!==generation||!snapshot)return;if(snapshot.version!==saved.version)this.state.options=[];this.state.syncNeeded=false;if(!this.state.messages.length)this.state.messages.push({role:'assistant',text:'Параметры поездки восстановлены с сервера. Можно продолжить разговор.'});}
  catch(error){if(this.generation!==generation)return;if(error.status===404)this.markSessionLost();else{this.state.syncNeeded=true;this.state.connection='offline';this.state.error='Переписка сохранена. Связь с сервером временно недоступна.';}}
  this.notify();
 }
 async send(message){
  message=String(message||'').trim();if(!message||this.state.busy)return null;
  if(this.state.pendingRequest||this.state.syncNeeded||this.state.sessionLost)throw new ChatApiError('Сначала восстановите предыдущий ответ или начните новый поиск.',{code:'recovery'});
  if(message.length>4000)throw new ChatApiError('Напишите сообщение длиной до 4000 символов.',{code:'validation'});
  const baseTrip=tripCopy(this.state),userIndex=this.state.messages.length;
  this.state.messages.push({role:'user',text:message},{role:'assistant',text:''});this.state.draft='';
  this.state.pendingRequest={id:idempotency(),message,sessionId:this.state.sessionId,userIndex,replyIndex:userIndex+1,baseTrip};
  return this.transmit(false);
 }
 async retry(){if(this.state.busy||!this.state.pendingRequest||this.state.sessionLost)return null;return this.transmit(true);}
 async transmit(retry){
  const pending=this.state.pendingRequest;if(!pending)return null;
  const generation=++this.generation;this.probeEpoch++;this.reconnectPromise=null;this.abort=new AbortController();this.state.busy=true;this.state.error=null;this.state.question=null;this.state.statusText=retry?'Восстанавливаем ответ':'Готовим поездку';
  const reply=this.state.messages[pending.replyIndex],previousText=reply.text;reply.error=false;reply.interrupted=false;this.notify();let receivedDone=false;
  try{
   const result=await streamChat({message:pending.message,sessionId:pending.sessionId,requestId:pending.id,retry,signal:this.abort.signal,fetchImpl:this.fetch,
    onDelta:(_,text)=>{if(this.generation===generation){const next=visibleText(text);reply.text=retry&&previousText.startsWith(next)?previousText:next;this.notify();}},
    onEvent:(type,data)=>{if(this.generation===generation){if(type==='done'&&(!safeId(data.session_id)||(pending.sessionId&&data.session_id!==pending.sessionId)||!Number.isInteger(data.version)||data.version<(pending.baseTrip?.version||0)||!stages.includes(data.stage)))return;applyAgentEvent(this.state,type,data);if(type==='done'){receivedDone=true;this.state.pendingRequest=null;this.state.syncNeeded=true;}this.notify();}}});
   if(this.generation!==generation)return null;
   if(!receivedDone||!safeId(result.sessionId))throw new ChatApiError('Сервер не подтвердил завершение ответа. Можно повторить сообщение.',{code:'protocol'});
   this.state.sessionId=result.sessionId;this.state.pendingRequest=null;this.state.connection='online';
   if(!reply.text)reply.text='Сервер завершил обработку. Результат показан ниже.';
   try{await this.sessionSnapshot();if(this.generation!==generation)return null;this.state.syncNeeded=false;}
   catch(error){if(this.generation!==generation)return null;if(error.status===404)this.markSessionLost();else this.state.error='Ответ сохранён. Не удалось обновить состояние поездки; проверьте связь.';}
   this.remember();return result;
  }catch(error){
   if(this.generation!==generation)return null;
   const terminal=error.code==='agent'||[409,410,422].includes(error.status);
   if(!receivedDone&&terminal){if(pending.baseTrip)Object.assign(this.state,copy(pending.baseTrip));this.state.pendingRequest=null;if(error.status===410)this.markSessionLost();else this.state.syncNeeded=!!this.state.sessionId;}
   this.state.error=error?.name==='AbortError'?'Получение ответа остановлено. Можно получить его повторно.':visibleText(error.message||'Связь прервалась. Можно повторить сообщение.');
   if(!receivedDone&&!terminal){this.state.pendingRequest=pending;reply.interrupted=true;}
   reply.error=true;if(!reply.text)reply.text=previousText||'';
   if(error.code==='network'||error.code==='incomplete')this.state.connection='offline';else if([401,403].includes(error.status))this.state.connection='auth';
   throw error;
  }finally{if(this.generation===generation){this.state.busy=false;this.state.statusText='';this.abort=null;this.notify();}}
 }
 restartSession(){const {messages,orders,draft}=this.state;this.reset();this.state.messages=messages;this.state.draft=draft;this.state.orders=orders;this.state.messages.push({role:'assistant',text:'История сохранена. Начнём новый поиск: расскажите, куда и когда хотите поехать.'});this.notify();}
 async pay({travelers,outcome,acknowledgeDemo,acknowledgeUnpriced}){
  if(this.state.busy)return null;
  if(this.state.pendingRequest||this.state.syncNeeded||this.state.sessionLost)throw new ChatApiError('Сначала восстановите ответ и актуальные условия поездки.',{code:'recovery'});
  if(!acknowledgeDemo||!acknowledgeUnpriced)throw new ChatApiError('Подтвердите демо-режим и неоценённые дополнительные расходы.',{code:'validation'});
  if(!this.state.cart?.cart_id||!this.state.sessionId)throw new ChatApiError('Сначала подтвердите вариант поездки.',{code:'validation'});
  if(!['approved','declined'].includes(outcome))throw new ChatApiError('Выберите тестовый исход оплаты.',{code:'validation'});
  const generation=this.generation,sessionId=this.state.sessionId;
  const current=()=>this.generation===generation&&this.state.sessionId===sessionId;
  const path='/api/checkout/carts/'+encodeURIComponent(this.state.cart.cart_id),signature=path+'|'+JSON.stringify(travelers);
  this.state.busy=true;this.state.error=null;this.notify();
  try{
   if(this.savedTravelers!==signature){const cart=await requestCheckout({path:path+'/travelers',method:'PUT',body:{travelers},sessionId,fetchImpl:this.fetch});if(!current())return null;this.state.cart=cart;this.savedTravelers=signature;}
   if(!this.pendingPayment||this.pendingPayment.path!==path||this.pendingPayment.outcome!==outcome)this.pendingPayment={path,outcome,key:idempotency()};
   const result=await requestCheckout({path:path+'/pay',method:'POST',body:{outcome,idempotency_key:this.pendingPayment.key,acknowledge_demo:true,acknowledge_unpriced:true},sessionId,fetchImpl:this.fetch});
   if(!current())return null;
   if(result.status==='paid_demo'&&result.is_simulated===true){
    checkedOrder(result,sessionId,this.state.cart.cart_id);
    this.state.order=result;this.state.currentOrderId=result.order_id;this.state.stage='booked';this.state.cart={...this.state.cart,status:'paid_demo'};
    this.keepOrder(result,sessionId);
    this.remember();
   }else if(result.status==='payment_declined'&&result.is_simulated===true&&result.cart_id===this.state.cart.cart_id){this.state.cart={...this.state.cart,status:'payment_declined'};}
   else throw new ChatApiError('Сервер не подтвердил результат демо-оплаты.',{code:'protocol'});
   this.pendingPayment=null;
   return result;
  }catch(error){if(!current())return null;this.state.error=visibleText(error.message||'Оформление временно недоступно.');throw error;}
  finally{if(current()){this.state.busy=false;this.notify();}}
 }
 async download(kind,entry=null){
  const order=entry?.order||this.state.order,sessionId=entry?.sessionId||this.state.sessionId;
  if(!order?.order_id||!['voucher','calendar'].includes(kind))throw new ChatApiError('Демо-документ пока недоступен.',{code:'validation'});
  return downloadCheckout({path:'/api/checkout/orders/'+encodeURIComponent(order.order_id)+(kind==='voucher'?'/voucher':'/calendar.ics'),sessionId,fetchImpl:this.fetch});
 }
 reset(){this.generation++;this.probeEpoch++;this.reconnectPromise=null;this.abort?.abort();const {orders,health,connection}=this.state;this.state={...createApiModel(),orders,health,connection};this.pendingPayment=null;this.savedTravelers=null;this.remember();this.notify();}
}
