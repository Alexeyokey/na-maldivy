import test from 'node:test';
import assert from 'node:assert/strict';
import {ApiTripController,createApiModel,applyAgentEvent,visibleText,createApiAnnouncements} from '../dist/api-model.js';

const sid='private-session_01';
const packageOption={index:1,label:'Вариант 1',can_confirm:true,total_rub:83234,known_total_rub:83234,price_scope:'listed_components',hotel:{hotel_id:'osm:way/123',name:'Sky Business Hotel',check_in:'2026-11-18',check_out:'2026-11-25',total_price_rub:23338},outbound:{departure_at:'2026-11-18T10:00:00+03:00'},inbound:{departure_at:'2026-11-25T10:00:00+03:00'},flights:[{price_rub:22502,price_source:{provider:'aviasales_data_api',seat_price_rub:11251,cache_hit:true}}],why:['Запись Aviasales'],risks:['Наличие мест не подтверждено']};
const cart={cart_id:'CART-private_01',session_id:sid,status:'awaiting_travelers',version:2,option:1,package:packageOption,expected_travelers:{adults:2,children:0,infants:0},travelers:[],is_simulated:true};
const travelers=[{full_name:'DEMO ONE',birth_date:'1990-01-01'},{full_name:'DEMO TWO',birth_date:'1991-01-01'}];
const order={order_id:'DEMO-private_01',cart_id:cart.cart_id,session_id:sid,status:'paid_demo',is_simulated:true,package:packageOption,travelers,created_at:'2026-10-28T12:00:00Z',voucher_path:'/api/checkout/orders/DEMO-private_01/voucher',calendar_path:'/api/checkout/orders/DEMO-private_01/calendar.ics'};
const eventResponse=events=>new Response(events.map(([type,data])=>'event: '+type+'\ndata: '+JSON.stringify(data)+'\n\n').join(''),{headers:{'Content-Type':'text/event-stream'}});

test('Live announcements ignore restored history and streaming tokens; known phases and completion are spoken once',()=>{
 const announce=createApiAnnouncements(),state=createApiModel();
 state.messages=[{role:'assistant',text:'A previous answer'}];assert.equal(announce(state),null);
 Object.assign(state,{busy:true,pendingRequest:{id:'turn-one'},statusText:'Готовим поездку'});
 assert.equal(announce(state),'Готовим ответ.');
 for(let index=0;index<20;index++){state.messages.at(-1).text+=' token';assert.equal(announce(state),null);}
 state.statusText='Собираю поездку: рейсы, отели, трансферы';assert.equal(announce(state),'Ищем рейсы, отели и трансферы.');
 state.statusText='Готовлю ответ';assert.equal(announce(state),null,'An earlier phase is not spoken twice within one attempt');
 Object.assign(state,{pendingRequest:null,syncNeeded:true});assert.equal(announce(state),null,'done clears pending while the same chat is still busy; this is not checkout');
 state.busy=false;assert.equal(announce(state),'Ответ получен. Обновляем состояние поездки.');assert.equal(announce(state),null);
 Object.assign(state,{syncNeeded:false,options:[{},{},{}],stage:'packages_shown'});
 assert.equal(announce(state),'Ответ готов. Доступно вариантов: 3. Результаты находятся после переписки.');
 for(let index=0;index<5;index++)assert.equal(announce(state),null);
});

test('Interrupted live requests and retries have distinct safe feedback without a duplicate terminal announcement',()=>{
 const announce=createApiAnnouncements(),state=createApiModel();announce(state);
 Object.assign(state,{busy:true,pendingRequest:{id:'repeatable-turn'},statusText:'Разбираю запрос'});assert.match(announce(state),/Разбираем/);
 Object.assign(state,{busy:false,connection:'offline'});assert.match(announce(state),/Проверьте связь/);assert.equal(announce(state),null);
 state.connection='online';assert.match(announce(state),/Можно повторить/);assert.equal(announce(state),null);
 Object.assign(state,{busy:true,statusText:'Восстанавливаем ответ'});assert.match(announce(state),/Восстанавливаем/);
 Object.assign(state,{busy:false,pendingRequest:null,question:{text:'Private question text'}});assert.match(announce(state),/уточняющий вопрос/);
 assert.equal(announce(state),null);state.version++;assert.equal(announce(state),null);
});

test('Live announcements never interpolate status, message, error, hotel, session or order data',()=>{
 const announce=createApiAnnouncements(),state=createApiModel(),secret='sensitive-payload-do-not-announce';announce(state);
 Object.assign(state,{busy:true,pendingRequest:{id:secret,message:secret},statusText:secret,sessionId:secret,messages:[{role:'assistant',text:secret}]});
 const output=[announce(state)];state.statusText=secret+' again';output.push(announce(state));
 Object.assign(state,{busy:false,pendingRequest:null,error:secret});output.push(announce(state));
 state.error=null;output.push(announce(state));
 state.sessionLost=true;output.push(announce(state));
 assert.doesNotMatch(output.filter(Boolean).join(' '),new RegExp(secret));assert.equal(output.at(3),null,'A failed action is not later re-announced as successful');
});

test('Demo checkout announcements require server confirmation, distinguish decline, and never imply a real payment',()=>{
 const announce=createApiAnnouncements(),state=createApiModel();announce(state);
 state.busy=true;assert.match(announce(state),/демонстрационного оформления/);assert.equal(announce(state),null);
 Object.assign(state,{busy:false,cart:{status:'payment_declined'}});assert.match(announce(state),/тестовый отказ/);assert.equal(announce(state),null);
 state.busy=true;assert.match(announce(state),/Проверяем/);
 Object.assign(state,{busy:false,order:{status:'paid_demo',is_simulated:true,order_id:'private-order'}});
 assert.equal(announce(state),'Демонстрационное оформление подтверждено сервером. Реального списания нет; документы доступны.');assert.equal(announce(state),null);
});

test('Live recovery messages are deduplicated and an unchanged error cannot cause transcript announcements',()=>{
 const announce=createApiAnnouncements(),state=createApiModel();announce(state);
 for(const [patch,pattern] of [[{connection:'auth'},/вход на сайт/],[{connection:'offline'},/Нет связи/],[{connection:'online',sessionLost:true},/сессия завершилась/]]){
  Object.assign(state,patch);assert.match(announce(state),pattern);for(let count=0;count<3;count++)assert.equal(announce(state),null);
 }
});

test('An ordinary chat answer never announces an earlier demo approval or decline as a new payment result',()=>{
 for(const paymentState of [{order:{status:'paid_demo',is_simulated:true}},{cart:{status:'payment_declined'}}]){
  const announce=createApiAnnouncements(),state={...createApiModel(),...paymentState};announce(state);
  Object.assign(state,{busy:true,pendingRequest:{id:'chat-after-checkout'},statusText:'Разбираю запрос'});announce(state);
  Object.assign(state,{busy:false,pendingRequest:null});const text=announce(state);
  assert.equal(text,'Ответ готов. Новое сообщение находится в чате.');assert.doesNotMatch(text,/оплат|оформление подтверждено/);
 }
});

test('A browser storage failure is announced once per outage without suppressing phases or completion',()=>{
 const announce=createApiAnnouncements(),state=createApiModel();announce(state);
 Object.assign(state,{busy:true,pendingRequest:{id:'storage-turn'},statusText:'Готовим поездку',storageWarning:'private storage error'});
 const first=announce(state);assert.match(first,/Не удалось сохранить переписку/);assert.match(first,/Готовим ответ/);assert.doesNotMatch(first,/private/);
 for(let count=0;count<20;count++){state.storageWarning='different private error '+count;assert.equal(announce(state),null);}
 state.statusText='Разбираю запрос';const phase=announce(state);assert.match(phase,/Разбираем/);assert.doesNotMatch(phase,/сохранить переписку/);
 Object.assign(state,{busy:false,pendingRequest:null});assert.equal(announce(state),'Ответ готов. Новое сообщение находится в чате.');assert.equal(announce(state),null);
 state.storageWarning=null;assert.equal(announce(state),null);
 state.storageWarning='a new private storage error';assert.equal(announce(state),'Не удалось сохранить переписку в браузере. Пока вкладка открыта, сообщения остаются на странице.');assert.equal(announce(state),null);
 const initial=createApiAnnouncements();assert.match(initial({...createApiModel(),storageWarning:'private'}),/Не удалось сохранить переписку/,'A warning on the first idle frame must also be accessible');
});

function checkoutController(payment,options={}){
 const calls=[];
 const controller=new ApiTripController({...options,onChange:()=>{},fetchImpl:async(path,init={})=>{
  const body=init.body?JSON.parse(init.body):undefined;calls.push({path,method:init.method||'GET',headers:init.headers,body});
  if(path.endsWith('/travelers'))return Response.json({...cart,cart_id:path.split('/').at(-2),status:'ready_for_demo_payment',travelers});
  if(path.endsWith('/pay'))return payment({path,init,body,calls});
  if(path===order.voucher_path)return new Response('<html>DEMO</html>',{headers:{'Content-Type':'text/html'}});
  if(path===order.calendar_path)return new Response('BEGIN:VCALENDAR\r\nEND:VCALENDAR',{headers:{'Content-Type':'text/calendar'}});
  throw new Error('Unexpected test route');
 }});
 controller.state.sessionId=sid;controller.state.cart=structuredClone(cart);controller.state.selected=1;controller.state.options=[structuredClone(packageOption)];controller.state.optionKind='packages';controller.state.stage='checkout_pending';
 return {controller,calls};
}
const paymentArgs={travelers,outcome:'approved',acknowledgeDemo:true,acknowledgeUnpriced:true};

test('Agent events retain server options, source metadata and explicit empty results',()=>{
 const model=createApiModel();assert.deepEqual(model.options,[]);assert.equal(model.order,null);
 applyAgentEvent(model,'options',{kind:'packages',items:[structuredClone(packageOption)],notes:[]});
 assert.equal(model.optionKind,'packages');assert.equal(model.options[0].total_rub,83234);assert.equal(model.options[0].flights[0].price_source.seat_price_rub,11251);
 applyAgentEvent(model,'options',{kind:'packages',items:[],notes:['Нет котировки']});assert.deepEqual(model.options,[]);assert.equal(model.order,null);
 applyAgentEvent(model,'status',{stage:'search',text:'Проверяем котировки'});assert.deepEqual(model.options,[]);assert.equal(model.cart,null);
});

test('Sequential messages preserve the server session and recover brief and pending cart',async()=>{
 const calls=[];let turn=0;const brief={origin:'MOW',destination:'AYT',start_date:'2026-11-18',nights:7,adults:2};
 const controller=new ApiTripController({onChange:()=>{},fetchImpl:async(path,init={})=>{
  calls.push({path,method:init.method||'GET',body:init.body?JSON.parse(init.body):undefined,headers:init.headers});
  if(path==='/api/chat'){
   turn++;
   if(turn===1)return eventResponse([['options',{kind:'packages',items:[packageOption]}],['delta',{text:'Показаны котировки Aviasales'}],['done',{session_id:sid,stage:'packages_shown',version:2}]]);
   return eventResponse([['booking',{cart_id:cart.cart_id,status:cart.status,paid:false,is_simulated:true}],['delta',{text:'Создана корзина, оплата ещё не выполнена'}],['done',{session_id:sid,stage:'checkout_pending',version:2}]]);
  }
  if(path==='/api/session/'+sid)return Response.json({session_id:sid,stage:turn===1?'packages_shown':'checkout_pending',version:2,brief,sources:{destination:'user'},selected:turn===1?null:1,booking:turn===1?null:{cart_id:cart.cart_id,status:cart.status,paid:false}});
  if(path==='/api/checkout/carts/'+cart.cart_id)return Response.json(cart);
  throw new Error('Unexpected test route');
 }});
 await controller.send('В Анталью вдвоём');assert.equal(controller.state.sessionId,sid);assert.equal(controller.state.brief.destination,'AYT');assert.equal(controller.state.options[0].total_rub,83234);assert.equal(controller.state.order,null);
 await controller.send('Подтверждаю');
 const chatCalls=calls.filter(call=>call.path==='/api/chat');assert.equal(chatCalls[0].body.session_id,undefined);assert.equal(chatCalls[1].body.session_id,sid);
 assert.equal(controller.state.cart.cart_id,cart.cart_id);assert.equal(controller.state.order,null);assert.equal(controller.state.stage,'checkout_pending');
 assert.ok(calls.filter(call=>call.path==='/api/session/'+sid).length===2);assert.equal(calls.find(call=>call.path==='/api/checkout/carts/'+cart.cart_id).headers['X-Session-ID'],sid);
 assert.match(JSON.stringify(controller.state.messages),/оплата ещё не выполнена/);assert.equal(controller.state.busy,false);
});

test('Missing options and incomplete SSE cannot create local fallback offers or a paid order',async()=>{
 const controller=new ApiTripController({onChange:()=>{},fetchImpl:async(path)=>path==='/api/chat'?eventResponse([['delta',{text:'Не хватает дат'}],['question',{field:'start_date',text:'Когда вылет?',quick_replies:['18 ноября']}],['done',{session_id:sid,stage:'collecting',version:0}]]):Response.json({session_id:sid,stage:'collecting',version:0,brief:{},sources:{},selected:null,booking:null})});
 await controller.send('Хочу в отпуск');assert.deepEqual(controller.state.options,[]);assert.equal(controller.state.order,null);assert.equal(controller.state.stage,'collecting');
 const interrupted=new ApiTripController({onChange:()=>{},fetchImpl:async()=>eventResponse([['delta',{text:'Неполный ответ'}]])});await assert.rejects(interrupted.send('В отпуск'),error=>error.code==='incomplete');assert.equal(interrupted.state.order,null);assert.deepEqual(interrupted.state.options,[]);assert.ok(interrupted.state.error);assert.equal(interrupted.state.busy,false);
});

test('Both explicit acknowledgements are required before any payment request',async()=>{
 for(const args of [{...paymentArgs,acknowledgeDemo:false},{...paymentArgs,acknowledgeUnpriced:false}]){
  const {controller,calls}=checkoutController(()=>assert.fail('Payment must not run'));
  await assert.rejects(controller.pay(args),error=>error.code==='validation');assert.equal(calls.length,0);assert.equal(controller.state.order,null);
 }
});

test('A declined payment creates no order; approval sends a different idempotency key and no prices',async()=>{
 const {controller,calls}=checkoutController(({body})=>Response.json(body.outcome==='declined'?{cart_id:cart.cart_id,status:'payment_declined',is_simulated:true,notice:'Тестовый отказ'}:order));
 await controller.pay({...paymentArgs,outcome:'declined'});assert.equal(controller.state.order,null);assert.equal(controller.state.orders.length,0);assert.equal(controller.state.cart.status,'payment_declined');
 await controller.pay(paymentArgs);assert.equal(controller.state.order.order_id,order.order_id);assert.equal(controller.state.order.status,'paid_demo');assert.equal(controller.state.orders.length,1);
 const payments=calls.filter(call=>call.path.endsWith('/pay'));assert.equal(payments.length,2);assert.notEqual(payments[0].body.idempotency_key,payments[1].body.idempotency_key);
 for(const call of payments){assert.equal(call.body.session_id,sid);assert.equal(call.body.acknowledge_demo,true);assert.equal(call.body.acknowledge_unpriced,true);assert.deepEqual(Object.keys(call.body).sort(),['acknowledge_demo','acknowledge_unpriced','idempotency_key','outcome','session_id']);}
});

test('An ambiguous payment network failure retries the identical key without resubmitting travelers',async()=>{
 let count=0;const {controller,calls}=checkoutController(()=>{if(++count===1)throw new Error('Network broke after possible server acceptance');return Response.json(order);});
 await assert.rejects(controller.pay(paymentArgs),error=>error.code==='network');assert.equal(controller.state.order,null);assert.ok(controller.state.error);
 await controller.pay(paymentArgs);assert.equal(controller.state.order.order_id,order.order_id);
 const payments=calls.filter(call=>call.path.endsWith('/pay'));assert.equal(payments.length,2);assert.equal(payments[0].body.idempotency_key,payments[1].body.idempotency_key);assert.equal(calls.filter(call=>call.path.endsWith('/travelers')).length,1);
});

test('An ambiguous payment timeout retries its original key after stalled headers or body without duplicate orders',async()=>{
 for(const phase of ['headers','body']){
  const accepted=new Map(),late=deferred();let attempts=0;
  const {controller,calls}=checkoutController(({body})=>{
   if(!accepted.has(body.idempotency_key))accepted.set(body.idempotency_key,structuredClone(order));
   if(++attempts===1)return phase==='headers'?late.promise:{ok:true,headers:new Headers({'Content-Type':'application/json'}),json:()=>late.promise};
   return Response.json(accepted.get(body.idempotency_key));
  },{checkoutTimeoutMs:15});
  await assert.rejects(controller.pay(paymentArgs),error=>error.code==='timeout');
  assert.equal(controller.state.busy,false);assert.equal(controller.state.order,null);assert.ok(controller.pendingPayment?.key);
  assert.doesNotMatch(controller.state.error,/заказ не создан|отказ оплаты/i);
  await controller.pay(paymentArgs);
  assert.equal(controller.state.order.order_id,order.order_id);assert.equal(controller.state.orders.length,1);assert.equal(accepted.size,1);
  const payments=calls.filter(call=>call.path.endsWith('/pay'));
  assert.equal(payments.length,2);assert.equal(payments[0].body.idempotency_key,payments[1].body.idempotency_key);
  assert.equal(calls.filter(call=>call.path.endsWith('/travelers')).length,1);
  const confirmed=structuredClone(controller.state);
  late.resolve(phase==='headers'?Response.json({...order,order_id:'DEMO-late-ignored'}):{...order,order_id:'DEMO-late-ignored'});
  await Promise.resolve();await Promise.resolve();assert.deepEqual(controller.state,confirmed,'Late consumption cannot replace the confirmed payment');
 }
});

test('A stalled cart body cannot partly apply a newer snapshot and a later reconciliation recovers',async()=>{
 let attempts=0;
 const controller=new ApiTripController({checkoutTimeoutMs:15,fetchImpl:async path=>{
  if(path.startsWith('/api/session/'))return Response.json({session_id:sid,stage:'checkout_pending',version:2,brief:{destination:'AYT'},selected:1,booking:{cart_id:cart.cart_id}});
  if(++attempts===1)return {ok:true,headers:new Headers({'Content-Type':'application/json'}),json:()=>new Promise(()=>{})};
  return Response.json(cart);
 }});
 controller.state.sessionId=sid;controller.state.version=1;controller.state.brief={destination:'MLE'};
 const before=structuredClone(controller.state);
 await assert.rejects(controller.sessionSnapshot(),error=>error.code==='timeout');assert.deepEqual(controller.state,before);
 await controller.sessionSnapshot();assert.equal(controller.state.version,2);assert.equal(controller.state.cart.cart_id,cart.cart_id);
});

test('A stalled session body is bounded even if its response ignores the abort signal',async()=>{
 const controller=new ApiTripController({recoveryTimeoutMs:15,fetchImpl:async()=>({ok:true,json:()=>new Promise(()=>{})})});
 controller.state.sessionId=sid;const before=structuredClone(controller.state);
 await assert.rejects(controller.sessionSnapshot(),error=>error.code==='timeout');assert.deepEqual(controller.state,before);
});

test('User-visible text excludes source review identifiers and runtime access identifiers',()=>{
 const raw='Цитата «Понравилось семье». [review_id: rv_001] CART-0123456789abcdef0123456789abcdef DEMO-ABCDEF0123456789ABCDEF0123456789 0123456789abcdef0123456789abcdef';
 const safe=visibleText(raw);assert.match(safe,/Понравилось семье/);assert.doesNotMatch(safe,/rv_001|CART-|DEMO-|0123456789abcdef|ABCDEF0123456789/);
});

test('A new cart requires its own traveler submission even when the people are unchanged',async()=>{
 const {controller,calls}=checkoutController(({path})=>Response.json({...order,cart_id:path.split('/').at(-2),order_id:path.includes('CART-new_02')?'DEMO-new_02':order.order_id}));
 await controller.pay(paymentArgs);
 controller.state.cart={...cart,cart_id:'CART-new_02'};controller.state.stage='checkout_pending';controller.state.order=null;
 await controller.pay(paymentArgs);
 const writes=calls.filter(call=>call.path.endsWith('/travelers'));assert.equal(writes.length,2);assert.notEqual(writes[0].path,writes[1].path);
});

test('Downloads remain unavailable before server approval and use the private session afterwards',async()=>{
 const {controller,calls}=checkoutController(()=>Response.json(order));
 await assert.rejects(controller.download('voucher'),error=>error.code==='validation');assert.equal(calls.length,0);
 await controller.pay(paymentArgs);
 for(const [kind,filename] of [['voucher','demo-voucher.html'],['calendar','demo-trip.ics']]){
  const result=await controller.download(kind);assert.equal(result.filename,filename);assert.ok(result.blob instanceof Blob);
  const call=calls.at(-1);assert.equal(call.headers['X-Session-ID'],sid);assert.ok(!call.path.includes(sid));
 }
 const entries=controller.state.orders.slice();controller.reset();assert.equal(controller.state.sessionId,null);assert.equal(controller.state.cart,null);assert.equal(controller.state.order,null);assert.deepEqual(controller.state.options,[]);assert.deepEqual(controller.state.orders,entries);
});

const deferred=()=>{let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve};};

test('Browser fetch retains its required global receiver for session requests',async()=>{
 const controller=new ApiTripController({fetchImpl:async function(){assert.equal(this,globalThis,'Native browser fetch must not receive the controller as this');return Response.json({stage:'collecting',brief:{},sources:{},version:1,selected:null,stale:false});}});
 controller.state.sessionId=sid;await controller.sessionSnapshot();assert.equal(controller.state.version,1);
});

test('A late successful payment cannot book or unlock a reset/new trip',async()=>{
 const started=deferred(),response=deferred();
 const {controller,calls}=checkoutController(()=>{started.resolve();return response.promise;});
 const pending=controller.pay(paymentArgs);await started.promise;
 controller.reset();controller.state.sessionId='new-private-session';controller.state.busy=true;
 response.resolve(Response.json(order));assert.equal(await pending,null);
 assert.equal(controller.state.order,null);assert.equal(controller.state.cart,null);assert.equal(controller.state.stage,'collecting');assert.equal(controller.state.orders.length,0);assert.equal(controller.state.sessionId,'new-private-session');assert.equal(controller.state.busy,true,'Old finally must not unlock a new request');
 assert.equal(calls.find(call=>call.path.endsWith('/pay')).body.session_id,sid);
});

test('A late traveler write cannot issue payment using the new session owner',async()=>{
 const started=deferred(),response=deferred();let payments=0;
 const controller=new ApiTripController({fetchImpl:async(path)=>{if(path.endsWith('/travelers')){started.resolve();return response.promise;}payments++;return Response.json(order);}});
 controller.state.sessionId=sid;controller.state.cart=structuredClone(cart);
 const pending=controller.pay(paymentArgs);await started.promise;controller.reset();controller.state.sessionId='new-private-session';
 response.resolve(Response.json({...cart,status:'ready_for_demo_payment'}));assert.equal(await pending,null);assert.equal(payments,0);assert.equal(controller.state.cart,null);
});

test('A late session snapshot and late cart read cannot overwrite a new trip',async()=>{
 for(const delayCart of [false,true]){
  const started=deferred(),response=deferred();
  const snapshot={stage:'checkout_pending',version:2,brief:{destination:'AYT'},sources:{},selected:1,stale:false,booking:{cart_id:cart.cart_id}};
  const controller=new ApiTripController({fetchImpl:async path=>{
   if(path.startsWith('/api/session/')){if(!delayCart){started.resolve();return response.promise;}return Response.json(snapshot);}
   started.resolve();return response.promise;
  }});
  controller.state.sessionId=sid;const pending=controller.sessionSnapshot();await started.promise;controller.reset();controller.state.sessionId='new-private-session';
  response.resolve(Response.json(delayCart?cart:snapshot));assert.equal(await pending,null);
  assert.equal(controller.state.stage,'collecting');assert.deepEqual(controller.state.brief,{});assert.equal(controller.state.cart,null);assert.equal(controller.state.sessionId,'new-private-session');
 }
});

test('A delayed private order restore cannot repopulate state after new trip',async()=>{
 const started=deferred(),response=deferred();
 const storage={getItem:()=>JSON.stringify({sessionId:sid,orders:[{sessionId:sid,orderId:order.order_id}]}),setItem:()=>{}};
 const controller=new ApiTripController({storage,fetchImpl:async()=>{started.resolve();return response.promise;}});
 const pending=controller.restore();await started.promise;controller.reset();response.resolve(Response.json(order));await pending;
 assert.equal(controller.state.sessionId,null);assert.equal(controller.state.orders.length,0);assert.deepEqual(controller.state.messages,[]);assert.equal(controller.state.order,null);
});
