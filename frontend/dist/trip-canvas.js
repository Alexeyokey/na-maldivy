/** Projection of orchestrator Session.snapshot()/SSE brief. Never extracts state from prose. */
export const CANVAS_FIELDS={
 origin:'Откуда',destination:'Куда едем',start_date:'Дата вылета',nights:'Ночей',month:'Месяц',
 adults:'Взрослые',children_ages:'Возраст детей',pets:'Питомцы',budget_rub:'Бюджет на всю поездку',
 max_flight_minutes:'Максимум в полёте',direct_only:'Прямой рейс',refundable_only:'Бесплатная отмена',
 min_stars:'Звёзды отеля',min_water_temperature_c:'Температура воды от',visa_free_only:'Без визы',needs:'Особые пожелания',mood:'Настроение'
};
export const CANVAS_PLACES={MOW:'Москва',SVO:'Москва (Шереметьево)',LED:'Санкт-Петербург',AYT:'Анталья',DXB:'Дубай',MLE:'Мальдивы'};
const sources=new Set(['explicit','inferred','default']);
const limits={month:[1,12],nights:[1,60],adults:[1,12],pets:[0,3],budget_rub:[1,Number.MAX_SAFE_INTEGER],max_flight_minutes:[1,1440],min_stars:[1,5],min_water_temperature_c:[10,35]};
const booleans=new Set(['direct_only','refundable_only','visa_free_only']);
const record=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
export function emptyCanvas(){return {session_id:null,version:null,stage:null,brief:null,sources:{},needs:[]};}
function validField(field,value){
 if(value===null)return true;
 if(limits[field])return Number.isFinite(value)&&(field==='min_water_temperature_c'||Number.isSafeInteger(value))&&value>=limits[field][0]&&value<=limits[field][1];
 if(booleans.has(field))return typeof value==='boolean';
 if(field==='children_ages')return Array.isArray(value)&&value.length<=8&&value.every(age=>Number.isInteger(age)&&age>=0&&age<=17);
 if(field==='needs')return Array.isArray(value)&&value.length<=100&&value.every(v=>typeof v==='string'&&v.length<=200);
 if(field==='start_date')return typeof value==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(value)&&new Date(value+'T12:00:00Z').toISOString().slice(0,10)===value;
 return typeof value==='string'&&value.length<=200;
}
export function applyCanvasEvent(canvas,type,payload){
 if(!record(payload))throw new TypeError('Агент вернул некорректный холст поездки.');
 if(type==='brief'){
  if(!record(payload.brief))throw new TypeError('Агент вернул некорректный бриф поездки.');
  const brief={};
  for(const [key,value]of Object.entries(payload.brief))if(Object.hasOwn(CANVAS_FIELDS,key)){
   if(!validField(key,value))throw new TypeError('Агент вернул некорректное поле: '+CANVAS_FIELDS[key]);
   brief[key]=Array.isArray(value)?[...value]:value;
  }
  const fieldSources={};
  for(const [key,value]of Object.entries(record(payload.sources)?payload.sources:{}))if(Object.hasOwn(brief,key)&&sources.has(value))fieldSources[key]=value;
  const needs=Array.isArray(payload.needs)?payload.needs.filter(n=>record(n)&&typeof n.code==='string'&&typeof n.label==='string'&&typeof n.effect==='string').map(n=>({code:n.code,label:n.label,effect:n.effect})):[];
  return {...canvas,brief,sources:fieldSources,needs};
 }
 if(type==='done'){
  if(payload.session_id!==undefined&&(typeof payload.session_id!=='string'||!payload.session_id||payload.session_id.length>64))throw new TypeError('Некорректная сессия холста.');
  if(payload.version!==undefined&&(!Number.isSafeInteger(payload.version)||payload.version<0))throw new TypeError('Некорректная версия холста.');
  if(canvas.version!==null&&payload.version!==undefined&&payload.session_id===canvas.session_id&&payload.version<canvas.version)throw new TypeError('Получена устаревшая версия холста.');
  return {...canvas,session_id:payload.session_id??canvas.session_id,version:payload.version??canvas.version,stage:typeof payload.stage==='string'?payload.stage:canvas.stage};
 }
 return canvas;
}
export function restoreCanvas(saved){
 try{
  if(!record(saved))return emptyCanvas();
  let result=applyCanvasEvent(emptyCanvas(),'done',saved);
  if(saved.brief)result=applyCanvasEvent(result,'brief',saved);
  return result;
 }catch{return emptyCanvas();}
}
export function canvasFields(canvas){
 const brief=canvas.brief||{};
 return Object.keys(CANVAS_FIELDS).filter(key=>{
  const value=brief[key];
  if(value===undefined||value===null)return false;
  if(key==='month'&&brief.start_date)return false;
  if(canvas.sources[key])return true;
  if(Array.isArray(value))return value.length>0;
  if(booleans.has(key))return value;
  if(key==='adults')return value!==1;
  if(key==='pets')return value>0;
  return value!=='';
 });
}
export function canvasEditMessage(field,input){
 let value=input;
 if(limits[field])value=Number(input);
 if(!Object.hasOwn(CANVAS_FIELDS,field)||!validField(field,value)||value===null)throw new TypeError('Проверьте значение поля «'+(CANVAS_FIELDS[field]||field)+'».');
 const place=CANVAS_PLACES[value]||value;
 switch(field){
  case 'budget_rub':return 'Бюджет '+value+' рублей на всю поездку за всех.';
  case 'origin':return 'Город вылета: из '+place+'.';
  case 'destination':return 'Хочу поехать в '+place+'.';
  case 'start_date':return 'Дата вылета: '+new Date(value+'T12:00:00').toLocaleDateString('ru-RU',{day:'numeric',month:'long',year:'numeric'})+'.';
  case 'nights':return 'На '+value+' ночей.';
  case 'adults':return value+' взрослых.';
  default:throw new TypeError('Это пожелание можно уточнить в чате.');
 }
}
