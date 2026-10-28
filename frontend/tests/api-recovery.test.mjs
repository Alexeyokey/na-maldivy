import test from 'node:test';
import assert from 'node:assert/strict';
import {ApiTripController} from '../dist/api-model.js';

const sid='recovery-session';
const done={session_id:sid,stage:'collecting',version:1};
const snapshot={...done,brief:{destination:'AYT'},sources:{destination:'user'},selected:null,stale:false,booking:null};
const eventResponse=events=>new Response(events.map(([type,data])=>`event: ${type}\ndata: ${JSON.stringify(data)}\n\n`).join(''),{headers:{'Content-Type':'text/event-stream'}});
const complete=(text='Полный ответ')=>eventResponse([['delta',{text}],['done',done]]);
const partial=()=>eventResponse([['delta',{text:'Полученная часть'}]]);
const memoryStorage=()=>{
 const values=new Map();
 return {getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),value:()=>JSON.parse([...values.values()][0]||'null')};
};
const deferred=()=>{let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve};};

test('Interrupted response replays the same first-turn request without duplicate conversation bubbles',async()=>{
 const storage=memoryStorage(),calls=[];let attempts=0;
 const controller=new ApiTripController({storage,fetchImpl:async(path,init={})=>{
  if(path==='/api/chat'){calls.push(JSON.parse(init.body));return ++attempts===1?partial():complete('Полученная часть и продолжение');}
  return Response.json(snapshot);
 }});
 await assert.rejects(controller.send('Хочу в Анталью'),error=>error.code==='incomplete');
 assert.equal(controller.state.messages[1].text,'Полученная часть');
 assert.equal(controller.state.messages[1].interrupted,true);
 assert.equal(storage.value().messages[1].text,'Полученная часть');
 const requestId=controller.state.pendingRequest.id;
 assert.equal(storage.value().pendingRequest.id,requestId);
 // A later result/session hint must not change the original first-turn fingerprint.
 controller.state.sessionId=sid;
 await controller.retry();
 assert.equal(calls.length,2);assert.equal(calls[0].request_id,calls[1].request_id);
 assert.equal(calls[0].session_id,undefined);assert.equal(calls[1].session_id,undefined);
 assert.equal(calls[1].retry,true);assert.equal(calls[1].message,calls[0].message);
 assert.deepEqual(controller.state.messages.map(row=>[row.role,row.text]),[['user','Хочу в Анталью'],['assistant','Полученная часть и продолжение']]);
 assert.equal(controller.state.pendingRequest,null);assert.equal(controller.state.messages[1].error,false);
 assert.equal(controller.state.busy,false);assert.equal(storage.value().pendingRequest,null);
});

test('A retry that loses its connection before the first replayed delta preserves the previous partial answer',async()=>{
 let attempts=0;
 const controller=new ApiTripController({fetchImpl:async()=>{if(++attempts===1)return partial();throw new TypeError('Connection unavailable');}});
 await assert.rejects(controller.send('Поездка'),error=>error.code==='incomplete');
 const requestId=controller.state.pendingRequest.id;
 await assert.rejects(controller.retry(),error=>error.code==='network');
 assert.equal(controller.state.messages.length,2);assert.equal(controller.state.messages[1].text,'Полученная часть');
 assert.equal(controller.state.pendingRequest.id,requestId);assert.equal(controller.state.messages[1].interrupted,true);
 await assert.rejects(controller.send('Другое сообщение'),error=>error.code==='recovery');
 assert.equal(attempts,2,'An unresolved request must be handled before another message is sent');
});

test('A second interruption during an earlier replayed prefix cannot shorten the saved partial answer',async()=>{
 const storage=memoryStorage(),original='Полученная часть и продолжение';let attempts=0;
 const controller=new ApiTripController({storage,fetchImpl:async path=>{
  if(path!=='/api/chat')return Response.json(snapshot);
  if(++attempts===3)return complete(original+'. Ответ завершён.');
  return eventResponse([['delta',{text:attempts===1?original:'Полученная'}]]);
 }});
 await assert.rejects(controller.send('Поездка'),error=>error.code==='incomplete');
 const requestId=controller.state.pendingRequest.id;
 await assert.rejects(controller.retry(),error=>error.code==='incomplete');
 assert.equal(controller.state.messages[1].text,original);
 assert.equal(storage.value().messages[1].text,original,'A shorter replay must not overwrite browser recovery storage');
 assert.equal(controller.state.pendingRequest.id,requestId);assert.equal(controller.state.messages[1].interrupted,true);
 await controller.retry();
 assert.equal(controller.state.messages[1].text,original+'. Ответ завершён.');
 assert.equal(controller.state.pendingRequest,null);assert.equal(controller.state.messages.length,2);
});

test('Reloading an interrupted conversation restores transcript and draft offline and never resends automatically',async()=>{
 const storage=memoryStorage();
 const first=new ApiTripController({storage,fetchImpl:async()=>partial()});
 await assert.rejects(first.send('В Анталью'),error=>error.code==='incomplete');
 first.setDraft('А если взять ребёнка?');
 const requestId=first.state.pendingRequest.id,calls=[];
 const restored=new ApiTripController({storage,fetchImpl:async(path,init)=>{calls.push({path,init});throw new TypeError('Offline');}});
 await restored.restore();
 assert.deepEqual(restored.state.messages.map(row=>row.text),['В Анталью','Полученная часть']);
 assert.equal(restored.state.draft,'А если взять ребёнка?');assert.equal(restored.state.pendingRequest.id,requestId);
 assert.equal(restored.state.busy,false);assert.deepEqual(calls,[]);
 await restored.reconnect();
 assert.equal(restored.state.connection,'offline');assert.equal(restored.state.pendingRequest.id,requestId);
 assert.deepEqual(calls.map(row=>row.path),['/health'],'A reachability probe cannot resend a message');
});

test('A completed answer with a failed state read is recovered with GET requests, without another model turn',async()=>{
 const calls=[];let reads=0;
 const controller=new ApiTripController({fetchImpl:async(path,init={})=>{
  calls.push({path,method:init.method||'GET'});
  if(path==='/api/chat')return complete();
  if(path==='/health')return Response.json({status:'ok',llm_provider:'fake'});
  if(++reads===1)throw new TypeError('Connection failed after completed response');
  return Response.json(snapshot);
 }});
 await controller.send('Поездка');
 assert.equal(controller.state.messages[1].text,'Полный ответ');assert.equal(controller.state.pendingRequest,null);
 assert.equal(controller.state.syncNeeded,true);assert.ok(controller.state.error);
 await assert.rejects(controller.send('Следующий вопрос'),error=>error.code==='recovery');
 await controller.reconnect();
 assert.equal(controller.state.syncNeeded,false);assert.equal(controller.state.error,null);
 assert.equal(controller.state.brief.destination,'AYT');
 assert.equal(calls.filter(row=>row.method==='POST').length,1);
 assert.equal(calls.filter(row=>row.path==='/api/session/'+sid).length,2);
});

test('An expired replay fails closed and a deliberate new search retains the visible conversation',async()=>{
 let attempts=0;
 const controller=new ApiTripController({fetchImpl:async()=>++attempts===1?partial():Response.json({detail:'Expired replay'},{status:410})});
 await assert.rejects(controller.send('Первый поиск'),error=>error.code==='incomplete');
 await assert.rejects(controller.retry(),error=>error.status===410);
 assert.equal(attempts,2);assert.equal(controller.state.sessionLost,true);assert.equal(controller.state.pendingRequest,null);
 assert.equal(controller.state.messages[1].text,'Полученная часть');
 await assert.rejects(controller.send('Следующий вопрос'),error=>error.code==='recovery');
 controller.restartSession();
 assert.equal(controller.state.sessionLost,false);assert.equal(controller.state.sessionId,null);
 assert.deepEqual(controller.state.messages.slice(0,2).map(row=>row.text),['Первый поиск','Полученная часть']);
 assert.match(controller.state.messages[2].text,/История сохранена/);
});

test('A terminal agent error rolls back provisional trip events while preserving the conversation',async()=>{
 const controller=new ApiTripController({fetchImpl:async()=>eventResponse([
  ['brief',{brief:{destination:'DXB'},changes:[{field:'destination'}]}],
  ['booking',{cart_id:'CART-provisional',status:'awaiting_travelers'}],
  ['delta',{text:'Предварительный ответ'}],
  ['error',{message:'Не удалось завершить ход'}],
 ])});
 controller.state.sessionId=sid;controller.state.brief={destination:'AYT'};controller.state.version=2;
 await assert.rejects(controller.send('Сменить направление'),error=>error.code==='agent');
 assert.equal(controller.state.pendingRequest,null);assert.equal(controller.state.cart,null);
 assert.deepEqual(controller.state.brief,{destination:'AYT'});assert.equal(controller.state.version,2);
 assert.equal(controller.state.messages[1].text,'Предварительный ответ');assert.equal(controller.state.messages[1].error,true);
});

test('Old response callbacks and cleanup cannot overwrite a new conversation after reset',async()=>{
 const started=deferred(),oldResponse=deferred();let attempts=0;
 const controller=new ApiTripController({fetchImpl:async path=>{
  if(path==='/api/chat'){if(++attempts===1){started.resolve();return oldResponse.promise;}return complete('Новый ответ');}
  return Response.json(snapshot);
 }});
 const oldSend=controller.send('Старый запрос');await started.promise;
 controller.reset();await controller.send('Новый запрос');
 oldResponse.resolve(complete('Запоздалый старый ответ'));assert.equal(await oldSend,null);
 assert.deepEqual(controller.state.messages.map(row=>row.text),['Новый запрос','Новый ответ']);
 assert.equal(controller.state.busy,false);assert.equal(controller.state.pendingRequest,null);
});

test('An old failed connection probe cannot mark a subsequent successful chat offline',async()=>{
 const health=deferred(),started=deferred();
 const controller=new ApiTripController({fetchImpl:async path=>{
  if(path==='/health'){started.resolve();return health.promise;}
  if(path==='/api/chat')return complete();
  return Response.json(snapshot);
 }});
 const probe=controller.reconnect();await started.promise;
 await controller.send('Поездка');health.resolve(Response.json({detail:'Unavailable'},{status:503}));await probe;
 assert.equal(controller.state.connection,'online');assert.equal(controller.state.messages[1].text,'Полный ответ');
 assert.equal(controller.state.busy,false);
});

test('Browser recovery storage contains order references but excludes traveler details and full checkout documents',()=>{
 const storage=memoryStorage();const controller=new ApiTripController({storage});
 controller.state.sessionId=sid;
 controller.state.cart={cart_id:'CART-private',status:'ready_for_demo_payment',travelers:[{full_name:'PRIVATE TRAVELER',birth_date:'1990-01-01'}],package:{private_data:'PRIVATE PACKAGE'}};
 const order={order_id:'DEMO-private',travelers:[{full_name:'PRIVATE TRAVELER'}],voucher:'PRIVATE DOCUMENT'};
 controller.state.order=order;controller.state.orders=[{sessionId:sid,order}];controller.remember();
 const saved=storage.value();assert.equal(saved.order,null);
 assert.deepEqual(saved.orders,[{sessionId:sid,orderId:'DEMO-private'}]);
 assert.doesNotMatch(JSON.stringify(saved),/PRIVATE TRAVELER|1990-01-01|PRIVATE DOCUMENT|PRIVATE PACKAGE/);
});

test('Unavailable browser storage cannot prevent a chat from completing',async()=>{
 const storage={getItem:()=>{throw new Error('Blocked storage');},setItem:()=>{throw new Error('Quota');}};
 const controller=new ApiTripController({storage,fetchImpl:async path=>path==='/api/chat'?complete():Response.json(snapshot)});
 await controller.restore();await controller.send('Поездка');
 assert.equal(controller.state.messages[1].text,'Полный ответ');assert.equal(controller.state.pendingRequest,null);
 assert.match(controller.state.storageWarning,/Не удалось сохранить/);
});

test('A malformed terminal event cannot discard the pending recovery record for either a first or continued turn',async()=>{
 for(const sessionId of [null,sid]){
  const controller=new ApiTripController({fetchImpl:async()=>eventResponse([['delta',{text:'Ответ без сохранённой сессии'}],['done',{}]])});
  controller.state.sessionId=sessionId;
  await assert.rejects(controller.send('Поездка'),error=>error.code==='protocol');
  assert.ok(controller.state.pendingRequest,'Only a valid terminal session confirmation can clear a pending request');
  assert.equal(controller.state.messages[1].text,'Ответ без сохранённой сессии');
  assert.equal(controller.state.messages[1].interrupted,true);assert.equal(controller.state.syncNeeded,false);
 }
});
