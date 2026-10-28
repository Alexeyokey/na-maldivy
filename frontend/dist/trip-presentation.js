/** Pure display helpers. Values and prices always come from the server. */
export function counted(value,one,few,many){
 if(!Number.isInteger(value)||value<0)return 'не уточнено';
 const last=value%10,pair=value%100;
 return `${value} ${last===1&&pair!==11?one:last>=2&&last<=4&&(pair<12||pair>14)?few:many}`;
}
export const adultsLabel=value=>counted(value,'взрослый','взрослых','взрослых');
export const nightsLabel=value=>counted(value,'ночь','ночи','ночей');
export const optionsLabel=value=>counted(value,'вариант','варианта','вариантов');

/** Prefer the bundled quote scenario while future, then keep quick prompts usable. */
export function demoDepartureDate(now=new Date()){
 const date=new Date(now);
 if(!Number.isFinite(date.getTime()))throw new TypeError('A valid reference date is required');
 if(date.toISOString().slice(0,10)<'2026-11-18')return '2026-11-18';
 date.setUTCDate(date.getUTCDate()+28);
 return date.toISOString().slice(0,10);
}
export function earliestSearchDate(now=new Date()){
 const date=new Date(now);
 if(!Number.isFinite(date.getTime()))throw new TypeError('A valid reference date is required');
 date.setUTCDate(date.getUTCDate()+1);
 return date.toISOString().slice(0,10);
}

export function isoDay(value){
 if(typeof value!=='string')return null;
 const part=value.slice(0,10);
 if(!/^\d{4}-\d{2}-\d{2}$/.test(part))return null;
 const parsed=new Date(part+'T12:00:00Z');
 return Number.isFinite(parsed.getTime())&&parsed.toISOString().slice(0,10)===part?part:null;
}

/** Match the checkout's completed-years rule without inventing a future birth. */
export function demoTravelerBirthDate(age,departure,now=new Date()){
 const trip=isoDay(departure),reference=new Date(now),today=Number.isFinite(reference.getTime())?reference.toISOString().slice(0,10):null;
 const invalid={value:'',min:'',max:today||'',note:'Проверьте дату вылета и возраст путешественника перед оформлением.'};
 if(!trip||!today||!Number.isInteger(age)||age<0||age>130)return invalid;
 const yearsBefore=years=>{
  const year=Number(trip.slice(0,4))-years;if(year<1)return null;
  const candidate=String(year).padStart(4,'0')+trip.slice(4);
  return isoDay(candidate)||(trip.slice(5)==='02-29'?String(year).padStart(4,'0')+'-02-28':null);
 };
 const nextDay=day=>{if(!day)return null;const value=new Date(day+'T12:00:00Z');value.setUTCDate(value.getUTCDate()+1);return isoDay(value.toISOString());};
 // For children the exact age is priced; adults only need to be 18–130.
 const child=age<18,min=nextDay(yearsBefore(child?age+1:131)),upper=yearsBefore(child?age:18);
 if(!min||!upper)return invalid;
 const max=upper<today?upper:today;
 if(min>max)return {value:'',min:'',max:today,note:child?'Уже родившийся ребёнок не может иметь указанный возраст на эту дату вылета. Измените возраст в чате или дату поездки.':'Для выбранной даты нет допустимой даты рождения взрослого. Измените дату поездки.'};
 const preferred=yearsBefore(age)||max,value=preferred<min?min:preferred>max?max:preferred;
 return {value,min,max,note:''};
}
export function formatDate(value){
 const date=isoDay(value);return date?`${date.slice(8,10)}.${date.slice(5,7)}.${date.slice(0,4)}`:'дата не уточнена';
}
export function formatRating(value){return Number.isFinite(value)&&value>=0&&value<=10?value.toLocaleString('ru-RU',{minimumFractionDigits:1,maximumFractionDigits:1}):'нет оценки';}
export function refundLabel(value){return value===true?'Возвратный тариф':value===false?'Невозвратный тариф':'Условия возврата не уточнены';}
export function transferLabel(transfer){
 if(!transfer)return 'Трансфер не оценён';
 const modes={taxi:'Такси',minivan:'Минивэн',shuttle:'Шаттл',bus:'Автобус',metro:'Метро',boat:'Катер',seaplane:'Гидросамолёт',flight_speedboat:'Самолёт и катер'};
 const mode=modes[transfer.mode]||'Трансфер';
 const duration=Number.isInteger(transfer.duration_minutes)&&transfer.duration_minutes>0?` · ${transfer.duration_minutes} мин`:'';
 return `${mode}${duration}${transfer.available===false?' · ожидание отправления':''}`;
}
export const isTrainTrip=state=>state.brief?.transport==='train'||state.optionKind==='trains';
export function trainPresentation(train){
 const classes={platzkart:'Плацкарт',coupe:'Купе',sv:'СВ',seated:'Сидячие места'};
 return {
  number:typeof train?.number==='string'&&train.number?train.number:'не указан',
  seatClass:classes[train?.seat_class]||'Класс не уточнён',
  originStation:typeof train?.origin?.name==='string'?train.origin.name:'Вокзал отправления не уточнён',
  destinationStation:typeof train?.destination?.name==='string'?train.destination.name:'Вокзал прибытия не уточнён',
  priceLabel:train?.is_synthetic===true?'Модельная демо-цена':'Источник цены не указан',
  limitation:'Источник расписания в карточке не указан. Наличие мест и доступность класса не подтверждены. Оформление железнодорожных билетов не подключено.',
 };
}

/** Missing values stay unknown; especially null baggage/transfer is never converted to zero. */
export function priceRows(option){
 return [
  {label:'Перелёт туда',value:option?.outbound?.price_rub},
  {label:'Багаж туда',value:option?.outbound?.baggage_price_rub},
  {label:'Перелёт обратно',value:option?.inbound?.price_rub},
  {label:'Багаж обратно',value:option?.inbound?.baggage_price_rub},
  {label:'Отель',value:option?.hotel?.price_rub},
  {label:'Трансфер в отель',value:option?.transfer?.price_rub},
 ];
}

/** Only current search data may explain assumptions; an explicit source always wins. */
export function tripAssumptions(state){
 const brief=state.brief||{},sources=state.sources||{},rows=Array.isArray(state.assumptions)?state.assumptions:[];
 const rail=isTrainTrip(state);
 const option=state.options?.find(row=>row.index===state.selected)||state.options?.[0];
 const details=field=>rows.find(row=>row.field===field)?.text||'';
 const values={adults:brief.adults??option?.travelers?.adults,nights:brief.nights??option?.hotel?.nights,start_date:isoDay(brief.start_date)||isoDay(rail?option?.departure_at:option?.outbound?.departure_at)};
 const labels={adults:'Взрослые',nights:'Продолжительность',start_date:rail?'Дата отправления':'Дата вылета'};
 const display={adults:adultsLabel,nights:nightsLabel,start_date:formatDate};
 return (rail?['adults','start_date']:['adults','nights','start_date']).flatMap(field=>{
  if(sources[field]==='explicit'||values[field]===undefined||values[field]===null)return [];
  const source=['default','inferred'].includes(sources[field])?sources[field]:details(field)?(field==='start_date'?'inferred':'default'):field==='adults'&&values.adults===1?'default':null;
  if(!source)return [];
  return [{field,label:labels[field],value:values[field],display:display[field](values[field]),source,explanation:details(field)}];
 });
}

/** A short warning index never assigns a risk to a hotel; the original keeps every qualification. */
export function narrativeView(input){
 const text=String(input??'');
 if(text.length<1100)return {text,compact:false,preview:'',warnings:[]};
 const intro=[];
 for(const line of text.trim().split(/\n\s*\n/,1)[0].split('\n')){
  if(/^\s*(?:\d+[.)]|[-+*•])\s+\S/.test(line))break;
  intro.push(line);
 }
 const first=intro.join('\n').trim()||'Подробный ответ с вариантами — ниже.';
 const preview=first.length>360?first.slice(0,360).replace(/\s+\S*$/,'')+'…':first;
 const warnings=[];
 if(/синтетич|демонстрацион|сгенерирован/i.test(text))warnings.push('В ответе отмечены демонстрационные или сгенерированные данные.');
 if(/не подтвержд[её]н/i.test(text))warnings.push('Есть сведения, которые не подтверждены поставщиком.');
 if(/не оцен[её]н|не включ[её]н|не вход(?:ит|ят)/i.test(text))warnings.push('Есть неоценённые или не включённые расходы: показанная сумма может не охватывать всю поездку.');
 if(/невозврат|без возврата|не возвращ|удержани/i.test(text))warnings.push('Упомянуты ограничения возврата; проверьте условия конкретного варианта.');
 if(/компромисс|ограничени|бронировать заранее|только дн[её]м|предзаказ/i.test(text))warnings.push('Упомянуты компромиссы или специальные ограничения; подробности по вариантам — в полном ответе.');
 if(!warnings.length&&/риск|требует исправления/i.test(text))warnings.push('В полном ответе есть дополнительные риски и условия, которые нужно проверить перед выбором.');
 return {text,compact:true,preview,warnings};
}
