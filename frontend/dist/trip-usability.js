import {formatDate,counted,monthNames,durationLabel,temperatureLabel} from './trip-presentation.js';
import {destinationNames} from './destination-names.generated.js';

export const criterionLabels={transport:'Транспорт',origin:'Отправление',destination:'Направление',month:'Месяц',year:'Год',departure_window_start:'Вылеты с',departure_window_end:'Вылеты до',departure_months:'Месяцы вылета',start_date:'Дата отправления',nights:'Продолжительность',adults:'Взрослые',children_ages:'Дети',pets:'Питомцы',budget_rub:'Бюджет за всех',max_flight_minutes:'Максимальный перелёт',direct_only:'Без пересадок',late_departure:'Вечерний вылет',refundable_only:'Возвратный отель',min_stars:'Звёзды отеля',min_water_temperature_c:'Температура воды',visa_free_only:'Без визы',needs:'Пожелания'};
const needLabels={active:'активный отдых',surfing:'сёрфинг',diving:'дайвинг',child:'с детьми',pets:'с питомцем',sea:'море и пляж',warm:'тепло',budget_low:'сначала недорогие',short_flight:'короткий перелёт',romantic:'романтическая поездка',late_arrival:'поздний прилёт',short_transfer:'короткий трансфер',quiet:'спокойный отдых',flexible:'возвратные тарифы'};
const places={...destinationNames,MOW:'Москва',SVO:'Москва · Шереметьево'};
const own=(value,key)=>Object.prototype.hasOwnProperty.call(value,key);
const obj=value=>!!value&&typeof value==='object'&&!Array.isArray(value);
const text=value=>typeof value==='string'?value.slice(0,600):'';
const amount=value=>Number.isSafeInteger(value)&&value>=0?value:null;
const components={outbound:'Перелёт туда',outbound_baggage:'Багаж туда',inbound:'Перелёт обратно',inbound_baggage:'Багаж обратно',hotel:'Отель',arrival_transfer:'Трансфер в отель',train:'Поезд в одну сторону'};
const sourceKinds=new Set(['synthetic','modelled','provider_quote_with_modelled_extras','unknown']);

export function refinementActions(state){
 if(state.optionKind!=='packages'||!state.options?.length||state.stale||state.sessionLost)return [];
 const brief=state.brief||{};
 return [
  {key:'cheaper',label:'Искать дешевле',active:Array.isArray(brief.needs)&&brief.needs.includes('budget_low'),on:'Подбери более дешёвые варианты на тех же условиях, без оформления.',off:'Сними приоритет низкой цены, остальные условия сохраняем, без оформления.'},
  {key:'direct',label:'Без пересадок',active:brief.direct_only===true,on:'Только прямые рейсы, остальные условия сохраняем, без оформления.',off:'Можно с пересадками, остальные условия сохраняем, без оформления.'},
  {key:'refundable',label:'Возвратный отель',active:brief.refundable_only===true,on:'Только возвратный отель, остальные условия сохраняем, без оформления.',off:'Можно и невозвратные отели, остальные условия сохраняем, без оформления.'},
 ];
}

const safeValue=value=>value===null||typeof value==='boolean'||typeof value==='number'&&Number.isFinite(value)?value:typeof value==='string'?text(value):Array.isArray(value)?value.slice(0,24).filter(row=>typeof row==='string'||Number.isInteger(row)).map(row=>typeof row==='string'?text(row):row):null;
function summary(value){
 if(!obj(value)||!['package','train','destination'].includes(value.kind))return null;
 return {kind:value.kind,name:text(value.name)||text(value.hotel),known_total_rub:amount(value.known_total_rub),start_date:text(value.start_date),check_in:text(value.check_in),check_out:text(value.check_out),components:Array.isArray(value.components)?value.components.filter(row=>obj(row)&&own(components,row.key)).slice(0,8).map(row=>({key:row.key,label:components[row.key],known_cost_rub:amount(row.known_cost_rub),source_kind:sourceKinds.has(row.source?.kind??row.source_kind)?row.source?.kind??row.source_kind:'unknown'})):[]};
}

/** Public display projection only. No raw response objects or capabilities enter storage. */
export function normalizeTripDiff(value,version){
 if(!obj(value)||value.schema_version!==1||!Number.isInteger(value.version)||value.version!==version||!['updated','no_results','awaiting_input'].includes(value.status)||!Array.isArray(value.criteria_changes))return null;
 const before=summary(value.before),after=summary(value.after),comparison=obj(value.comparison)?value.comparison:{};
 const computed=before?.known_total_rub!==null&&after?.known_total_rub!==null&&before&&after?after.known_total_rub-before.known_total_rub:null;
 const delta=Number.isSafeInteger(comparison.delta_known_total_rub)&&comparison.delta_known_total_rub===computed?computed:null;
 return {schema_version:1,version,status:value.status,criteria_changes:value.criteria_changes.filter(row=>obj(row)&&own(criterionLabels,row.field)).slice(0,24).map(row=>({field:row.field,label:criterionLabels[row.field],before:safeValue(row.before),after:safeValue(row.after),source:['explicit','default','inferred'].includes(row.source)?row.source:null})),before,after,comparison:{delta_known_total_rub:delta,same_basis:comparison.same_basis===true,differences:Array.isArray(comparison.differences)?comparison.differences.filter(row=>['hotel','dates','travelers','route','transport','components'].includes(row)):[],savings_guaranteed:false,notice:text(comparison.notice)},notes:Array.isArray(value.notes)?value.notes.filter(row=>typeof row==='string').slice(0,8).map(text):[]};
}

export function criterionValue(field,value){
 if(value===null||value===undefined)return ['budget_rub','max_flight_minutes','min_stars','min_water_temperature_c'].includes(field)?'Без ограничения':'Не уточнено';
 if(['start_date','departure_window_start','departure_window_end'].includes(field))return formatDate(value);
 if(field==='month')return Number.isInteger(value)&&value>=1&&value<=12?monthNames[value-1]:'Не уточнено';
 if(field==='departure_months')return Array.isArray(value)?value.filter(row=>typeof row==='string'&&/^\d{4}-(?:0[1-9]|1[0-2])$/.test(row)).map(row=>monthNames[Number(row.slice(5))-1]+' '+row.slice(0,4)).join(', ')||'Не уточнено':'Не уточнено';
 if(['origin','destination'].includes(field))return own(places,value)?places[value]:String(value);
 if(field==='transport')return value==='train'?'Поезд':value==='flight'?'Самолёт':'Не уточнено';
 if(field==='children_ages')return !Array.isArray(value)?'Не уточнено':value.length?value.map(age=>counted(age,'год','года','лет')).join(', '):'Без детей';
 if(field==='needs')return !Array.isArray(value)?'Не уточнено':value.length?value.map(need=>needLabels[need]).filter(Boolean).join(', ')||'Другие пожелания':'Без дополнительных пожеланий';
 if(typeof value==='boolean')return value?'Да':'Нет';
 if(field==='budget_rub')return value.toLocaleString('ru-RU')+' ₽';
 if(field==='nights')return counted(value,'ночь','ночи','ночей');
 if(field==='max_flight_minutes')return durationLabel(value);
 if(field==='min_water_temperature_c')return temperatureLabel(value);
 return String(value);
}

export function componentChanges(diff){
 const before=new Map((diff?.before?.components||[]).map(row=>[row.key,row]));
 const after=new Map((diff?.after?.components||[]).map(row=>[row.key,row]));
 return Object.entries(components).filter(([key])=>before.has(key)||after.has(key)).map(([key,label])=>({key,label,before:before.get(key)?.known_cost_rub??null,after:after.get(key)?.known_cost_rub??null,sourceChanged:!!before.get(key)&&!!after.get(key)&&before.get(key).source_kind!==after.get(key).source_kind}));
}

const escape=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const rubles=value=>Number.isSafeInteger(value)&&value>=0?value.toLocaleString('ru-RU')+' ₽':'Не оценено';
export function renderTripChanges(state){
 const diff=normalizeTripDiff(state.lastDiff,state.version);
 if(!diff||state.busy||state.stale||state.sessionLost)return '';
 const delta=diff.comparison.delta_known_total_rub,rows=componentChanges(diff);
 const hasAmounts=diff.before?.known_total_rub!=null||diff.after?.known_total_rub!=null;
 if(!hasAmounts){
  if(!diff.criteria_changes.length&&diff.status==='updated')return '';
  const status=diff.status==='no_results'?'Подходящих поездок по новым условиям пока нет.':diff.status==='awaiting_input'?'Нужно уточнить условия поиска.':'';
  return `<details class="api-trip-changes api-trip-changes-compact" aria-label="Изменения условий поиска"><summary>Условия поиска обновлены</summary>${diff.criteria_changes.length?`<dl class="api-criteria-changes">${diff.criteria_changes.map(row=>`<div><dt>${escape(row.label)}</dt><dd>${escape(criterionValue(row.field,row.after))}${row.source&&row.source!=='explicit'?'<small>Предположение для поиска</small>':''}</dd></div>`).join('')}</dl>`:''}${status?`<p>${escape(status)}</p>`:''}${diff.notes.map(note=>`<p class="demo-data-note">${escape(note)}</p>`).join('')}</details>`;
 }

 const result=diff.status==='no_results'?'Подходящих вариантов по новым условиям пока нет.':diff.status==='awaiting_input'?'Нужно уточнить условия, чтобы завершить пересчёт.':'';
 const change=delta===null?'Разницу стоимости пока нельзя оценить.':delta===0?'Известная сумма не изменилась.':`Изменение известной суммы: ${delta>0?'+':'−'}${rubles(Math.abs(delta))}.`;
 return `<section class="api-trip-changes" aria-label="Последний пересчёт"><h2>Что изменилось</h2>${diff.criteria_changes.length?`<dl class="api-criteria-changes">${diff.criteria_changes.map(row=>`<div><dt>${escape(row.label)}</dt><dd><span>${escape(criterionValue(row.field,row.before))}</span><span aria-hidden="true"> → </span><strong>${escape(criterionValue(row.field,row.after))}</strong>${row.source&&row.source!=='explicit'?'<small>Предположение для поиска</small>':''}</dd></div>`).join('')}</dl>`:''}${result?`<p>${escape(result)}</p>`:''}<div class="api-change-totals"><div><span>Было · ${escape(diff.before?.name||'предыдущий подбор')}</span><strong>${rubles(diff.before?.known_total_rub)}</strong></div><div><span>Стало · ${escape(diff.after?.name||'нового варианта пока нет')}</span><strong>${rubles(diff.after?.known_total_rub)}</strong></div></div><p class="api-change-delta">${change}</p>${diff.comparison.notice?`<p>${escape(diff.comparison.notice)}</p>`:''}${!diff.comparison.same_basis?'<p class="api-change-caution">Условия или состав вариантов различаются. Разница сумм не означает экономию на той же поездке.</p>':''}${rows.length?`<details><summary>Изменения по компонентам</summary><div class="api-change-table" role="region" tabindex="0" aria-label="Стоимость компонентов до и после пересчёта"><table><thead><tr><th scope="col">Компонент</th><th scope="col">Было</th><th scope="col">Стало</th></tr></thead><tbody>${rows.map(row=>`<tr><th scope="row">${escape(row.label)}${row.sourceChanged?'<small>Источник данных изменился</small>':''}</th><td>${rubles(row.before)}</td><td>${rubles(row.after)}</td></tr>`).join('')}</tbody></table></div></details>`:''}<p class="demo-data-note">Сравниваем известные расходы за всю группу. Прочие доплаты и наличие услуг не подтверждены.</p>${diff.notes.map(note=>`<p class="demo-data-note">${escape(note)}</p>`).join('')}</section>`;
}

/** Restoring is a fresh search; any missed condition stays visible before choosing. */
export function favoriteMismatches(criteria,brief){
 if(!obj(criteria)||!obj(brief))return [];
 return Object.keys(criterionLabels).filter(field=>own(criteria,field)).filter(field=>{
  let expected=criteria[field],actual=brief[field];
  if(field==='origin')return expected==='MOW'?!['MOW','SVO','DME','VKO'].includes(actual):expected!==actual;
  if(field==='needs')return !Array.isArray(expected)||!Array.isArray(actual)||expected.some(need=>!actual.includes(need));
  if(field==='children_ages'){if(!Array.isArray(expected)||!Array.isArray(actual))return true;expected=[...expected].sort((a,b)=>a-b);actual=[...actual].sort((a,b)=>a-b);}
  return JSON.stringify(expected??null)!==JSON.stringify(actual??null);
 }).map(field=>({field,label:criterionLabels[field],expected:criterionValue(field,criteria[field]),actual:criterionValue(field,brief[field])}));
}
