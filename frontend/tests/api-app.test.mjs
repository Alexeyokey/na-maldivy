import test from 'node:test';
import assert from 'node:assert/strict';
import {mountApiApp} from '../dist/api-app.js';

const sid='durable-ui-session';
const order={order_id:'DEMO-durable-ui',cart_id:'CART-durable-ui',session_id:sid,status:'paid_demo',is_simulated:true,package:{hotel:{name:'Durable Demo Hotel',check_in:'2026-11-18',check_out:'2026-11-25'},known_total_rub:80000}};
const until=async predicate=>{
 const deadline=Date.now()+2000;
 while(!predicate()){
  assert.ok(Date.now()<deadline,'The mounted app did not finish restoring its durable order');
  await new Promise(resolve=>setImmediate(resolve));
 }
};

test('The mounted chat keeps its paid-order sidebar and document dialog after the in-memory session expires',async t=>{
 let stored=JSON.stringify({schemaVersion:2,sessionId:sid,version:1,stage:'booked',currentOrderId:order.order_id,orders:[{sessionId:sid,orderId:order.order_id}],messages:[{role:'user',text:'**Подтверждаю** <img src=x onerror=alert(1)>'},{role:'assistant',text:'Сравним варианты.\n\n1. **JEN** — поездка [review_id:rv_001]\n\n2. **Villa Nautica** — второй вариант.\n\nУсловия нужно уточнить.'}]});
 const storage={getItem:()=>stored,setItem:(_,value)=>stored=value},calls=[];
 const document=Object.assign(new EventTarget(),{visibilityState:'visible',activeElement:null});
 const window=new EventTarget(),input={value:'',selectionStart:0,selectionEnd:0};
 const area={scrollTop:0,scrollHeight:100,clientHeight:100},sidebar={classList:{contains:()=>false}};
 const root=Object.assign(new EventTarget(),{innerHTML:'',querySelector:selector=>({'#api-chat-input':input,'.chat-scroll':area,'.sidebar':sidebar}[selector]||null)});
 const modal=Object.assign(new EventTarget(),{innerHTML:'',open:false,setAttribute(){},showModal(){this.open=true;},close(){this.open=false;}});
 const globals={document,window,sessionStorage:storage,fetch:async(path,init={})=>{
  calls.push({path,method:init.method||'GET'});
  if(path==='/health')return Response.json({status:'ok',llm_provider:'fake'});
  if(path==='/api/session/'+sid)return Response.json({detail:'Expired session'},{status:404});
  if(path==='/api/checkout/orders/'+order.order_id)return Response.json(order);
  throw new Error('Unexpected route');
 }};
 const original=Object.fromEntries(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const [key,value] of Object.entries(globals))Object.defineProperty(globalThis,key,{value,writable:true,configurable:true});
 let controller;
 t.after(()=>{
  controller?.dispose();
  for(const [key,descriptor] of Object.entries(original))if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];
 });
 const click=(action,extra={})=>{
  const target={dataset:{apiAction:action,...extra},closest(){return this;}};
  const event=new Event('click');Object.defineProperty(event,'target',{value:target});document.dispatchEvent(event);
 };
 controller=mountApiApp({root,modal,icon:()=>'',config:{}});
 await until(()=>controller.state.sessionLost&&controller.state.order&&controller.state.connection==='online');
 assert.match(root.innerHTML,/<strong>JEN<\/strong>/);
 assert.match(root.innerHTML,/<ol\b/);assert.match(root.innerHTML,/<strong>Villa Nautica<\/strong>/);
 assert.match(root.innerHTML,/\*\*Подтверждаю\*\* &lt;img src=x onerror=alert\(1\)&gt;/,'User input remains literal escaped text');
 assert.doesNotMatch(root.innerHTML,/rv_001|<img src=x/,'Formatting cannot expose source IDs or execute message HTML');
 assert.match(root.innerHTML,/Демо-заказ и документы/);
 assert.match(root.innerHTML,/Демо-заказ и документы доступны/);
 const documentsButton=root.innerHTML.match(/<button[^>]*data-api-action="success"[^>]*>/)?.[0];
 assert.ok(documentsButton);assert.doesNotMatch(documentsButton,/disabled/);
 assert.doesNotMatch(root.innerHTML,/Восстановить этот ответ уже нельзя/);
 click('success');
 assert.equal(modal.open,true);assert.match(modal.innerHTML,/Скачать демо-ваучер/);assert.match(modal.innerHTML,/Скачать календарь \.ics/);
 assert.doesNotMatch(modal.innerHTML,/api-checkout-form/);
 click('restart-session');await until(()=>controller.state.connection==='online');
 assert.equal(controller.state.sessionId,null);assert.equal(controller.state.order,null);assert.equal(controller.state.orders.length,1);
 click('orders');assert.match(modal.innerHTML,/Открыть документы/);
 click('order',{orderIndex:'0'});assert.match(modal.innerHTML,/Скачать демо-ваучер/);
 assert.ok(calls.every(call=>call.method==='GET'),'Document restoration cannot create another payment or chat turn');
});
