import test from 'node:test';
import assert from 'node:assert/strict';
import {ApiTripController,applyAgentEvent,createApiModel} from '../dist/api-model.js';
import {normalizeTripDiff,renderTripChanges} from '../dist/trip-usability.js';
import {normalizeFavoriteIntent} from '../dist/favorites.js';
import {streamChat} from '../dist/api.js';

const sid='diff-test-session';
const brief={transport:'flight',origin:'MOW',destination:'MLE',adults:2,children_ages:[],pets:0,nights:7,start_date:'2026-11-18',direct_only:false};
const option={index:1,kind:'package',hotel:{name:'Новый отель',check_in:'2026-11-18',check_out:'2026-11-25'},outbound:{departure_at:'2026-11-18T12:00:00Z'},inbound:{departure_at:'2026-11-25T12:00:00Z'}};
const envelope={kind:'packages',items:[option],assumptions:[],notes:[]};
const diff=(version=2)=>({
 schema_version:1,version,status:'updated',criteria_changes:[{field:'direct_only',before:false,after:true,source:'explicit'}],
 before:{kind:'package',name:'Старый отель',known_total_rub:100000,components:[{key:'outbound',known_cost_rub:40000,source:{kind:'synthetic',access_token:'diff-private-token'}}],session_id:'diff-private-capability'},
 after:{kind:'package',name:'Новый отель',known_total_rub:90000,components:[{key:'outbound',known_cost_rub:30000,source:{kind:'provider_quote_with_modelled_extras'}}]},
 comparison:{delta_known_total_rub:-10000,same_basis:false,differences:['hotel'],savings_guaranteed:true},order_id:'diff-private-capability',
});
const eventResponse=events=>new Response(events.map(([type,data])=>'event: '+type+'\ndata: '+JSON.stringify(data)+'\n\n').join(''),{headers:{'Content-Type':'text/event-stream'}});
const currentModel=()=>Object.assign(createApiModel(),{sessionId:sid,version:1,stage:'packages_shown',brief:structuredClone(brief),optionKind:'packages',options:[structuredClone(option)],stale:false});
const snapshot=(overrides={})=>({session_id:sid,version:2,stage:'packages_shown',brief:{...brief,direct_only:true},sources:{direct_only:'explicit'},needs:[],selected:null,booking:null,stale:false,options:envelope,last_diff:diff(),...overrides});
const storage=initial=>{let saved=initial===undefined?null:JSON.stringify(initial);return {getItem:()=>saved,setItem:(_,value)=>saved=value,read:()=>JSON.parse(saved)};};

test('The SSE brief/options/diff/done sequence keeps a normalized comparison for its matching revision',()=>{
 const state=currentModel(),raw=diff();
 applyAgentEvent(state,'brief',{brief:{...brief,direct_only:true},sources:{direct_only:'explicit'},needs:[],search_changed:true,version:2,stage:'collecting'});
 assert.equal(state.lastDiff,null);assert.equal(state.stale,true);
 applyAgentEvent(state,'options',envelope);applyAgentEvent(state,'diff',raw);
 assert.deepEqual(state.lastDiff,normalizeTripDiff(raw,2));assert.equal(state.stale,false);
 applyAgentEvent(state,'done',snapshot());
 assert.deepEqual(state.lastDiff,normalizeTripDiff(raw,2));assert.equal(state.version,2);
 assert.match(renderTripChanges(state),/Изменение известной суммы/);
 assert.doesNotMatch(JSON.stringify(state.lastDiff),/diff-private-token|diff-private-capability/);
});

test('A meaningful criteria edit clears the previous diff; reaffirming an identical source does not invent a new revision',()=>{
 const state=currentModel();state.lastDiff=normalizeTripDiff(diff(1),1);
 const previous=structuredClone(state.lastDiff);
 applyAgentEvent(state,'brief',{brief:structuredClone(brief),sources:{adults:'explicit'},needs:[],search_changed:false,version:1});
 assert.deepEqual(state.lastDiff,previous);assert.equal(state.options.length,1);
 applyAgentEvent(state,'brief',{brief:{...brief,adults:3},sources:{adults:'explicit'},needs:[],invalidate_options:true,version:2});
 assert.equal(state.lastDiff,null);assert.equal(state.options.length,0);assert.equal(state.stale,true);
 applyAgentEvent(state,'diff',diff(1));assert.equal(state.lastDiff,null,'A late comparison for the old passenger group cannot reappear');
});

test('Done is authoritative about a replacement, an explicit null, and a diff from the wrong version',()=>{
 const state=currentModel();state.lastDiff=normalizeTripDiff(diff(1),1);
 applyAgentEvent(state,'done',snapshot());assert.equal(state.lastDiff.version,2);
 applyAgentEvent(state,'done',snapshot({last_diff:null}));assert.equal(state.lastDiff,null);
 applyAgentEvent(state,'done',snapshot({version:3,last_diff:diff(2)}));assert.equal(state.lastDiff,null);
 for(const value of [null,{},[],{...diff(3),criteria_changes:3},{...diff(3),status:'paid_demo'}]){
  assert.doesNotThrow(()=>applyAgentEvent(state,'diff',value));assert.equal(state.lastDiff,null);
 }
});

test('A complete HTTP-backed chat stores the same safe comparison emitted by SSE and reconciled by snapshot',async()=>{
 const stored=storage(),seen=[],calls=[];
 const controller=new ApiTripController({storage:stored,onChange:state=>{if(state.lastDiff)seen.push(structuredClone(state.lastDiff));},fetchImpl:async(path,init={})=>{
  calls.push({path,method:init.method||'GET'});
  if(path==='/api/chat')return eventResponse([
   ['brief',{brief:{...brief,direct_only:true},sources:{direct_only:'explicit'},needs:[],search_changed:true,version:2,stage:'collecting'}],
   ['options',envelope],['diff',diff()],['delta',{text:'Пересчитали варианты.'}],['done',snapshot()],
  ]);
  if(path==='/api/session/'+sid)return Response.json(snapshot());
  throw new Error('Unexpected test route');
 }});
 Object.assign(controller.state,currentModel());
 await controller.send('Только прямые рейсы, без оформления.');
 const expected=normalizeTripDiff(diff(),2);
 assert.deepEqual(controller.state.lastDiff,expected);assert.equal(controller.state.busy,false);assert.equal(controller.state.syncNeeded,false);
 assert.ok(seen.length>1);assert.ok(seen.every(row=>JSON.stringify(row)===JSON.stringify(expected)));
 assert.deepEqual(stored.read().lastDiff,expected);assert.deepEqual(stored.read().pendingRequest,null);
 assert.doesNotMatch(JSON.stringify(stored.read().lastDiff),/diff-private-token|diff-private-capability/);
 assert.deepEqual(calls,[{path:'/api/chat',method:'POST'},{path:'/api/session/'+sid,method:'GET'}]);
});

test('Snapshot reconciliation replaces or removes the diff without retaining a comparison for old conditions',async()=>{
 let response=snapshot();
 const controller=new ApiTripController({fetchImpl:async()=>Response.json(response)});Object.assign(controller.state,currentModel());
 await controller.sessionSnapshot();assert.deepEqual(controller.state.lastDiff,normalizeTripDiff(diff(),2));
 response=snapshot({version:3,brief:{...brief,adults:3},last_diff:null});
 await controller.sessionSnapshot();assert.equal(controller.state.lastDiff,null);assert.equal(controller.state.brief.adults,3);
 response=snapshot({version:4,last_diff:diff(3)});
 await controller.sessionSnapshot();assert.equal(controller.state.lastDiff,null);
 const before=structuredClone(controller.state);response=snapshot({version:2});
 await assert.rejects(controller.sessionSnapshot(),error=>error.code==='protocol');
 assert.deepEqual(controller.state,before,'A regressed HTTP snapshot cannot replace the current brief or revive an older diff');
});

test('Restored browser storage normalizes the diff before its first notification and verifies it against the server',async()=>{
 const raw=diff(),saved=storage({schemaVersion:2,sessionId:sid,version:2,stage:'packages_shown',brief:{...brief,direct_only:true},optionKind:'packages',options:[option],lastDiff:raw,messages:[],orders:[]}),seen=[];
 const controller=new ApiTripController({storage:saved,onChange:state=>seen.push(structuredClone(state.lastDiff)),fetchImpl:async path=>{
  assert.equal(path,'/api/session/'+sid);return Response.json(snapshot());
 }});
 await controller.restore();const expected=normalizeTripDiff(raw,2);
 assert.deepEqual(controller.state.lastDiff,expected);assert.ok(seen.length);
 assert.ok(seen.every(value=>JSON.stringify(value)===JSON.stringify(expected)));
 assert.doesNotMatch(JSON.stringify(saved.read().lastDiff),/diff-private-token|diff-private-capability/);
 const stale=storage({schemaVersion:2,version:3,brief:{},lastDiff:raw,orders:[]});
 const noSession=new ApiTripController({storage:stale,fetchImpl:()=>assert.fail('There is no session to fetch')});
 await noSession.restore();assert.equal(noSession.state.lastDiff,null);assert.equal(stale.read().lastDiff,null);
});

test('Reset, restart and expired sessions discard the diff while keeping known paid history',()=>{
 for(const action of ['reset','restartSession','markSessionLost']){
  const controller=new ApiTripController({fetchImpl:()=>assert.fail('No HTTP is necessary')});Object.assign(controller.state,currentModel());
  controller.state.lastDiff=normalizeTripDiff(diff(1),1);
  controller.state.orders=[{sessionId:'previous-trip',order:{order_id:'DEMO-existing',status:'paid_demo'}}];
  controller.state.messages=[{role:'user',text:'Предыдущий запрос'}];
  controller[action]();
  assert.equal(controller.state.lastDiff,null);assert.equal(controller.state.orders.length,1);assert.equal(controller.state.orders[0].order.order_id,'DEMO-existing');
  assert.equal(renderTripChanges(controller.state),'');
 }
});

test('A late snapshot cannot restore its comparison after the user starts a new trip',async()=>{
 let resolve;const pendingResponse=new Promise(done=>resolve=done);
 const controller=new ApiTripController({fetchImpl:()=>pendingResponse});Object.assign(controller.state,currentModel());
 const pending=controller.sessionSnapshot();controller.reset();controller.state.sessionId='new-trip';
 resolve(Response.json(snapshot()));assert.equal(await pending,null);
 assert.equal(controller.state.sessionId,'new-trip');assert.equal(controller.state.lastDiff,null);assert.equal(controller.state.version,0);
});

test('A terminal agent error restores the earlier conditions and their comparison instead of retaining a provisional new diff',async()=>{
 const controller=new ApiTripController({fetchImpl:async()=>eventResponse([
  ['brief',{brief:{...brief,direct_only:true},search_changed:true,version:2}],['options',envelope],['diff',diff()],['error',{message:'Не удалось завершить пересчёт.'}],
 ])});
 Object.assign(controller.state,currentModel());const earlier=normalizeTripDiff(diff(1),1);controller.state.lastDiff=earlier;
 await assert.rejects(controller.send('Только прямые рейсы'),error=>error.code==='agent');
 assert.equal(controller.state.version,1);assert.equal(controller.state.brief.direct_only,false);assert.deepEqual(controller.state.lastDiff,earlier);
 assert.equal(controller.state.pendingRequest,null);assert.equal(controller.state.syncNeeded,true);
});

const favoriteIntent=(verified=false)=>normalizeFavoriteIntent({
 hotel:{hotel_id:'osm:way/123456',name:'Сохранённый отель'},
 criteria:{transport:'flight',origin:'MOW',destination:'MLE',start_date:'2026-11-18',nights:7,adults:2,children_ages:[5,5],pets:1,budget_rub:300000,min_stars:4,needs:['child','pets','quiet']},
 rememberedKnownTotalRub:290000,priceKind:'historical_known_components',savedAt:'2026-10-29T12:00:00Z',verified,
});

test('Favorite restore intent survives ordinary snapshots and browser reload as public criteria, not a hidden access capability',async()=>{
 for(const verified of [false,true]){
  const persisted=storage(),intent=favoriteIntent(verified);
  const first=new ApiTripController({storage:persisted,fetchImpl:()=>assert.fail('Saving cannot make an HTTP request')});
  Object.assign(first.state,currentModel(),{favoriteIntent:intent,favoriteRestoreUnconfirmed:false});first.remember();
  assert.deepEqual(persisted.read().favoriteIntent,intent);
  const restored=new ApiTripController({storage:persisted,fetchImpl:async()=>Response.json(snapshot())});
  await restored.restore();
  assert.deepEqual(restored.state.favoriteIntent,intent);assert.equal(restored.state.favoriteRestoreUnconfirmed,false);
  assert.equal(restored.state.favoriteIntent.verified,verified);
  assert.doesNotMatch(JSON.stringify(persisted.read().favoriteIntent),/session_id|cart_id|order_id|birth_date|travelers|voucher_path/);
 }
});

test('Informational brief updates and rebuilds cannot silently discard an unverified restored intent',()=>{
 const state=currentModel(),intent=favoriteIntent();state.favoriteIntent=intent;state.favoriteRestoreUnconfirmed=false;
 applyAgentEvent(state,'brief',{brief:structuredClone(brief),search_changed:false,version:1,sources:{adults:'explicit'}});
 applyAgentEvent(state,'done',{session_id:sid,version:1,stage:'packages_shown'});
 assert.deepEqual(state.favoriteIntent,intent);assert.equal(state.favoriteIntent.verified,false);
 applyAgentEvent(state,'brief',{brief:{...brief,budget_rub:100000},search_changed:true,version:2});
 assert.equal(state.lastDiff,null);assert.deepEqual(state.favoriteIntent,intent,'Losing a favorite condition during rebuild must retain the warning context');
});

test('An invalid persisted favorite intent fails closed across snapshots until an explicit new trip clears it',async()=>{
 for(const raw of [{},{...favoriteIntent(),criteria:{...favoriteIntent().criteria,pets:99}},{...favoriteIntent(),hotel:{hotel_id:'CART-private',name:'Unknown'}}]){
  const persisted=storage({schemaVersion:2,sessionId:sid,version:2,stage:'packages_shown',brief,optionKind:'packages',options:[option],favoriteIntent:raw,favoriteRestoreUnconfirmed:false,messages:[],orders:[]});
  const controller=new ApiTripController({storage:persisted,fetchImpl:async()=>Response.json(snapshot())});
  await controller.restore();assert.equal(controller.state.favoriteIntent,null);assert.equal(controller.state.favoriteRestoreUnconfirmed,true);
  await controller.sessionSnapshot();assert.equal(controller.state.favoriteRestoreUnconfirmed,true,'An ordinary GET cannot acknowledge lost favorite conditions');
  controller.reset();assert.equal(controller.state.favoriteIntent,null);assert.equal(controller.state.favoriteRestoreUnconfirmed,false);
  assert.equal(persisted.read().favoriteIntent,null);assert.equal(persisted.read().favoriteRestoreUnconfirmed,false);
 }
});

test('Reset and restart remove the previous favorite intent while retaining paid order history',()=>{
 for(const action of ['reset','restartSession']){
  const controller=new ApiTripController({fetchImpl:()=>assert.fail('No HTTP is necessary')});
  Object.assign(controller.state,currentModel(),{favoriteIntent:favoriteIntent(),favoriteRestoreUnconfirmed:true,orders:[{sessionId:'paid-session',order:{order_id:'DEMO-paid'}}]});
  controller[action]();assert.equal(controller.state.favoriteIntent,null);assert.equal(controller.state.favoriteRestoreUnconfirmed,false);
  assert.equal(controller.state.orders[0].order.order_id,'DEMO-paid');
 }
});

test('An interrupted search-only request keeps its exact booking restriction, message and idempotency ID after reload and retry',async()=>{
 for(const searchOnly of [false,true]){
  const saved=storage(),calls=[];
  const first=new ApiTripController({storage:saved,fetchImpl:async(path,init)=>{
   assert.equal(path,'/api/chat');calls.push(JSON.parse(init.body));return eventResponse([['delta',{text:'Часть ответа.'}]]);
  }});Object.assign(first.state,currentModel());
  await assert.rejects(first.send('Почему подходит этот отель?',{searchOnly}),error=>error.code==='incomplete');
  const pending=saved.read().pendingRequest;assert.equal(pending.searchOnly,searchOnly);
  assert.equal(calls[0].search_only,searchOnly?true:undefined);
  const restored=new ApiTripController({storage:saved,fetchImpl:async(path,init)=>{
   if(path==='/api/chat'){calls.push(JSON.parse(init.body));return eventResponse([['delta',{text:'Полный ответ.'}],['done',snapshot({version:1,brief,last_diff:null})]]);}
   assert.equal(path,'/api/session/'+sid);return Response.json(snapshot({version:1,brief,last_diff:null}));
  }});
  await restored.restore();assert.equal(restored.state.pendingRequest.searchOnly,searchOnly);
  restored.state.favoriteRestoreUnconfirmed=true;
  await restored.retry({searchOnly:!searchOnly});
  assert.equal(calls.length,2);assert.equal(calls[1].retry,true);
  assert.equal(calls[1].request_id,calls[0].request_id);assert.equal(calls[1].message,calls[0].message);
  assert.equal(calls[1].search_only,calls[0].search_only,'Retry cannot change the original ledger request mode');
  assert.equal(restored.state.pendingRequest,null);assert.equal(restored.state.favoriteRestoreUnconfirmed,true);
 }
});

test('Only an explicit boolean true can add the search-only transport flag',async()=>{
 for(const searchOnly of [true,false,undefined,null,1,'true',{},[]]){
  let body;await streamChat({message:'Справочный вопрос',searchOnly,fetchImpl:async(_,init)=>{
   body=JSON.parse(init.body);return eventResponse([['done',{session_id:sid,version:0,stage:'collecting'}]]);
  }});
  assert.equal(body.search_only,searchOnly===true?true:undefined);
 }
});
