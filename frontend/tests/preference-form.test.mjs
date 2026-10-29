import test from 'node:test';
import assert from 'node:assert/strict';
import {renderPreferenceForm,revealPreferenceErrors} from '../dist/preference-form.js';
import {preferenceRequest} from '../dist/trip-presentation.js';

const now='2026-10-29T23:59:59Z';
const state=()=>({
 stage:'options_presented',stale:false,sessionLost:false,options:[],sources:{},assumptions:[],
 brief:{transport:'flight',adults:2,nights:7,start_date:'2026-11-18',children_ages:[],pets:0,budget_rub:null},
});
const decode=value=>value.replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');
const inputs=html=>Object.fromEntries([...html.matchAll(/<input\b[^>]*>/g)].map(([tag])=>[
 tag.match(/\bname="([^"]+)"/)[1],
 Object.fromEntries([...tag.matchAll(/([\w-]+)="([^"]*)"/g)].map(([,key,value])=>[key,decode(value)])),
]));
const values=html=>Object.fromEntries(Object.entries(inputs(html)).map(([name,input])=>[name,input.value]));

test('Common trip edits stay outside a closed native disclosure, whose values remain successful controls',()=>{
 const original=state(),before=structuredClone(original),html=renderPreferenceForm(original,{now});
 const extra=html.match(/<details\b([^>]*)>([\s\S]*?)<\/details>/);
 assert.ok(extra);assert.doesNotMatch(extra[1],/\bopen\b/);
 assert.match(extra[2],/<summary><span>Дети и питомцы<\/span>/);
 assert.deepEqual(Object.keys(inputs(extra[2])),['children_ages','pets']);
 assert.doesNotMatch(extra[2],/\bdisabled\b|\bhidden\b/,'Collapsing a disclosure cannot remove its values from submission');
 const main=html.replace(extra[0],'');
 assert.deepEqual(Object.keys(inputs(main)),['adults','start_date','nights','budget_rub']);
 assert.deepEqual(values(html),{adults:'2',start_date:'2026-11-18',nights:'7',budget_rub:'',children_ages:'',pets:'0'});
 assert.match(html,/<form id="api-preferences-form"[^>]*\bnovalidate\b[^>]*data-transport="flight"/);
 assert.match(html,/Если раздел свёрнут, его значения сохраняются/);
 assert.deepEqual(original,before,'Rendering cannot change the current search');
});

test('An existing family opens the extra section and submits twins, pets and the current budget unchanged',()=>{
 const current=state();Object.assign(current.brief,{children_ages:[0,5,5],pets:2,budget_rub:200000});
 const html=renderPreferenceForm(current,{now}),result=preferenceRequest(values(html),{now});
 assert.match(html,/<details\b[^>]*\bopen>/);assert.match(html,/3 ребёнка · 2 питомца/);
 assert.equal(values(html).children_ages,'0, 5, 5');assert.deepEqual(result.errors,{});
 assert.match(result.message,/2 взрослых, 7 ночей/);
 assert.match(result.message,/Дети: 0 лет, 5 лет, 5 лет\. Питомцев: 2\./);
 assert.match(result.message,/Бюджет 200000 рублей на всю поездку за всех/);
 assert.doesNotMatch(result.message,/5 взрослых|оформить|оплатить/);
});

test('Explicit empty child and budget values and zero pets retain removal semantics despite old option data',()=>{
 const current=state();current.options=[{travelers:{adults:6,children_ages:[8],pets:3}}];
 const html=renderPreferenceForm(current,{now}),result=preferenceRequest(values(html),{now});
 assert.doesNotMatch(html.match(/<details\b[^>]*>/)[0],/\bopen\b/);
 assert.equal(values(html).adults,'2');assert.equal(values(html).children_ages,'');assert.equal(values(html).pets,'0');
 assert.match(result.message,/Без детей\. Питомцев: 0\. Без питомцев\. Сними бюджет\./);
 assert.deepEqual(result.errors,{});
});

test('A train search keeps passenger extras and a one-way budget without hotel nights or flight wording',()=>{
 const current=state();current.brief.transport='train';current.brief.children_ages=[8];
 const html=renderPreferenceForm(current,{now}),fields=inputs(html),result=preferenceRequest(values(html),{now,train:true});
 assert.equal(fields.nights,undefined);assert.match(html,/data-transport="train"/);
 assert.match(html,/Дата отправления/);assert.match(html,/Бюджет на билеты за всех в одну сторону/);
 assert.doesNotMatch(html,/Ночи|Дата вылета|на всю поездку/);
 assert.match(result.message,/дата отправления 18\.11\.2026/);
 assert.match(result.message,/Дети: 8 лет/);assert.doesNotMatch(result.message,/ноч|вылета|отел/);
});

test('Current assumptions prefill visible basics, while stale results cannot invent a date or stay',()=>{
 const current=state();Object.assign(current.brief,{adults:null,nights:null,start_date:null});
 current.sources={adults:'default',nights:'default',start_date:'inferred'};
 current.options=[{travelers:{adults:1},hotel:{nights:10},outbound:{departure_at:'2027-01-03T12:00:00Z'}}];
 const ready=inputs(renderPreferenceForm(current,{now}));
 assert.equal(ready.adults.value,'1');assert.equal(ready.nights.value,'10');assert.equal(ready.start_date.value,'2027-01-03');
 assert.equal(ready.start_date.min,'2026-10-30');
 const stale=inputs(renderPreferenceForm({...current,stale:true},{now}));
 assert.equal(stale.adults.value,'');assert.equal(stale.nights.value,'');assert.equal(stale.start_date.value,'');
});

test('Child or pet errors open their disclosure and are linked to the field without interpreting HTML',()=>{
 const message='Укажите возраст <18> и не вставляйте "HTML" <img src=x>';
 for(const name of ['children_ages','pets']){
  const html=renderPreferenceForm(state(),{now,errors:{[name]:message}}),fields=inputs(html);
  assert.match(html,/<details\b[^>]*\bopen>/);
  assert.equal(fields[name]['aria-invalid'],'true');
  assert.equal(fields[name]['aria-describedby'],`api-preferences-${name}-help api-preferences-${name}-error`);
  assert.match(html,/&lt;img src=x&gt;/);assert.doesNotMatch(html,/<img\b/);
  assert.equal(fields.adults['aria-invalid'],undefined);
 }
 const current=state();current.brief.budget_rub='" onfocus="alert(1)';
 const html=renderPreferenceForm(current,{now});assert.equal(inputs(html).budget_rub.value,current.brief.budget_rub);
 assert.doesNotMatch(html,/ onfocus="/);
});

function formHarness(){
 const details={open:false},events=[],fields={},messages={};
 for(const name of ['adults','start_date','nights','budget_rub','children_ages','pets']){
  const attributes=new Map([['aria-invalid','true']]);
  fields[name]={value:name==='children_ages'?'5, 5':'unchanged',attributes,
   setAttribute:(key,value)=>attributes.set(key,value),removeAttribute:key=>attributes.delete(key),
   closest:selector=>selector==='details'&&['children_ages','pets'].includes(name)?details:null,
   focus(){events.push({field:name,disclosureOpen:details.open,value:this.value});},
  };
  messages[name]={textContent:'Old error'};
 }
 const form={querySelector:selector=>{
  const field=selector.match(/^\[name="([^"]+)"\]$/)?.[1];
  return field?fields[field]:messages[selector.match(/^#api-preferences-(.+)-error$/)?.[1]]||null;
 }};
 return {form,details,events,fields,messages};
}

test('A submission error reveals children before focus and preserves in-progress entries',()=>{
 const ui=formHarness(),before=Object.fromEntries(Object.entries(ui.fields).map(([name,input])=>[name,input.value]));
 const error='Укажите возраст ребёнка от 0 до 17.';
 assert.equal(revealPreferenceErrors(ui.form,{children_ages:error}),error);
 assert.deepEqual(ui.events,[{field:'children_ages',disclosureOpen:true,value:'5, 5'}]);
 assert.equal(ui.messages.children_ages.textContent,error);assert.equal(ui.messages.adults.textContent,'');
 assert.equal(ui.fields.children_ages.attributes.get('aria-invalid'),'true');
 assert.equal(ui.fields.adults.attributes.has('aria-invalid'),false);
 assert.deepEqual(Object.fromEntries(Object.entries(ui.fields).map(([name,input])=>[name,input.value])),before);
 assert.equal(revealPreferenceErrors(ui.form,{}),null);
 assert.equal(ui.details.open,true,'Correcting an error cannot collapse fields the user is editing');
 assert.equal(ui.events.length,1,'A valid submission cannot unexpectedly move focus');
 assert.equal(ui.messages.children_ages.textContent,'');
});

test('Several errors focus the first visible field while also revealing invalid extras',()=>{
 const ui=formHarness();
 assert.equal(revealPreferenceErrors(ui.form,{pets:'Не более трёх питомцев.',budget_rub:'Проверьте бюджет.'}),'Проверьте бюджет.');
 assert.deepEqual(ui.events,[{field:'budget_rub',disclosureOpen:true,value:'unchanged'}]);
 assert.equal(ui.messages.pets.textContent,'Не более трёх питомцев.');
});
