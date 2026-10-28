/** Server-owned trip state. No hotel engine, generated prices or local payment success. */
import {streamChat,requestCheckout,downloadCheckout,ChatApiError} from './api.js';

export const visibleText=value=>String(value??'').replace(/\s*\[review_id:[^\]]*\]/g,'').replace(/\b(?:CART-[a-f0-9]{32}|DEMO-[A-F0-9]{32}|[a-f0-9]{32})\b/g,'[служебный номер скрыт]');
const copy=value=>structuredClone(value);
const STORAGE_KEY='na-maldivy-api-session-v1';
const idempotency=()=>globalThis.crypto?.randomUUID?.()||'web-'+Date.now()+'-'+Math.random().toString(36).slice(2);
export const createApiModel=()=>({sessionId:null,version:0,stage:'collecting',brief:{},sources:{},needs:[],options:[],optionKind:null,selected:null,cart:null,order:null,orders:[],messages:[],question:null,profile:null,savings:null,statusText:'',busy:false,error:null,stale:false,notes:[],assumptions:[]});

export function applyAgentEvent(model,type,data){
 if(type==='brief'){
  model.brief=data.brief||{};model.sources=data.sources||{};model.needs=data.needs||[];
  if(data.changes?.length){model.selected=null;model.cart=null;model.order=null;model.stale=true;model.options=[];model.savings=null;model.profile=null;}
 }else if(type==='options'){
  model.options=Array.isArray(data.items)?data.items:[];model.optionKind=data.kind;model.assumptions=data.assumptions||[];model.notes=data.notes||[];model.stale=false;
 }else if(type==='question')model.question=data;
 else if(type==='hotel_profile')model.profile=data;
 else if(type==='date_savings')model.savings=data;
 else if(type==='status')model.statusText=data.text||'';
 else if(type==='booking'&&data.cart_id){model.cart=copy(data);model.order=null;}
 else if(type==='done'){
  if(data.session_id)model.sessionId=data.session_id;
  if(data.stage)model.stage=data.stage;
  if(Number.isInteger(data.version))model.version=data.version;
 }
 return model;
}

export class ApiTripController{
 constructor({fetchImpl=globalThis.fetch,onChange=()=>{},storage=null}={}){
  this.fetch=fetchImpl.bind(globalThis);this.onChange=onChange;this.storage=storage;this.state=createApiModel();this.abort=null;this.generation=0;this.pendingPayment=null;this.savedTravelers=null;
 }
 notify(){this.onChange(this.state);}
 remember(){try{this.storage?.setItem(STORAGE_KEY,JSON.stringify({sessionId:this.state.sessionId,version:this.state.version,options:this.state.options,optionKind:this.state.optionKind,orders:this.state.orders.map(row=>({sessionId:row.sessionId,orderId:row.order.order_id}))}));}catch{} }
 async sessionSnapshot(){
  const generation=this.generation,sessionId=this.state.sessionId;
  const current=()=>this.generation===generation&&this.state.sessionId===sessionId;
  const response=await this.fetch('/api/session/'+encodeURIComponent(sessionId),{headers:{Accept:'application/json'},cache:'no-store'});
  if(!current())return null;
  if(!response.ok)throw new ChatApiError('Диалог на сервере недоступен. Начните новую поездку.',{code:'session',status:response.status});
  const value=await response.json();if(!current())return null;
  Object.assign(this.state,{brief:value.brief||{},sources:value.sources||{},version:value.version,stage:value.stage,selected:value.selected,stale:!!value.stale});
  if(value.stale)this.state.options=[];
  if(value.booking?.cart_id&&value.stage==='checkout_pending'){
   const cart=await requestCheckout({path:'/api/checkout/carts/'+encodeURIComponent(value.booking.cart_id),sessionId,fetchImpl:this.fetch});
   if(!current())return null;this.state.cart=cart;
  }else if(value.stage!=='booked')this.state.cart=null;
  return value;
 }
 async restore(){
  const generation=this.generation;
  let saved;try{saved=JSON.parse(this.storage?.getItem(STORAGE_KEY)||'null');}catch{return;}
  if(!saved)return;
  for(const row of saved.orders||[]){
   if(this.generation!==generation)return;
   try{const order=await requestCheckout({path:'/api/checkout/orders/'+encodeURIComponent(row.orderId),sessionId:row.sessionId,fetchImpl:this.fetch});if(this.generation!==generation)return;this.state.orders.push({sessionId:row.sessionId,order});}catch{if(this.generation!==generation)return;}
  }
  if(this.generation!==generation)return;
  if(!saved.sessionId){this.notify();return;}
  this.state.sessionId=saved.sessionId;this.state.options=Array.isArray(saved.options)?saved.options:[];this.state.optionKind=saved.optionKind;
  try{const snapshot=await this.sessionSnapshot();if(this.generation!==generation||!snapshot)return;if(snapshot.version!==saved.version)this.state.options=[];this.state.messages.push({role:'assistant',text:'Параметры поездки восстановлены с сервера. Можно продолжить разговор.'});}
  catch{if(this.generation!==generation)return;this.state.sessionId=null;this.state.options=[];this.state.cart=null;this.state.messages.push({role:'assistant',text:'Сервер перезапустился: начните новый поиск. Сохранённые демо-заказы доступны в «Моих поездках».'});}
  this.notify();
 }
 async send(message){
  message=String(message||'').trim();if(!message||this.state.busy)return null;
  const generation=++this.generation;this.abort=new AbortController();this.state.busy=true;this.state.error=null;this.state.question=null;this.state.statusText='Готовим поездку';
  this.state.messages.push({role:'user',text:message});const reply={role:'assistant',text:''};this.state.messages.push(reply);this.notify();
  try{
   const result=await streamChat({message,sessionId:this.state.sessionId,signal:this.abort.signal,fetchImpl:this.fetch,
    onDelta:(_,text)=>{if(this.generation===generation){reply.text=visibleText(text);this.notify();}},
    onEvent:(type,data)=>{if(this.generation===generation){applyAgentEvent(this.state,type,data);this.notify();}}});
   if(this.generation!==generation)return null;
   if(!result.sessionId)throw new ChatApiError('Сервер не сохранил диалог. Начните новую поездку.',{code:'protocol'});
   this.state.sessionId=result.sessionId;await this.sessionSnapshot();if(this.generation!==generation)return null;
   if(!reply.text)reply.text='Сервер завершил обработку. Результат показан ниже.';
   this.remember();return result;
  }catch(error){
   if(this.generation!==generation)return null;
   this.state.error=error?.name==='AbortError'?'Ответ остановлен. Можно продолжить разговор.':visibleText(error.message||'Сервис временно недоступен.');
   reply.error=true;if(!reply.text)reply.text=this.state.error;
   throw error;
  }finally{if(this.generation===generation){this.state.busy=false;this.state.statusText='';this.abort=null;this.notify();}}
 }
 async pay({travelers,outcome,acknowledgeDemo,acknowledgeUnpriced}){
  if(this.state.busy)return null;
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
   this.pendingPayment=null;
   if(result.status==='paid_demo'&&result.is_simulated===true){
    this.state.order=result;this.state.stage='booked';this.state.cart={...this.state.cart,status:'paid_demo'};
    if(!this.state.orders.some(row=>row.order.order_id===result.order_id))this.state.orders.unshift({sessionId,order:result});
    this.remember();
   }else if(result.status==='payment_declined'){this.state.cart={...this.state.cart,status:'payment_declined'};}
   else throw new ChatApiError('Сервер не подтвердил результат демо-оплаты.',{code:'protocol'});
   return result;
  }catch(error){if(!current())return null;this.state.error=visibleText(error.message||'Оформление временно недоступно.');throw error;}
  finally{if(current()){this.state.busy=false;this.notify();}}
 }
 async download(kind,entry=null){
  const order=entry?.order||this.state.order,sessionId=entry?.sessionId||this.state.sessionId;
  if(!order?.order_id||!['voucher','calendar'].includes(kind))throw new ChatApiError('Демо-документ пока недоступен.',{code:'validation'});
  return downloadCheckout({path:'/api/checkout/orders/'+encodeURIComponent(order.order_id)+(kind==='voucher'?'/voucher':'/calendar.ics'),sessionId,fetchImpl:this.fetch});
 }
 reset(){this.generation++;this.abort?.abort();const orders=this.state.orders;this.state={...createApiModel(),orders};this.pendingPayment=null;this.savedTravelers=null;this.remember();this.notify();}
}
