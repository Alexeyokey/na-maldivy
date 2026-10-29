/** Public search bookmarks only. Historical amounts never authorize a price or booking. */
import {isoDay,earliestSearchDate,formatDate,adultsLabel,nightsLabel,counted} from './trip-presentation.js';

export const FAVORITES_STORAGE_KEY='na-maldivy-favorites-v1';
export const FAVORITES_LIMIT=20;
const SCHEMA_VERSION=1,MAX_STORAGE_BYTES=65536;
const cities={MOW:'Москвы',SVO:'Шереметьево',LED:'Петербурга'};
const destinations={AYT:'Анталью',DXB:'Дубай',MLE:'Мале'};
const needText={child:'Нужен семейный отель.',pets:'Нужны отель и трансфер с питомцами.',sea:'Нужны море и пляж.',warm:'Нужен тёплый климат.',budget_low:'Предпочитаю недорогие варианты.',short_flight:'Предпочитаю короткий перелёт.',romantic:'Нужна романтическая поездка.',late_arrival:'Нужна круглосуточная стойка регистрации.',short_transfer:'Предпочитаю короткий трансфер.',quiet:'Нужен спокойный отдых.',flexible:'Планы могут поменяться, нужен возвратный тариф.'};
const plain=value=>!!value&&typeof value==='object'&&!Array.isArray(value)&&[Object.prototype,null].includes(Object.getPrototypeOf(value));
const integer=(value,min,max)=>Number.isSafeInteger(value)&&value>=min&&value<=max;
const amount=value=>typeof value==='number'&&Number.isFinite(value)&&value>=0&&value<=Number.MAX_SAFE_INTEGER?value:null;
const clone=value=>JSON.parse(JSON.stringify(value));
const invalid=()=>{throw new TypeError('Invalid public favorite');};
const exactDay=value=>typeof value==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(value)&&isoDay(value)===value?value:null;
const departureDay=value=>typeof value==='string'&&/^(?:\d{4}-\d{2}-\d{2})(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2}))?$/.test(value)&&Number.isFinite(Date.parse(value))?isoDay(value):null;
function hotel(value){
 if(!plain(value))invalid();
 const match=typeof value.hotel_id==='string'&&/^osm:(node|way|relation)[/:]([1-9][0-9]{0,19})$/.exec(value.hotel_id),name=value.name;
 if(!match||typeof name!=='string'||name!==name.trim()||name.length<1||name.length>160||!/[\p{L}]/u.test(name)||/[<>\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/u.test(name)||/(?:https?:|www\.|@|(?:CART|DEMO)-|session[_-]?id|request[_-]?id|order[_-]?id|cart[_-]?id|\b[0-9a-f]{32}\b)/i.test(name)||/(?:^|[^\p{L}])(?:ignore|instructions?|system|assistant|pay|book|confirm|оплати\p{L}*|оформи\p{L}*|забронируй|подтверждаю|выбери|беру|игнорируй|инструкци\p{L}*)(?=$|[^\p{L}])/iu.test(name))invalid();
 return {hotel_id:`osm:${match[1]}/${match[2]}`,name};
}
function criteria(value){
 if(!plain(value)||value.transport!==undefined&&value.transport!=='flight'||!Object.hasOwn(cities,value.origin)||!Object.hasOwn(destinations,value.destination)||!exactDay(value.start_date)||!integer(value.nights,1,60)||!integer(value.adults,1,12)||!Array.isArray(value.children_ages)||value.children_ages.length>8||value.children_ages.some(age=>!integer(age,0,17))||!integer(value.pets,0,3))invalid();
 const result={transport:'flight',origin:value.origin,destination:value.destination,start_date:value.start_date,nights:value.nights,adults:value.adults,children_ages:[...value.children_ages].sort((a,b)=>a-b),pets:value.pets};
 for(const [field,min,max] of [['budget_rub',1,Number.MAX_SAFE_INTEGER],['max_flight_minutes',1,1440],['min_stars',1,5]]){
  const number=value[field];if(number!=null&&!integer(number,min,max))invalid();result[field]=number??null;
 }
 const water=value.min_water_temperature_c;if(water!=null&&(typeof water!=='number'||!Number.isFinite(water)||water<10||water>35))invalid();result.min_water_temperature_c=water??null;
 for(const field of ['direct_only','late_departure','refundable_only','visa_free_only']){if(value[field]!=null&&typeof value[field]!=='boolean')invalid();result[field]=value[field]??false;}
 if(value.needs!=null&&(!Array.isArray(value.needs)||value.needs.length>30||value.needs.some(need=>typeof need!=='string'||!Object.hasOwn(needText,need))))invalid();
 result.needs=[...new Set(value.needs||[])].filter(need=>!(need==='child'&&!result.children_ages.length)&&!(need==='pets'&&!result.pets)).sort();
 return result;
}
const identity=value=>JSON.stringify([value.hotel.hotel_id,value.criteria]);
function identifier(value){
 // A stable public bookmark label, never an access token. No capabilities are retained.
 let hash=14695981039346656037n;
 for(const byte of new TextEncoder().encode(identity(value))){hash^=BigInt(byte);hash=BigInt.asUintN(64,hash*1099511628211n);}
 return 'fav-'+hash.toString(16).padStart(16,'0');
}
function savedItem(value){
 if(!plain(value)||value.priceKind!=='historical_known_components'||typeof value.savedAt!=='string'||!/^\d{4}-\d{2}-\d{2}T/.test(value.savedAt)||!isoDay(value.savedAt)||!Number.isFinite(Date.parse(value.savedAt)))invalid();
 const result={hotel:hotel(value.hotel),criteria:criteria(value.criteria),rememberedKnownTotalRub:amount(value.rememberedKnownTotalRub),priceKind:'historical_known_components',savedAt:new Date(value.savedAt).toISOString()};
 return {id:identifier(result),...result};
}

/** Persist only the same public projection when a restored search spans reloads. */
export function normalizeFavoriteIntent(value){
 try{return clone({...savedItem(value),verified:value.verified===true});}catch{return null;}
}
function optionItem(input,when){
 if(!plain(input)||!plain(input.brief)||!plain(input.option)||!when)invalid();
 const {brief,option}=input,group=plain(option.travelers)?option.travelers:{};
 if(brief.transport!=null&&brief.transport!=='flight'||option.kind!=null&&option.kind!=='package')invalid();
 const start=brief.start_date==null?departureDay(option.outbound?.departure_at):exactDay(brief.start_date);
 const values={transport:'flight',origin:brief.origin??option.outbound?.from,destination:brief.destination??option.destination,start_date:start,nights:brief.nights??option.hotel?.nights,adults:brief.adults??group.adults,children_ages:brief.children_ages??group.children_ages,pets:brief.pets??group.pets};
 for(const field of ['budget_rub','max_flight_minutes','direct_only','late_departure','refundable_only','min_stars','min_water_temperature_c','visa_free_only','needs'])values[field]=brief[field];
 const result={hotel:hotel(option.hotel),criteria:criteria(values),rememberedKnownTotalRub:amount(option.known_total_rub),priceKind:'historical_known_components',savedAt:when};
 if(option.destination!=null&&option.destination!==result.criteria.destination||option.hotel?.nights!=null&&option.hotel.nights!==result.criteria.nights||departureDay(option.outbound?.departure_at)&&departureDay(option.outbound.departure_at)!==start)invalid();
 for(const field of ['adults','pets'])if(group[field]!=null&&group[field]!==result.criteria[field])invalid();
 if(group.children_ages!=null&&(!Array.isArray(group.children_ages)||JSON.stringify([...group.children_ages].sort((a,b)=>a-b))!==JSON.stringify(result.criteria.children_ages)))invalid();
 return {id:identifier(result),...result};
}
function searchMessage(value){
 const c=value.criteria,parts=[`Новый поиск: из ${cities[c.origin]} в ${destinations[c.destination]}, дата вылета ${formatDate(c.start_date)}, ${nightsLabel(c.nights)}, ${adultsLabel(c.adults)}.`];
 parts.push(c.children_ages.length?'Дети: '+c.children_ages.map(age=>counted(age,'год','года','лет')).join(', ')+'.':'Без детей.');
 parts.push(`Питомцев: ${c.pets}.`+(c.pets===0?' Без питомцев.':''));
 parts.push(c.budget_rub===null?'Без ограничения бюджета.':`Бюджет ${c.budget_rub} рублей на всю поездку за всех.`);
 if(c.max_flight_minutes!==null)parts.push(c.max_flight_minutes%60===0?`Перелёт: максимум ${counted(c.max_flight_minutes/60,'час','часа','часов')}.`:`Максимальная длительность перелёта ${c.max_flight_minutes} минут.`);
 if(c.direct_only)parts.push('Только прямые рейсы.');
 if(c.late_departure)parts.push('Нужен вечерний вылет.');
 if(c.refundable_only)parts.push('Только возвратные тарифы отеля.');
 if(c.min_stars!==null)parts.push(`Отель не менее ${c.min_stars} звёзд.`);
 if(c.min_water_temperature_c!==null)parts.push(`Температура воды не ниже ${c.min_water_temperature_c} градусов.`);
 if(c.visa_free_only)parts.push('Только направления без визы.');
 parts.push(...c.needs.map(need=>needText[need]));
 // Neither hotel names/IDs, old totals nor an old result's numeric index enter the prompt.
 parts.push('Заново подобрать и пересчитать варианты. Ничего не выбирать, не оформлять и не оплачивать.');
 return parts.join(' ');
}

export function createFavoritesStore({storage,now=()=>new Date()}={}){
 let items=[],loaded=false,persisted=true,loadCode=null,loadNotice='';
 if(storage===undefined){try{storage=globalThis.localStorage;}catch{storage=null;}}
 if(!storage||typeof storage.getItem!=='function'||typeof storage.setItem!=='function')storage=null;
 const clock=()=>{try{const date=new Date(now());return Number.isFinite(date.getTime())?date.toISOString():null;}catch{return null;}};
 const output=(code=null,notice='',extra={})=>({items:clone(items),code,notice,persisted,...extra});
 const unavailable='Браузер не дал доступ к избранному. Изменения сохранятся только до перезагрузки страницы.';
 function load(){
  if(loaded)return output(loadCode,loadNotice);loaded=true;
  if(!storage){persisted=false;loadCode='storage_unavailable';loadNotice=unavailable;return output(loadCode,loadNotice);}
  let raw;
  try{raw=storage.getItem(FAVORITES_STORAGE_KEY);}catch{persisted=false;loadCode='storage_unavailable';loadNotice=unavailable;return output(loadCode,loadNotice);}
  if(raw===null)return output();
  try{
   if(typeof raw!=='string'||raw.length>MAX_STORAGE_BYTES)invalid();
   const saved=JSON.parse(raw);
   if(!plain(saved)||saved.schemaVersion!==SCHEMA_VERSION||!Array.isArray(saved.items)){
    loadCode=plain(saved)&&saved.schemaVersion!==SCHEMA_VERSION?'unsupported_version':'corrupt';invalid();
   }
   let rejected=0;const identities=new Set();
   for(const candidate of saved.items){
    try{const item=savedItem(candidate),key=identity(item);if(identities.has(key)){rejected++;continue;}if(items.length>=FAVORITES_LIMIT){rejected++;continue;}identities.add(key);items.push(item);}catch{rejected++;}
   }
   if(rejected){loadCode='partial_corrupt';loadNotice='Часть записей избранного повреждена или превышает лимит 20. Доступные варианты сохранены.';}
  }catch{items=[];loadCode||='corrupt';loadNotice=loadCode==='unsupported_version'?'Формат избранного не поддерживается. Сохраните нужные варианты заново.':'Не удалось прочитать избранное. Сохраните нужные варианты заново.';}
  return output(loadCode,loadNotice);
 }
 function save(code,notice,extra={}){
  if(!storage){persisted=false;return output('storage_unavailable',unavailable,extra);}
  try{storage.setItem(FAVORITES_STORAGE_KEY,JSON.stringify({schemaVersion:SCHEMA_VERSION,items}));persisted=true;loadCode=null;loadNotice='';return output(code,notice,extra);}
  catch{persisted=false;loadCode='storage_write_failed';loadNotice='Не удалось записать избранное: возможно, хранилище браузера заполнено. Изменения действуют только до перезагрузки страницы.';return output(loadCode,loadNotice,extra);}
 }
 function find(input){load();try{const candidate=optionItem(input,clock()),saved=items.find(item=>identity(item)===identity(candidate));return saved?clone(saved):null;}catch{return null;}}
 return {
  load,
  find,
  add(input){
   load();let item;try{item=optionItem(input,clock());}catch{return output('invalid','Не удалось сохранить вариант: проверьте маршрут, даты и состав группы.',{saved:false,duplicate:false,id:null});}
   const index=items.findIndex(value=>identity(value)===identity(item));
   if(index>=0){items[index]=item;return save('already_saved','Вариант уже в избранном. Прежняя сумма обновлена как историческая справка.',{saved:true,duplicate:true,id:item.id});}
   if(items.length>=FAVORITES_LIMIT)return output('limit','Можно сохранить до 20 вариантов. Удалите один, чтобы добавить новый.',{saved:false,duplicate:false,id:null});
   items.unshift(item);return save('saved','Сохранено в избранное. Цена относится к прежнему расчёту и требует проверки.',{saved:true,duplicate:false,id:item.id});
  },
  remove(id){load();const index=items.findIndex(item=>item.id===id);if(index<0)return output('not_found','Такого варианта в избранном уже нет.',{removed:false});items.splice(index,1);return save('removed','Вариант удалён из избранного.',{removed:true});},
  clear(){load();items=[];return save('cleared','Избранное очищено.');},
  isSaved(input){return find(input)!==null;},
  restore(id,{startDate}={}){
   load();const saved=items.find(item=>item.id===id);
   const fail=(code,notice,requiresDate=false)=>({message:'',requiresDate,freshSearch:true,favorite:saved?clone(saved):null,code,notice});
   if(!saved)return fail('not_found','Такого варианта в избранном уже нет.');
   const reference=clock();if(!reference)return fail('clock_unavailable','Не удалось проверить текущую дату. Проверьте часы устройства.');
   const start=startDate===undefined?saved.criteria.start_date:exactDay(startDate);
   if(!start||start<earliestSearchDate(reference))return fail(startDate===undefined?'date_required':'invalid_date','Выберите новую дату вылета не ранее '+formatDate(earliestSearchDate(reference))+'. Прежняя сумма не применяется.',true);
   const favorite=clone(saved);favorite.criteria.start_date=start;if(start!==saved.criteria.start_date)favorite.rememberedKnownTotalRub=null;
   // This is a one-off new search; the stored historical quote and identity stay unchanged.
   return {message:searchMessage(favorite),requiresDate:false,freshSearch:true,favorite,code:'ready',notice:'Выполним новый расчёт. Прежняя цена и наличие того же отеля не гарантируются.'};
  },
 };
}
