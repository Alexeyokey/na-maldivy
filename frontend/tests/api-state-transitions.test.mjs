import test from 'node:test';
import assert from 'node:assert/strict';
import {ApiTripController} from '../dist/api-model.js';

// Synthetic fixtures only: no model requests, live sessions or saved browser data.
const sid='state-transition-session';
const initialBrief={origin:'MOW',destination:'MLE',start_date:'2026-11-18',month:11,year:2026,nights:7,adults:2,children_ages:[],pets:0,needs:['beach'],mood:'Отдых'};
const changedBrief={...initialBrief,destination:'LED',transport:'train',needs:[]};
const oldPackage={index:1,destination:'MLE',can_confirm:true,hotel:{name:'Previous Maldives Hotel',check_in:'2026-11-18',check_out:'2026-11-25',nights:7,price_rub:90000},outbound:{departure_at:'2026-11-18T10:00:00+03:00',arrival_at:'2026-11-18T22:00:00+05:00',price_rub:60000},inbound:{departure_at:'2026-11-25T10:00:00+05:00',price_rub:60000},known_total_rub:210000,unpriced_components:[],issues:[]};
const newPackage={...structuredClone(oldPackage),destination:'DXB',hotel:{...oldPackage.hotel,name:'Replacement Dubai Hotel'}};
const train={index:1,title:'Москва → Петербург',departure_at:'2026-11-18T10:00:00+03:00',price_rub:12000};
const cart={cart_id:'CART-transition',session_id:sid,status:'ready_for_demo_payment',package:oldPackage,is_simulated:true};
const order={order_id:'DEMO-transition',cart_id:cart.cart_id,session_id:sid,status:'paid_demo',is_simulated:true,package:oldPackage};
const eventResponse=events=>new Response(events.map(([type,data])=>`event: ${type}\ndata: ${JSON.stringify(data)}\n\n`).join(''),{headers:{'Content-Type':'text/event-stream'}});
const done=(stage='collecting',version=8)=>({session_id:sid,stage,version});
const snapshot=(overrides={})=>({...done(),brief:changedBrief,sources:{destination:'explicit',transport:'explicit'},selected:null,stale:false,booking:null,...overrides});
const memoryStorage=()=>{let stored=null;return {getItem:()=>stored,setItem:(_,value)=>{stored=value;}};};
const deferred=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};};

function seed(controller,{stage='packages_shown',selected=null,paid=false}={}){
 Object.assign(controller.state,{
  sessionId:sid,version:7,stage,brief:structuredClone(initialBrief),sources:{destination:'explicit'},
  needs:[{code:'beach',label:'У пляжа'}],options:[structuredClone(oldPackage)],optionKind:'packages',selected,
  cart:stage==='checkout_pending'||paid?structuredClone(cart):null,
  order:paid?structuredClone(order):null,currentOrderId:paid?order.order_id:null,
  orders:paid?[{sessionId:sid,order:structuredClone(order)}]:[],
  profile:{status:'available',evidence:[{quote:'PREVIOUS HOTEL EVIDENCE'}]},
  savings:{checked_dates:[{status:'quoted'}],suggestions:[{suggested_departure:'2026-11-17'}]},
  notes:['PREVIOUS ROUTE NOTE'],assumptions:[{field:'nights',text:'PREVIOUS ROUTE ASSUMPTION'}],
  question:{text:'PREVIOUS ROUTE QUESTION',quick_replies:['Мальдивы']},connection:'online',
 });
 return controller;
}
function assertClearedResults(state){
 assert.deepEqual(state.options,[],'The preceding destination must not retain selectable offer cards');
 assert.equal(state.optionKind,null);
 assert.equal(state.selected,null);assert.equal(state.cart,null);assert.equal(state.order,null);
 assert.equal(state.currentOrderId,null);assert.equal(state.profile,null);assert.equal(state.savings,null);
 assert.deepEqual(state.notes,[]);assert.deepEqual(state.assumptions,[]);
 assert.deepEqual(state.needs,[],'The sidebar cannot keep needs absent from the authoritative brief');
}
const tripKeys=['sessionId','version','stage','brief','sources','needs','options','optionKind','selected','cart','order','currentOrderId','question','profile','savings','stale','notes','assumptions','assumptionsAccepted','acceptedAssumptions'];
const trip=state=>Object.fromEntries(tripKeys.map(key=>[key,structuredClone(state[key])]));

test('A changed authoritative brief clears old offers even when done already applied the new version',async()=>{
 const calls=[];
 const controller=seed(new ApiTripController({fetchImpl:async(path,init={})=>{
  calls.push({path,method:init.method||'GET'});
  if(path==='/api/chat')return eventResponse([['delta',{text:'Отменяем прошлый выбор. Обсудим Петербург.'}],['done',done()]]);
  assert.equal(path,'/api/session/'+sid);return Response.json(snapshot());
 }}));
 await controller.send('Так а если отмена и я хочу в питер');
 assert.equal(controller.state.brief.destination,'LED');assert.equal(controller.state.stage,'collecting');
 assertClearedResults(controller.state);
 assert.equal(controller.state.question,null);assert.equal(controller.state.syncNeeded,false);
 assert.equal(calls.filter(row=>row.method==='POST').length,1);
 assert.deepEqual(controller.state.messages.map(row=>row.text),['Так а если отмена и я хочу в питер','Отменяем прошлый выбор. Обсудим Петербург.']);
});

test('A same-version cancellation snapshot removes the preceding selected offer and its dependent panels',async()=>{
 const controller=seed(new ApiTripController({fetchImpl:async()=>Response.json(snapshot({version:7,brief:{...initialBrief,needs:[]}}))}),{stage:'awaiting_confirmation',selected:1});
 await controller.sessionSnapshot();
 assertClearedResults(controller.state);
 assert.equal(controller.state.stage,'collecting');
});

test('Reload reconciles a same-version cancelled session instead of resurrecting persisted package cards',async()=>{
 const storage=memoryStorage();seed(new ApiTripController({storage}),{stage:'checkout_pending',selected:1}).remember();
 const calls=[];
 const restored=new ApiTripController({storage,fetchImpl:async(path,init={})=>{
  calls.push({path,method:init.method||'GET'});return Response.json(snapshot({version:7,brief:{...initialBrief,needs:[]}}));
 }});
 await restored.restore();
 assertClearedResults(restored.state);
 assert.equal(restored.state.sessionLost,false);assert.equal(restored.state.syncNeeded,false);
 assert.deepEqual(calls,[{path:'/api/session/'+sid,method:'GET'}],'Restoration cannot search, pay or reopen the cancelled cart');
});

test('A full changed brief invalidates old cards during streaming even if changes metadata is absent',{timeout:5000},async()=>{
 const observed=deferred();
 const encoder=new TextEncoder();let push;
 const response=new Response(new ReadableStream({start(controller){push=controller;}}),{headers:{'Content-Type':'text/event-stream'}});
 const controller=seed(new ApiTripController({onChange:state=>{if(state.brief.destination==='LED')observed.resolve();},fetchImpl:async path=>path==='/api/chat'?response:Response.json(snapshot())}),{stage:'awaiting_confirmation',selected:1});
 const sending=controller.send('Теперь хочу в Петербург');
 push.enqueue(encoder.encode(`event: brief\ndata: ${JSON.stringify({brief:changedBrief,sources:{destination:'explicit'},needs:[]})}\n\n`));
 await observed.promise;
 // Observe before done/GET: the stale cards cannot remain usable during a long answer.
 const provisional=trip(controller.state);
 push.enqueue(encoder.encode(`event: done\ndata: ${JSON.stringify(done())}\n\n`));push.close();
 await sending;
 assertClearedResults(provisional);
});

test('A replacement options event cannot pair new cards with the preceding selection or checkout',async()=>{
 const views=[];
 const newBrief={...initialBrief,destination:'DXB',needs:[]};
 const controller=seed(new ApiTripController({onChange:state=>{if(state.options[0]?.hotel?.name===newPackage.hotel.name)views.push(trip(state));},fetchImpl:async path=>path==='/api/chat'?eventResponse([
  ['options',{kind:'packages',items:[newPackage],notes:[],assumptions:[]}],
  ['done',done('packages_shown')],
 ]):Response.json(snapshot({stage:'packages_shown',brief:newBrief}))}),{stage:'checkout_pending',selected:1});
 await controller.send('Обнови варианты в Дубай');
 assert.ok(views.length>0);
 for(const view of views){assert.equal(view.selected,null);assert.equal(view.cart,null);assert.equal(view.order,null);assert.equal(view.profile,null);assert.equal(view.savings,null);}
 assert.equal(controller.state.options[0]?.hotel.name,newPackage.hotel.name,'A fresh SSE result must survive the following matching snapshot');
 assert.equal(controller.state.stage,'packages_shown');
});

test('Changing a paid trip starts fresh results while the paid history and original document ownership remain intact',async()=>{
 const calls=[];
 const controller=seed(new ApiTripController({fetchImpl:async(path,init={})=>{
  calls.push({path,method:init.method||'GET',sessionId:init.headers?.['X-Session-ID']});
  if(path==='/api/chat')return eventResponse([
   ['brief',{brief:changedBrief,sources:{destination:'explicit'},needs:[],changes:[{field:'destination',old:'MLE',new:'LED'}]}],
   ['options',{kind:'trains',items:[train],notes:[],assumptions:[]}],
   ['done',done('trains_shown')],
  ]);
  if(path==='/api/session/'+sid)return Response.json(snapshot({stage:'trains_shown'}));
  if(path==='/api/checkout/orders/'+order.order_id+'/voucher')return new Response('<html>DEMO DOCUMENT</html>',{headers:{'Content-Type':'text/html'}});
  throw new Error('Unexpected request: '+path);
 }}),{stage:'booked',selected:1,paid:true});
 const history=structuredClone(controller.state.orders);
 await controller.send('Отменим текущий поиск, я хочу в Питер');
 assert.equal(controller.state.optionKind,'trains');assert.equal(controller.state.options[0]?.title,train.title);
 assert.equal(controller.state.cart,null);assert.equal(controller.state.order,null);assert.equal(controller.state.currentOrderId,null);
 assert.deepEqual(controller.state.orders,history,'A new search cannot erase or rewrite an already paid order');
 assert.ok((await controller.download('voucher',controller.state.orders[0])).blob instanceof Blob);
 assert.equal(calls.at(-1).sessionId,sid);
 assert.equal(calls.filter(row=>row.method==='POST').length,1,'Changing criteria cannot create another checkout or payment');
});

test('A terminal failure rolls back all provisional criteria and panels without losing paid history',async()=>{
 const controller=seed(new ApiTripController({fetchImpl:async path=>{
  assert.equal(path,'/api/chat');return eventResponse([
   ['brief',{brief:changedBrief,needs:[],changes:[{field:'destination'}]}],
   ['options',{kind:'trains',items:[train],notes:[],assumptions:[]}],
   ['delta',{text:'Предварительно посмотрим Петербург.'}],
   ['error',{message:'Не удалось завершить обновление'}],
  ]);
 }}),{stage:'booked',selected:1,paid:true});
 const before=trip(controller.state),history=structuredClone(controller.state.orders);
 await assert.rejects(controller.send('Поменяем направление'),error=>error.code==='agent');
 assert.deepEqual(trip(controller.state),before);
 assert.deepEqual(controller.state.orders,history);assert.equal(controller.state.pendingRequest,null);
 assert.equal(controller.state.messages.length,2);assert.equal(controller.state.messages[1].error,true);
 assert.equal(controller.state.syncNeeded,true,'A terminal rollback must reconcile server state before further mutations');
});

test('Retrying a changed-criteria turn reuses its original request and never duplicates cards, user bubbles or paid history',async()=>{
 const requests=[];let attempts=0;
 const brief=['brief',{brief:changedBrief,needs:[],changes:[{field:'destination',old:'MLE',new:'LED'}]}];
 const controller=seed(new ApiTripController({fetchImpl:async(path,init={})=>{
  if(path!=='/api/chat')return Response.json(snapshot({stage:'trains_shown'}));
  requests.push(JSON.parse(init.body));
  return ++attempts===1?eventResponse([brief,['delta',{text:'Смотрим Петербург'}]]):eventResponse([brief,['options',{kind:'trains',items:[train],notes:[],assumptions:[]}],['delta',{text:'Смотрим Петербург. Вот поезд.'}],['done',done('trains_shown')]]);
 }}),{stage:'booked',selected:1,paid:true});
 const history=structuredClone(controller.state.orders);
 await assert.rejects(controller.send('Так а если отмена и я хочу в питер'),error=>error.code==='incomplete');
 assert.equal(controller.state.options.length,0);assert.equal(controller.state.order,null);
 await controller.retry();
 assert.equal(requests.length,2);assert.equal(requests[1].retry,true);
 for(const key of ['message','session_id','request_id'])assert.equal(requests[0][key],requests[1][key]);
 assert.equal(requests[1].session_id,sid);
 assert.deepEqual(controller.state.options,[train]);assert.equal(controller.state.selected,null);
 assert.equal(controller.state.messages.length,2);assert.equal(controller.state.messages[1].text,'Смотрим Петербург. Вот поезд.');
 assert.deepEqual(controller.state.orders,history);assert.equal(controller.state.pendingRequest,null);
});

test('A mood-only brief update preserves still-valid offers and selection instead of resetting the search',async()=>{
 const relaxed={...initialBrief,mood:'Спокойный отдых'};
 const controller=seed(new ApiTripController({fetchImpl:async path=>path==='/api/chat'?eventResponse([
  ['brief',{brief:relaxed,sources:{mood:'explicit'},needs:[{code:'beach',label:'У пляжа'}],changes:[{field:'mood',old:'Отдых',new:'Спокойный отдых'}]}],
  ['done',done('awaiting_confirmation')],
 ]):Response.json(snapshot({stage:'awaiting_confirmation',brief:relaxed,selected:1}))}),{stage:'awaiting_confirmation',selected:1});
 await controller.send('Настроение — спокойный отдых');
 assert.deepEqual(controller.state.options,[oldPackage]);assert.equal(controller.state.optionKind,'packages');
 assert.equal(controller.state.selected,1);assert.equal(controller.state.stale,false);
 assert.equal(controller.state.brief.mood,'Спокойный отдых');
});

test('An unchanged snapshot preserves existing results and the server-selected package',async()=>{
 const controller=seed(new ApiTripController({fetchImpl:async()=>Response.json(snapshot({version:7,stage:'awaiting_confirmation',brief:initialBrief,selected:1}))}),{stage:'packages_shown'});
 await controller.sessionSnapshot();
 assert.deepEqual(controller.state.options,[oldPackage]);assert.equal(controller.state.optionKind,'packages');
 assert.equal(controller.state.selected,1);assert.equal(controller.state.stage,'awaiting_confirmation');
});

test('A terminal cancellation clears old result panels even if its follow-up snapshot cannot be read',async()=>{
 const controller=seed(new ApiTripController({fetchImpl:async path=>{
  if(path==='/api/chat')return eventResponse([
   ['delta',{text:'Отменил прошлый поиск. Уточним новую поездку.'}],
   ['done',{...snapshot({version:7}),options:{kind:'trains',items:[],assumptions:[],notes:[]},needs:[]}],
  ]);
  throw new TypeError('Disconnected after completed turn');
 }}),{stage:'checkout_pending',selected:1});
 await controller.send('Так а если отмена и я хочу в питер');
 assertClearedResults(controller.state);assert.equal(controller.state.brief.destination,'LED');
 assert.equal(controller.state.pendingRequest,null);assert.equal(controller.state.syncNeeded,true);
 assert.equal(controller.state.messages[1].text,'Отменил прошлый поиск. Уточним новую поездку.');
 await assert.rejects(controller.send('Следующее сообщение'),error=>error.code==='recovery');
});

test('Reload hydrates authoritative replacement results instead of clearing them because the saved version is older',async()=>{
 const storage=memoryStorage();seed(new ApiTripController({storage}),{stage:'awaiting_confirmation',selected:1}).remember();
 const calls=[],notes=['NEW TRAIN NOTE'],assumptions=[{field:'seat_class',text:'Купе по умолчанию'}];
 const restored=new ApiTripController({storage,fetchImpl:async(path,init={})=>{
  calls.push({path,method:init.method||'GET'});
  return Response.json(snapshot({stage:'trains_shown',needs:[],options:{kind:'trains',items:[train],notes,assumptions}}));
 }});
 await restored.restore();
 assert.equal(restored.state.brief.destination,'LED');assert.equal(restored.state.optionKind,'trains');
 assert.deepEqual(restored.state.options,[train]);assert.deepEqual(restored.state.notes,notes);
 assert.deepEqual(restored.state.assumptions,assumptions);assert.equal(restored.state.profile,null);assert.equal(restored.state.savings,null);
 assert.deepEqual(restored.state.needs,[]);assert.equal(restored.state.cart,null);assert.equal(restored.state.selected,null);
 assert.deepEqual(calls,[{path:'/api/session/'+sid,method:'GET'}]);
});

test('Changed criteria invalidate dependent evidence even when the server returns the same package items',async()=>{
 const brief={...initialBrief,refundable_only:true},envelope={kind:'packages',items:[oldPackage],notes:[],assumptions:[]};
 const controller=seed(new ApiTripController({fetchImpl:async()=>Response.json(snapshot({stage:'packages_shown',brief,needs:[],options:envelope}))}));
 await controller.sessionSnapshot();
 assert.deepEqual(controller.state.options,[oldPackage],'Authoritative still-valid offers remain available');
 assert.equal(controller.state.profile,null,'Evidence from a preceding set of requirements must not look freshly checked');
 assert.equal(controller.state.savings,null);assert.equal(controller.state.question,null);
});

test('A malformed options snapshot is rejected atomically before any trip or paid-history mutation',async()=>{
 for(const invalid of [{notes:{}},{assumptions:{}},{items:[null]},{items:[{}]},{items:[{...oldPackage,hotel:null}]},{items:[{...oldPackage,outbound:{departure_at:5}}]},{items:[{...oldPackage,issues:{}}]},{kind:null,items:[oldPackage]}]){
  const controller=seed(new ApiTripController({fetchImpl:async()=>Response.json(snapshot({stage:'packages_shown',needs:[],options:{kind:'packages',items:[newPackage],notes:[],assumptions:[],...invalid}}))}),{stage:'booked',selected:1,paid:true});
  const before=trip(controller.state),history=structuredClone(controller.state.orders);
  await assert.rejects(controller.sessionSnapshot(),error=>error.code==='protocol',JSON.stringify(invalid));
  assert.deepEqual(trip(controller.state),before);assert.deepEqual(controller.state.orders,history);
 }
});

test('Accepting shown assumptions is a local acknowledgment without a model turn, selection or checkout',()=>{
 const calls=[],controller=seed(new ApiTripController({fetchImpl:async(path,init)=>{calls.push({path,init});throw new Error('No request expected');}}));
 const before=trip(controller.state);
 assert.equal(controller.acceptAssumptions(),true);assert.equal(controller.state.assumptionsAccepted,true);
 assert.equal(typeof controller.state.acceptedAssumptions,'string');
 const after=trip(controller.state);delete before.assumptionsAccepted;delete before.acceptedAssumptions;delete after.assumptionsAccepted;delete after.acceptedAssumptions;
 assert.deepEqual(after,before);assert.deepEqual(calls,[]);
});

test('Assumptions cannot be accepted while results are stale or an interrupted operation remains unresolved',()=>{
 for(const flags of [{busy:true},{stale:true},{syncNeeded:true},{sessionLost:true},{pendingRequest:{id:'pending'}}]){
  const controller=seed(new ApiTripController({fetchImpl:async()=>{throw new Error('No request expected');}}));
  Object.assign(controller.state,flags);
  assert.equal(controller.acceptAssumptions(),false);assert.equal(controller.state.assumptionsAccepted,false);
 }
 const controller=new ApiTripController();assert.equal(controller.acceptAssumptions(),false);
});

test('The same authoritative assumptions remain acknowledged through GET reconciliation and browser reload',async()=>{
 const storage=memoryStorage(),calls=[];
 const sources={destination:'explicit',adults:'default',nights:'default'},assumptions=[{field:'nights',text:'На 7 ночей по умолчанию'}];
 const authoritative=snapshot({version:7,stage:'packages_shown',brief:initialBrief,sources,needs:[{code:'beach',label:'У пляжа'}],options:{kind:'packages',items:[oldPackage],notes:[],assumptions}});
 const fetchImpl=async(path,init={})=>{calls.push({path,method:init.method||'GET'});assert.equal(path,'/api/session/'+sid);return Response.json(authoritative);};
 const controller=seed(new ApiTripController({storage,fetchImpl}));controller.state.sources=sources;controller.state.assumptions=assumptions;
 assert.equal(controller.acceptAssumptions(),true);
 await controller.sessionSnapshot();assert.equal(controller.state.assumptionsAccepted,true);controller.remember();
 const restored=new ApiTripController({storage,fetchImpl});await restored.restore();
 assert.equal(restored.state.assumptionsAccepted,true);assert.deepEqual(restored.state.options,[oldPackage]);
 assert.ok(calls.every(row=>row.method==='GET'),'Acknowledging assumptions cannot confirm or pay for a trip');
});

test('A criteria change removes the previous local acknowledgment of assumptions',async()=>{
 const controller=seed(new ApiTripController({fetchImpl:async path=>path==='/api/chat'?eventResponse([
  ['brief',{brief:changedBrief,needs:[],search_changed:true,invalidate_options:true}],
  ['done',done()],
 ]):Response.json(snapshot())}));
 assert.equal(controller.acceptAssumptions(),true);
 await controller.send('Вместо Мальдив хочу в Питер');
 assert.equal(controller.state.assumptionsAccepted,false);assert.equal(controller.state.acceptedAssumptions,null);
});

test('An empty search retains its server explanation through done, snapshot and reload without reviving old offers',async()=>{
 const storage=memoryStorage(),calls=[],reason='Нет подтверждённых котировок на выбранные даты.';
 const empty=snapshot({brief:{...initialBrief,destination:'DXB',needs:[]},needs:[],options:{kind:'packages',items:[],notes:[reason],assumptions:[]}});
 const fetchImpl=async(path,init={})=>{
  calls.push({path,method:init.method||'GET'});
  if(path==='/api/chat')return eventResponse([['delta',{text:reason}],['done',empty]]);
  assert.equal(path,'/api/session/'+sid);return Response.json(empty);
 };
 const controller=seed(new ApiTripController({storage,fetchImpl}),{stage:'checkout_pending',selected:1});
 await controller.send('Хочу в Дубай на эти же даты');
 const restored=new ApiTripController({storage,fetchImpl});await restored.restore();
 for(const state of [controller.state,restored.state]){
  assert.equal(state.stage,'collecting');assert.equal(state.optionKind,'packages');
  assert.deepEqual(state.options,[]);assert.deepEqual(state.notes,[reason]);
  assert.equal(state.selected,null);assert.equal(state.cart,null);assert.equal(state.order,null);
  assert.equal(state.profile,null);assert.equal(state.savings,null);assert.equal(state.syncNeeded,false);
 }
 assert.equal(calls.filter(row=>row.method==='POST').length,1,'Reloading an empty result cannot silently invoke a new search');
});
