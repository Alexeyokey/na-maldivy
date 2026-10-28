import test from 'node:test';
import assert from 'node:assert/strict';
import {ApiTripController} from '../dist/api-model.js';

const sid='audit-session';
const travelers=[{full_name:'DEMO ONE',birth_date:'1990-01-01'}];
const option={index:1,can_confirm:true,hotel:{name:'Demo Hotel',check_in:'2026-11-18',check_out:'2026-11-25'},outbound:{departure_at:'2026-11-18T10:00:00+03:00'},inbound:{departure_at:'2026-11-25T10:00:00+03:00'}};
const cart={cart_id:'CART-audit',session_id:sid,status:'ready_for_demo_payment',package:option,travelers,is_simulated:true};
const order={order_id:'DEMO-audit',cart_id:cart.cart_id,session_id:sid,status:'paid_demo',is_simulated:true,package:option,travelers};
const snapshot={session_id:sid,version:1,stage:'booked',brief:{destination:'AYT'},sources:{destination:'explicit'},selected:1,stale:false,booking:{cart_id:cart.cart_id,order_id:order.order_id}};
const deferred=()=>{let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve};};
const storageWith=value=>({getItem:()=>JSON.stringify(value),setItem:()=>{}});

test('A paid trip reload restores its current order and downloads without reopening checkout',async()=>{
 const calls=[];
 const controller=new ApiTripController({storage:storageWith({schemaVersion:2,sessionId:sid,stage:'booked',version:1,cart:{cart_id:cart.cart_id,status:'paid_demo'},messages:[{role:'user',text:'Подтверждаю'}],orders:[{sessionId:sid,orderId:order.order_id}]}),fetchImpl:async(path,init={})=>{
  calls.push({path,method:init.method||'GET',headers:init.headers});
  if(path==='/api/session/'+sid)return Response.json(snapshot);
  if(path==='/api/checkout/orders/'+order.order_id)return Response.json(order);
  if(path.endsWith('/voucher'))return new Response('<html>DEMO</html>',{headers:{'Content-Type':'text/html'}});
  throw new Error('Unexpected route');
 }});
 await controller.restore();
 assert.equal(controller.state.order?.order_id,order.order_id);
 assert.equal(controller.state.orders.length,1);assert.equal(controller.state.stage,'booked');
 assert.equal((await controller.download('voucher')).filename,'demo-voucher.html');
 assert.ok(calls.every(call=>call.method==='GET'),'Restoring a paid trip cannot create another payment');
 assert.equal(calls.at(-1).headers['X-Session-ID'],sid);
});

test('A missing local order reference is recovered from the server snapshot after a lost payment response',async()=>{
 const controller=new ApiTripController({fetchImpl:async path=>path.startsWith('/api/session/')?Response.json(snapshot):Response.json(order)});
 controller.state.sessionId=sid;controller.state.cart=structuredClone(cart);
 await controller.sessionSnapshot();
 assert.equal(controller.state.order?.order_id,order.order_id);
 assert.equal(controller.state.orders.length,1);
});

test('A durable paid order and both documents survive a lost chat session and another browser reload',async()=>{
 let stored=JSON.stringify({schemaVersion:2,sessionId:sid,stage:'booked',version:1,cart:{cart_id:cart.cart_id,status:'paid_demo'},messages:[{role:'user',text:'Подтверждаю'}],orders:[{sessionId:sid,orderId:order.order_id}]});
 const storage={getItem:()=>stored,setItem:(_,value)=>stored=value},calls=[];
 const fetchImpl=async(path,init={})=>{
  calls.push({path,method:init.method||'GET',headers:init.headers});
  if(path==='/api/session/'+sid)return Response.json({detail:'Session no longer exists'},{status:404});
  if(path==='/api/checkout/orders/'+order.order_id)return Response.json(order);
  if(path.endsWith('/voucher'))return new Response('<html>DURABLE DEMO</html>',{headers:{'Content-Type':'text/html'}});
  if(path.endsWith('/calendar.ics'))return new Response('BEGIN:VCALENDAR\r\nEND:VCALENDAR',{headers:{'Content-Type':'text/calendar'}});
  throw new Error('Unexpected route');
 };
 for(let reload=0;reload<2;reload++){
  const controller=new ApiTripController({storage,fetchImpl});await controller.restore();
  assert.equal(controller.state.order?.order_id,order.order_id);
  assert.equal(controller.state.currentOrderId,order.order_id);
  assert.equal(controller.state.sessionLost,true,'The expired search session must not be silently reused');
  assert.match(controller.state.error,/документы сохранены/);
  assert.equal(controller.state.orders.length,1);
  await assert.rejects(controller.send('Следующая поездка'),error=>error.code==='recovery');
  for(const kind of ['voucher','calendar'])assert.ok((await controller.download(kind)).blob instanceof Blob);
 }
 assert.ok(calls.every(call=>call.method==='GET'));
 assert.equal(calls.at(-1).headers['X-Session-ID'],sid);
});

test('The saved current order reference chooses the right durable order when one session has several bookings',async()=>{
 const previous={...order,order_id:'DEMO-previous',cart_id:'CART-previous'};
 const saved={schemaVersion:2,sessionId:sid,stage:'booked',version:2,currentOrderId:order.order_id,orders:[{sessionId:sid,orderId:previous.order_id},{sessionId:sid,orderId:order.order_id}]};
 const controller=new ApiTripController({storage:storageWith(saved),fetchImpl:async path=>{
  if(path.startsWith('/api/session/'))return Response.json({detail:'Missing session'},{status:404});
  return Response.json(path.endsWith(previous.order_id)?previous:order);
 }});
 await controller.restore();
 assert.equal(controller.state.order?.order_id,order.order_id);assert.equal(controller.state.orders.length,2);
 controller.restartSession();
 assert.equal(controller.state.order,null);assert.equal(controller.state.currentOrderId,null);
 assert.equal(controller.state.sessionLost,false);assert.equal(controller.state.orders.length,2);
});

test('An unavailable durable order cannot be reconstructed from a saved paid marker alone',async()=>{
 const saved={schemaVersion:2,sessionId:sid,stage:'booked',version:1,currentOrderId:order.order_id,orders:[{sessionId:sid,orderId:order.order_id}]};
 const controller=new ApiTripController({storage:storageWith(saved),fetchImpl:async()=>Response.json({detail:'Missing object'},{status:404})});
 await controller.restore();
 assert.equal(controller.state.sessionLost,true);assert.equal(controller.state.order,null);assert.equal(controller.state.orders.length,0);
 await assert.rejects(controller.download('voucher'),error=>error.code==='validation');
});

test('The current order reference remains saved while its authenticated GET is in flight',async()=>{
 const started=deferred(),response=deferred();
 let stored=JSON.stringify({schemaVersion:2,sessionId:sid,stage:'booked',version:1,currentOrderId:order.order_id,sessionLost:true,orders:[{sessionId:sid,orderId:order.order_id}]});
 const storage={getItem:()=>stored,setItem:(_,value)=>stored=value};
 const controller=new ApiTripController({storage,fetchImpl:async()=>{started.resolve();return response.promise;}});
 const restoring=controller.restore();await started.promise;
 assert.equal(JSON.parse(stored).currentOrderId,order.order_id,'Another reload during the GET must retain the durable selection');
 response.resolve(Response.json(order));await restoring;
 assert.equal(controller.state.order?.order_id,order.order_id);assert.equal(JSON.parse(stored).order,null);
 assert.doesNotMatch(stored,/DEMO ONE|1990-01-01/,'The selected reference cannot persist the full traveler payload');
});

test('A late paid-order read cannot repopulate a reset conversation',async()=>{
 const started=deferred(),response=deferred();
 const controller=new ApiTripController({fetchImpl:async path=>{
  if(path.startsWith('/api/session/'))return Response.json(snapshot);
  started.resolve();return response.promise;
 }});
 controller.state.sessionId=sid;
 const pending=controller.sessionSnapshot();await started.promise;
 controller.reset();response.resolve(Response.json(order));assert.equal(await pending,null);
 assert.equal(controller.state.order,null);assert.deepEqual(controller.state.orders,[]);assert.equal(controller.state.stage,'collecting');
});

test('A session snapshot for a different owner cannot overwrite the current trip',async()=>{
 const controller=new ApiTripController({fetchImpl:async()=>Response.json({...snapshot,session_id:'another-session'})});
 controller.state.sessionId=sid;controller.state.brief={destination:'MLE'};
 await assert.rejects(controller.sessionSnapshot(),error=>error.code==='protocol');
 assert.deepEqual(controller.state.brief,{destination:'MLE'});assert.equal(controller.state.order,null);
});

test('A stale server snapshot cannot rewind a newer trip version',async()=>{
 const controller=new ApiTripController({fetchImpl:async()=>Response.json({...snapshot,version:1})});
 controller.state.sessionId=sid;controller.state.version=2;controller.state.brief={destination:'MLE'};
 await assert.rejects(controller.sessionSnapshot(),error=>error.code==='protocol');
 assert.equal(controller.state.version,2);assert.deepEqual(controller.state.brief,{destination:'MLE'});
});

test('A mismatched or regressed terminal chat event preserves the original recovery request',async()=>{
 for(const terminal of [{session_id:'another-session',version:3,stage:'collecting'},{session_id:sid,version:1,stage:'collecting'}]){
  const body='event: delta\ndata: {"text":"Полученная часть"}\n\nevent: done\ndata: '+JSON.stringify(terminal)+'\n\n';
  const controller=new ApiTripController({fetchImpl:async()=>new Response(body,{headers:{'Content-Type':'text/event-stream'}})});
  controller.state.sessionId=sid;controller.state.version=2;
  await assert.rejects(controller.send('Продолжаем'),error=>error.code==='protocol');
  assert.equal(controller.state.sessionId,sid);assert.equal(controller.state.version,2);assert.ok(controller.state.pendingRequest);
  assert.equal(controller.state.messages[1].text,'Полученная часть');
 }
});

test('A failed checkout read does not partially apply a newer snapshot',async()=>{
 const controller=new ApiTripController({fetchImpl:async path=>path.startsWith('/api/session/')?Response.json({...snapshot,stage:'checkout_pending',booking:{cart_id:cart.cart_id}}):Response.json({detail:'Unavailable'},{status:503})});
 controller.state.sessionId=sid;controller.state.stage='packages_shown';controller.state.version=0;controller.state.brief={destination:'MLE'};
 await assert.rejects(controller.sessionSnapshot(),error=>error.status===503);
 assert.equal(controller.state.stage,'packages_shown');assert.equal(controller.state.version,0);assert.deepEqual(controller.state.brief,{destination:'MLE'});
});

test('An invalid payment success never unlocks documents and retries the same idempotency key',async()=>{
 for(const invalid of [{status:'paid_demo',is_simulated:true},{...order,cart_id:'CART-another'},{...order,session_id:'another-session'},{...order,session_id:undefined}]){
  const keys=[];let payments=0;
  const controller=new ApiTripController({fetchImpl:async(path,init={})=>{
   if(path.endsWith('/travelers'))return Response.json(cart);
   keys.push(JSON.parse(init.body).idempotency_key);return Response.json(++payments===1?invalid:order);
  }});
  controller.state.sessionId=sid;controller.state.cart=structuredClone(cart);
  const args={travelers,outcome:'approved',acknowledgeDemo:true,acknowledgeUnpriced:true};
  await assert.rejects(controller.pay(args),error=>error.code==='protocol');
  assert.equal(controller.state.order,null);assert.equal(controller.state.orders.length,0);
  await controller.pay(args);assert.equal(controller.state.order.order_id,order.order_id);
  assert.equal(keys[0],keys[1],'An unconfirmed payment outcome must retain its request key');
 }
});
