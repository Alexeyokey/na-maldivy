import test from 'node:test';
import assert from 'node:assert/strict';
import {departurePeriod,durationLabel,temperatureLabel,humanizeTravelText,normalizeSearchSummary,flexiblePackageOverview,tripAssumptions,activityReferenceView} from '../dist/trip-presentation.js';
import {criterionValue,renderTripChanges} from '../dist/trip-usability.js';
import {createFavoritesStore} from '../dist/favorites.js';
import {ApiTripController,applyAgentEvent,createApiModel} from '../dist/api-model.js';

const window={departure_window_start:'2026-10-30',departure_window_end:'2027-04-29',departure_months:[]};
const brief={transport:'flight',origin:'MOW',destination:null,start_date:'2027-01-15',nights:7,adults:2,children_ages:[],pets:0,budget_rub:200000,needs:['active','sea','surfing','diving','budget_low'],...window};
const summary={kind:'flexible_dates',window_start:window.departure_window_start,window_end:window.departure_window_end,checked_dates:72,checked_destinations:6,complete_quotes:50,within_budget:30,scope:'sampled_dates'};
const option=(index,destination,date,total)=>({index,kind:'package',destination,can_confirm:true,known_total_rub:total,travelers:{adults:2,children_ages:[],pets:0},hotel:{hotel_id:`demo:hotel/${destination}-01`,name:'Демо · Морской отель',nights:7,check_in:date,check_out:'2027-02-22'},outbound:{from:'SVO',to:destination,departure_at:date+'T14:00:00+03:00'},inbound:{departure_at:'2027-02-22T12:00:00+05:30'}});
const options=[option(1,'CMB','2026-12-15',190000),option(2,'CXR','2027-02-15',170000)];

test('Flexible periods and travel figures use readable units without changing factual qualifications',()=>{
 assert.equal(departurePeriod(brief),'30.10.2026 — 29.04.2027');
 assert.equal(departurePeriod({departure_months:['2026-11','2026-12','2027-01','2027-02']}),'Ноябрь 2026, Декабрь 2026, Январь 2027, Февраль 2027');
 assert.equal(departurePeriod({departure_window_start:'2026-02-30',departure_window_end:'2026-12-01'}),null);
 assert.equal(criterionValue('month',1),'Январь');assert.equal(criterionValue('month',0),'Не уточнено');
 assert.equal(criterionValue('departure_months',['2026-11','2027-02']),'Ноябрь 2026, Февраль 2027');
 assert.equal(durationLabel(855),'14 ч 15 мин');assert.equal(durationLabel(60),'1 ч');assert.equal(durationLabel(null),'время не уточнено');
 assert.equal(temperatureLabel(27.75),'27,8 °C');
 const text=humanizeTravelText('Вода +27.75 °C; время перелёта 575 минут — географическая оценка, маршрут не проверен.');
 assert.equal(text,'Вода 27,8 °C; время перелёта 9 ч 35 мин — географическая оценка, маршрут не проверен.');
});

test('An unpriced destination change is a collapsed criteria update with no invented financial comparison',()=>{
 const html=renderTripChanges({version:2,lastDiff:{schema_version:1,version:2,status:'updated',criteria_changes:[{field:'month',before:null,after:1,source:'explicit'}],before:{kind:'destination',name:'Предыдущий подбор',known_total_rub:null},after:{kind:'destination',name:'Шри-Ланка',known_total_rub:null},comparison:{delta_known_total_rub:null,same_basis:false},notes:[]}});
 assert.match(html,/^<details[^>]*api-trip-changes-compact/);assert.match(html,/Условия поиска обновлены/);assert.match(html,/Январь/);
 assert.doesNotMatch(html,/Не оценено|api-change-totals|Разницу стоимости|Что изменилось|<strong>1<\/strong>/);
});

test('A flexible-date overview is based on the shown server options and explicitly limits the search claim',()=>{
 const state={brief,optionKind:'packages',options,searchSummary:summary,selected:null,stale:false};
 const text=flexiblePackageOverview(state);
 assert.match(text,/2 варианта · 2 взрослых/);assert.match(text,/30\.10\.2026 — 29\.04\.2027/);assert.match(text,/Среди показанных — от 170[\s\u00a0\u202f]?000 ₽/);
 assert.match(text,/сочетаний направления и даты: 72/);assert.match(text,/отдельные даты, а не каждый день/);
 assert.match(text,/Демо-расчёт/);assert.match(text,/обратный трансфер, визы и другие доплаты не оценены/);
 assert.doesNotMatch(text,/самая дешёвая|наиболее выгодная|все даты|цена покупки|включено всё/);
 assert.equal(flexiblePackageOverview({...state,selected:1}),null);
 assert.deepEqual(tripAssumptions({...state,sources:{start_date:'default'},assumptions:[]}).filter(row=>row.field==='start_date'),[],'A date on one card cannot become the assumed date of the whole search window');
});

test('Search metadata is a narrow idempotent public projection and fails closed for unsupported or malformed scopes',()=>{
 const value={...summary,session_id:'private-session',authorization:'private-token',orders:[{full_name:'PRIVATE NAME'}]};
 assert.deepEqual(normalizeSearchSummary(value),summary);assert.deepEqual(normalizeSearchSummary(normalizeSearchSummary(value)),summary);
 for(const patch of [{scope:'all_dates'},{checked_dates:-1},{within_budget:'30'},{window_end:'2026-10-01'},{window_start:null},{complete_quotes:Infinity}])assert.equal(normalizeSearchSummary({...value,...patch}),null);
});

test('Flexible options save their own direction and departure instead of the general brief date, and activity requests survive restore',()=>{
 const favorites=createFavoritesStore({storage:null,now:()=>new Date('2026-10-29T12:00:00Z')});
 for(const card of options){
  const result=favorites.add({brief,option:card});assert.equal(result.saved,true);
  const saved=result.items.find(row=>row.id===result.id);assert.equal(saved.criteria.destination,card.destination);assert.equal(saved.criteria.start_date,card.outbound.departure_at.slice(0,10));
  assert.equal(saved.criteria.nights,card.hotel.nights);assert.equal(saved.criteria.adults,2);assert.equal(saved.criteria.budget_rub,200000);
  assert.deepEqual(saved.criteria.needs,[...brief.needs].sort());
  const restored=favorites.restore(result.id);assert.match(restored.message,/активный отдых/);assert.match(restored.message,/сёрфинг/);assert.match(restored.message,/дайвинг/);assert.match(restored.message,/нужно подтвердить/);
  assert.doesNotMatch(restored.message,/190000|170000|Подтверждаю|Беру вариант/);
 }
 assert.equal(favorites.load().items.length,2);
 for(const changed of [{...options[0],outbound:{...options[0].outbound,departure_at:'2027-07-15T14:00:00+03:00'}},options[1]]){
  const criteria=changed===options[1]?{...brief,destination:'CMB'}:brief;
  assert.equal(favorites.add({brief:criteria,option:changed}).saved,false,'A stale option outside the current window or explicit direction cannot be saved');
 }
});

test('Window changes invalidate old results and metadata persists identically through events, snapshots, and browser restore',async()=>{
 let stored;const storage={setItem:(_,value)=>stored=value,getItem:()=>stored??null};
 const controller=new ApiTripController({storage,fetchImpl:async()=>Response.json({session_id:'window-session',version:1,stage:'packages_shown',brief,sources:{},needs:[],selected:null,booking:null,stale:false,options:{kind:'packages',items:options,search_summary:summary,notes:[],assumptions:[]}})});
 Object.assign(controller.state,{sessionId:'window-session',version:1,brief,stage:'packages_shown'});
 applyAgentEvent(controller.state,'options',{kind:'packages',items:options,search_summary:{...summary,access_token:'PRIVATE TOKEN'},notes:[],assumptions:[]});controller.remember();
 assert.deepEqual(JSON.parse(stored).searchSummary,summary);assert.doesNotMatch(stored,/PRIVATE TOKEN|access_token/);
 const restored=new ApiTripController({storage,fetchImpl:controller.fetch});await restored.restore();assert.deepEqual(restored.state.searchSummary,summary);
 applyAgentEvent(restored.state,'brief',{brief:{...brief,departure_window_end:'2027-02-28'},version:2});
 assert.equal(restored.state.options.length,0);assert.equal(restored.state.searchSummary,null);assert.equal(restored.state.stale,true);
 const fresh=createApiModel();assert.equal(fresh.searchSummary,null);
});


test('Activity references are scoped to a destination and cannot imply included or confirmed hotel services',()=>{
 const reference={requested:['active','surfing'],destination_activities:['surfing','diving','unknown'],sources:[{title:'Tourism source',url:'https://example.test/surf',checked_on:'2026-10-29'}, {title:'Unsafe',url:'javascript:alert(1)'}, {title:'Credentials',url:'https://user:pass@example.test/'}],scope:'destination_only',hotel_availability_confirmed:false,included_in_price:false};
 assert.deepEqual(activityReferenceView(reference),{labels:['сёрфинг','дайвинг'],sources:[{title:'Tourism source',url:'https://example.test/surf',checked_on:'2026-10-29'}]});
 for(const patch of [{scope:'hotel'},{hotel_availability_confirmed:true},{included_in_price:true},{sources:[]},{destination_activities:['unknown']}])assert.equal(activityReferenceView({...reference,...patch}),null);
});

test('Only a response that emits a fresh flexible search receives a compact search presentation flag',async()=>{
 let turn=0;
 const snapshot={session_id:'flag-session',version:1,stage:'packages_shown',brief,sources:{},needs:[],selected:null,booking:null,stale:false,options:{kind:'packages',items:options,search_summary:summary,notes:[],assumptions:[]}};
 const controller=new ApiTripController({fetchImpl:async path=>{
  if(path==='/api/chat'){
   const events=turn++===0?[['options',snapshot.options],['delta',{text:'Конкретные варианты.'}],['done',snapshot]]:[['delta',{text:'Ответ на вопрос об отзывах.'}],['done',snapshot]];
   return new Response(events.map(([name,value])=>'event: '+name+'\ndata: '+JSON.stringify(value)+'\n\n').join(''),{headers:{'Content-Type':'text/event-stream'}});
  }
  return Response.json(snapshot);
 }});
 await controller.send('Активный отдых на полгода вперёд.');assert.equal(controller.state.messages.at(-1).flexibleSearchResult,true);
 await controller.send('Расскажи о рисках варианта 2.');assert.equal(controller.state.messages.at(-1).flexibleSearchResult,undefined);assert.equal(controller.state.messages.at(-1).text,'Ответ на вопрос об отзывах.');assert.equal(controller.state.messages[1].flexibleSearchResult,true);
});
