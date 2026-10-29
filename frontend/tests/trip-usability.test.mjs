import test from 'node:test';
import assert from 'node:assert/strict';
import {componentChanges,criterionValue,favoriteMismatches,normalizeTripDiff,refinementActions,renderTripChanges} from '../dist/trip-usability.js';

const comparison=()=>({
 schema_version:1,version:2,status:'updated',
 criteria_changes:[{field:'direct_only',before:false,after:true,source:'explicit'}],
 before:{kind:'package',name:'Отель A',known_total_rub:100000,start_date:'2026-11-18',components:[{key:'outbound',known_cost_rub:25000,source:{kind:'synthetic'}},{key:'outbound_baggage',known_cost_rub:null,source:{kind:'modelled'}}]},
 after:{kind:'package',name:'Отель B',known_total_rub:90000,start_date:'2026-11-18',components:[{key:'outbound',known_cost_rub:20000,source:{kind:'provider_quote_with_modelled_extras'}},{key:'outbound_baggage',known_cost_rub:0,source:{kind:'modelled'}}]},
 comparison:{delta_known_total_rub:-10000,same_basis:false,differences:['hotel','travelers','components'],savings_guaranteed:false},notes:[],
});
const view=diff=>renderTripChanges({version:2,lastDiff:diff,busy:false,stale:false,sessionLost:false});

test('Refinement actions are reversible search edits and appear only for current package results',()=>{
 const state={optionKind:'packages',options:[{}],brief:{needs:['budget_low'],direct_only:true,refundable_only:false}};
 const before=structuredClone(state),actions=refinementActions(state);
 assert.deepEqual(actions.map(row=>[row.key,row.active]),[['cheaper',true],['direct',true],['refundable',false]]);
 for(const action of actions){assert.match(action.on,/без оформления/);assert.match(action.off,/без оформления/);assert.notEqual(action.on,action.off);}
 assert.match(actions[1].off,/Можно с пересадками/);assert.match(actions[2].off,/Можно и невозвратные/);
 for(const patch of [{optionKind:'trains'},{optionKind:'destinations'},{options:[]},{stale:true},{sessionLost:true}])assert.deepEqual(refinementActions({...state,...patch}),[]);
 assert.deepEqual(state,before);
});

test('A diff is an idempotent public projection and cannot retain nested access capabilities',()=>{
 const raw=comparison();Object.assign(raw,{session_id:'private-session-capability',cart_id:'CART-private',order_id:'DEMO-private',voucher_path:'/private/voucher'});
 Object.assign(raw.before,{session_id:'private-session-capability',travelers:[{full_name:'PRIVATE NAME',birth_date:'1990-01-01'}],package:{token:'private-token'}});
 Object.assign(raw.after.components[0].source,{token:'private-token',request_headers:{authorization:'private-token'}});
 raw.criteria_changes.push({field:'session_id',before:'private-session-capability',after:'another-private-capability'});
 raw.comparison.savings_guaranteed=true;raw.comparison.access_token='private-token';
 const before=structuredClone(raw),normalized=normalizeTripDiff(raw,2),serialized=JSON.stringify(normalized);
 assert.ok(normalized);assert.deepEqual(normalizeTripDiff(normalized,2),normalized);
 assert.doesNotMatch(serialized,/private-session-capability|CART-private|DEMO-private|private-token|PRIVATE NAME|birth_date|voucher_path|request_headers/);
 assert.deepEqual(normalized.criteria_changes.map(row=>row.field),['direct_only']);
 assert.equal(normalized.comparison.savings_guaranteed,false);assert.deepEqual(raw,before);
});

test('Unknown totals and components stay unknown while an explicitly free component stays zero',()=>{
 const raw=comparison();raw.before.known_total_rub=null;raw.comparison.delta_known_total_rub=-90000;
 const diff=normalizeTripDiff(raw,2),rows=componentChanges(diff),html=view(diff);
 assert.equal(diff.before.known_total_rub,null);assert.equal(diff.comparison.delta_known_total_rub,null);
 assert.deepEqual(rows.find(row=>row.key==='outbound_baggage'),{key:'outbound_baggage',label:'Багаж туда',before:null,after:0,sourceChanged:false});
 assert.match(html,/Разницу стоимости пока нельзя оценить/);
 assert.match(html,/<td>Не оценено<\/td><td>0 ₽<\/td>/);
 assert.doesNotMatch(html,/−90[\s\u00a0\u202f]?000 ₽/);
 const bothZero=comparison();bothZero.before.known_total_rub=0;bothZero.after.known_total_rub=0;bothZero.comparison.delta_known_total_rub=0;
 assert.match(view(bothZero),/Известная сумма не изменилась/);
});

test('A signed cost change is verified arithmetic and never presented as guaranteed savings',()=>{
 const cheaper=comparison(),down=view(cheaper);
 assert.match(down,/Изменение известной суммы: −10[\s\u00a0\u202f]?000 ₽/);
 assert.match(down,/Разница сумм не означает экономию на той же поездке/);
 assert.match(down,/Было · Отель A/);assert.match(down,/Стало · Отель B/);
 assert.doesNotMatch(down,/Вы сэкономили|Экономия:|гарантированная экономия/i);
 const dearer=comparison();dearer.after.known_total_rub=110000;dearer.comparison.delta_known_total_rub=10000;
 assert.match(view(dearer),/Изменение известной суммы: \+10[\s\u00a0\u202f]?000 ₽/);
 const falseClaim=comparison();falseClaim.comparison.delta_known_total_rub=-50000;
 assert.equal(normalizeTripDiff(falseClaim,2).comparison.delta_known_total_rub,null);
 assert.match(view(falseClaim),/Разницу стоимости пока нельзя оценить/);
});

test('Hotel, traveller and component differences remain qualified and source changes are explicitly marked',()=>{
 const diff=normalizeTripDiff(comparison(),2),rows=componentChanges(diff),html=view(diff);
 assert.deepEqual(diff.comparison.differences,['hotel','travelers','components']);
 assert.equal(rows.find(row=>row.key==='outbound').sourceChanged,true);
 assert.match(html,/Условия или состав вариантов различаются/);
 assert.match(html,/Источник данных изменился/);
 assert.match(html,/известные расходы за всю группу/);assert.match(html,/Прочие доплаты и наличие услуг не подтверждены/);
 const missing=comparison();missing.after.components=[];
 assert.equal(componentChanges(normalizeTripDiff(missing,2))[0].after,null,'A removed priced component is unknown on the new side, never free');
});

test('No-results and awaiting-input updates explain why there is no replacement price',()=>{
 for(const [status,message] of [['no_results','Подходящих вариантов по новым условиям пока нет.'],['awaiting_input','Нужно уточнить условия, чтобы завершить пересчёт.']]){
  const diff=comparison();diff.status=status;diff.after=null;diff.comparison.delta_known_total_rub=null;
  const html=view(diff);assert.ok(html.includes(message));assert.match(html,/нового варианта пока нет/);
  assert.match(html,/Разницу стоимости пока нельзя оценить/);assert.match(html,/<strong>Не оценено<\/strong>/);
 }
});

test('Diffs from an old revision or a hidden recovery state cannot appear in the current search',()=>{
 const diff=comparison();
 for(const patch of [{version:3},{busy:true},{stale:true},{sessionLost:true}])assert.equal(renderTripChanges({version:2,lastDiff:diff,...patch}),'');
 for(const patch of [{schema_version:2},{version:1},{status:'paid'},{criteria_changes:null}])assert.equal(normalizeTripDiff({...diff,...patch},2),null);
 for(const input of [null,false,3,'diff',[],{}])assert.equal(normalizeTripDiff(input,2),null);
});

test('Malformed diff rows are bounded, ignored or displayed as unknown and never execute markup',()=>{
 const diff=comparison();diff.criteria_changes=[null,[],{}, {field:'unknown',before:3,after:4},{field:'children_ages',before:{token:'secret'},after:[0,5,5,null,{}]},{field:'needs',before:false,after:['quiet','unknown']},{field:'budget_rub',before:Infinity,after:-10}];
 diff.before.components=[null,{},[],{key:'unknown',known_cost_rub:1},{key:'hotel',known_cost_rub:-1,source:null}];
 diff.after.name='<img src=x onerror=alert(1)>';diff.notes=[null,{},'<script>alert(1)</script>'];
 const normalized=normalizeTripDiff(diff,2),html=view(diff);
 assert.deepEqual(normalized.criteria_changes[0].after,[0,5,5]);assert.equal(normalized.before.components[0].known_cost_rub,null);
 assert.match(html,/&lt;img src=x/);assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<img\b|<script\b/);
 assert.equal(normalized.notes.length,1);
 assert.doesNotThrow(()=>renderTripChanges({version:2,lastDiff:{...diff,comparison:null}}));
});

test('Criterion labels preserve removal, infant and false semantics',()=>{
 assert.equal(criterionValue('budget_rub',null),'Без ограничения');assert.equal(criterionValue('children_ages',[]),'Без детей');
 assert.equal(criterionValue('children_ages',[0,5,5]),'0 лет, 5 лет, 5 лет');
 assert.equal(criterionValue('direct_only',false),'Нет');assert.equal(criterionValue('pets',0),'0');
 assert.equal(criterionValue('origin','SVO'),'Москва · Шереметьево');assert.equal(criterionValue('start_date','invalid'),'дата не уточнена');
});

test('Favorite checks accept a Moscow-airport refinement and reordered ages but preserve exact airports, child multiplicity and removals',()=>{
 const expected={origin:'MOW',children_ages:[5,0,5],needs:['quiet'],budget_rub:null,pets:0,direct_only:false};
 assert.deepEqual(favoriteMismatches(expected,{...expected,origin:'SVO',children_ages:[5,5,0],needs:['quiet','sea']}),[]);
 assert.deepEqual(favoriteMismatches({origin:'SVO'},{origin:'MOW'}),[{field:'origin',label:'Отправление',expected:'Москва · Шереметьево',actual:'Москва'}]);
 assert.equal(favoriteMismatches({origin:'SVO'},{origin:'LED'})[0].field,'origin');
 const changed=favoriteMismatches(expected,{...expected,children_ages:[0,5],budget_rub:200000,pets:1,direct_only:true,needs:[]});
 assert.deepEqual(changed.map(row=>row.field),['children_ages','pets','budget_rub','direct_only','needs']);
 assert.equal(changed.find(row=>row.field==='budget_rub').expected,'Без ограничения');
 assert.equal(changed.find(row=>row.field==='direct_only').expected,'Нет');
 assert.equal(changed.find(row=>row.field==='children_ages').expected,'5 лет, 0 лет, 5 лет');
});

test('Malformed saved favorite arrays and restored needs cannot crash the search UI',()=>{
 for(const bad of [3,'invalid',{},true]){
  for(const field of ['children_ages','needs']){
   let result;assert.doesNotThrow(()=>{result=favoriteMismatches({[field]:bad},{[field]:[]});});
   assert.ok(result.some(row=>row.field===field),'An unreadable saved condition cannot be silently considered satisfied');
   assert.doesNotThrow(()=>favoriteMismatches({[field]:[]},{[field]:bad}));
  }
  assert.doesNotThrow(()=>refinementActions({optionKind:'packages',options:[{}],brief:{needs:bad}}));
 }
});
