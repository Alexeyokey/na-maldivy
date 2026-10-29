/** Pure display helpers. Values and prices always come from the server. */
export function counted(value,one,few,many){
 if(!Number.isInteger(value)||value<0)return 'не уточнено';
 const last=value%10,pair=value%100;
 return `${value} ${last===1&&pair!==11?one:last>=2&&last<=4&&(pair<12||pair>14)?few:many}`;
}
export const adultsLabel=value=>counted(value,'взрослый','взрослых','взрослых');
export const nightsLabel=value=>counted(value,'ночь','ночи','ночей');
export const optionsLabel=value=>counted(value,'вариант','варианта','вариантов');
export const monthNames=['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'];
export function durationLabel(value){
 if(!Number.isInteger(value)||value<0)return 'время не уточнено';
 const hours=Math.floor(value/60),minutes=value%60;
 return [hours?`${hours} ч`:'',minutes||!hours?`${minutes} мин`:''].filter(Boolean).join(' ');
}
export function temperatureLabel(value){return Number.isFinite(value)?value.toLocaleString('ru-RU',{maximumFractionDigits:1})+' °C':'температура не уточнена';}
export function humanizeTravelText(input){
 return String(input??'').replace(/([+-]?\d+[.,]\d{2,})\s*°C/g,(_,number)=>temperatureLabel(Number(number.replace(',','.')))).replace(/(время перел[её]та\s+)(\d+)\s+минут/gi,(_,label,minutes)=>label+durationLabel(Number(minutes)));
}
export function departurePeriod(brief={}){
 const start=isoDay(brief.departure_window_start),end=isoDay(brief.departure_window_end);
 if(start&&end&&start<=end)return `${formatDate(start)} — ${formatDate(end)}`;
 const months=Array.isArray(brief.departure_months)?brief.departure_months.filter(value=>typeof value==='string'&&/^\d{4}-(?:0[1-9]|1[0-2])$/.test(value)):[];
 if(months.length)return months.map(value=>`${monthNames[Number(value.slice(5))-1]} ${value.slice(0,4)}`).join(', ');
 return null;
}
export function normalizeSearchSummary(value){
 if(!value||typeof value!=='object'||Array.isArray(value)||value.kind!=='flexible_dates'||value.scope!=='sampled_dates')return null;
 const start=isoDay(value.window_start),end=isoDay(value.window_end);
 if(!start||!end||start>end)return null;
 const fields=['checked_dates','checked_destinations','complete_quotes','within_budget'];
 if(fields.some(field=>!Number.isSafeInteger(value[field])||value[field]<0||value[field]>10000000))return null;
 return {kind:'flexible_dates',window_start:start,window_end:end,...Object.fromEntries(fields.map(field=>[field,value[field]])),scope:'sampled_dates'};
}
/** The search covers sampled dates, never a claim of the cheapest date in the entire window. */
export function flexiblePackageOverview(state){
 if(state.optionKind!=='packages'||!state.options?.length||state.stale||state.sessionLost||state.selected!=null)return null;
 const summary=normalizeSearchSummary(state.searchSummary),period=departurePeriod(state.brief);
 if(!summary&&!period)return null;
 const prices=state.options.map(option=>option.known_total_rub).filter(value=>Number.isSafeInteger(value)&&value>=0);
 const group=state.options[0]?.travelers||state.brief||{},minimum=prices.length?Math.min(...prices).toLocaleString('ru-RU')+' ₽':null;
 const lines=[`${optionsLabel(state.options.length)}${group.adults?' · '+adultsLabel(group.adults):''}${period?' · вылеты '+period:''}.`];
 if(minimum)lines.push(`Среди показанных — от ${minimum} за известные компоненты всей группы.`);
 if(summary)lines.push(`Рассчитано сочетаний направления и даты: ${summary.checked_dates}. Проверены отдельные даты, а не каждый день периода.`);
 lines.push('Демо-расчёт. Наличие услуг не подтверждено; обратный трансфер, визы и другие доплаты не оценены.');
 return lines.join(' ');
}

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
 const duration=Number.isInteger(transfer.duration_minutes)&&transfer.duration_minutes>0?` · ${durationLabel(transfer.duration_minutes)}`:'';
 return `${mode}${duration}${transfer.available===false?' · ожидание отправления':''}`;
}
export const isTrainTrip=state=>state.brief?.transport==='train'||state.optionKind==='trains';
export function trainPresentation(train){
 const classes={platzkart:'Плацкарт',coupe:'Купе',sv:'СВ',seated:'Сидячие места'};
 const source=train?.schedule_source,mode=train?.schedule_cache_mode;
 const recorded=train?.schedule_recorded_at,recordedValid=typeof recorded==='string'&&isoDay(recorded)&&/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(recorded)&&Number.isFinite(Date.parse(recorded));
 const scheduleLabel=source==='synthetic'?'Демонстрационное расписание':source==='yandex'&&mode==='replay'?'Сохранённое расписание Яндекс Расписаний':source==='yandex'&&['live','record'].includes(mode)?'Ответ API Яндекс Расписаний':source==='yandex'?'Яндекс Расписания · режим источника не указан':'Источник расписания не указан';
 return {
  number:typeof train?.number==='string'&&train.number?train.number:'не указан',
  seatClass:classes[train?.seat_class]||'Класс не уточнён',
  originStation:typeof train?.origin?.name==='string'?train.origin.name:'Вокзал отправления не уточнён',
  destinationStation:typeof train?.destination?.name==='string'?train.destination.name:'Вокзал прибытия не уточнён',
  priceLabel:train?.is_synthetic===true?'Модельная демо-цена':'Источник цены не указан',
  scheduleLabel,
  scheduleDetail:source==='yandex'&&mode==='replay'?(recordedValid?'Снимок: '+formatDate(recorded):'Дата снимка не указана'):'',
  limitation:(['synthetic','yandex'].includes(source)?'':'Источник расписания в карточке не указан. ')+(source==='yandex'&&mode==='replay'?'Актуальность расписания не подтверждена. ':'')+'Наличие мест и доступность класса не подтверждены. Оформление железнодорожных билетов не подключено.',
 };
}

/** Explicit form values become one validated chat edit, never a booking instruction. */
export function preferenceRequest(values,{train=false,now=new Date()}={}){
 const errors={},raw=field=>String(values[field]??'').trim();
 const integer=(field,min,max,message)=>{const text=raw(field),value=Number(text);if(!/^\d+$/.test(text)||!Number.isSafeInteger(value)||value<min||value>max){errors[field]=message;return null;}return value;};
 const adults=integer('adults',1,12,'Укажите число взрослых от 1 до 12.'),nights=train?null:integer('nights',1,60,'Укажите число ночей от 1 до 60.');
 const date=raw('start_date'),minimum=earliestSearchDate(now);
 if(date&&(!/^\d{4}-\d{2}-\d{2}$/.test(date)||!isoDay(date)))errors.start_date='Укажите существующую дату в календаре.';
 else if(date&&date<minimum)errors.start_date='Выберите дату не ранее '+formatDate(minimum)+'.';
 const ageText=raw('children_ages');let ages=[];
 if(ageText){
  if(!/^\d{1,2}(?:(?:\s*[,;]\s*|\s+)\d{1,2})*$/.test(ageText))errors.children_ages='Укажите возраст каждого ребёнка целым числом через запятую, например: 0, 5, 5.';
  else{ages=ageText.split(/[,;\s]+/).map(Number);if(ages.length>8||ages.some(age=>age<0||age>17))errors.children_ages='Допустимо до 8 детей с возрастом от 0 до 17 лет на дату отправления.';}
 }
 const pets=integer('pets',0,3,'Укажите количество питомцев от 0 до 3.');
 const budgetText=raw('budget_rub').replace(/[\s\u00a0\u202f]/g,''),budget=budgetText?Number(budgetText):null;
 if(budgetText&&(!/^\d+$/.test(budgetText)||!Number.isSafeInteger(budget)||budget<=0))errors.budget_rub='Укажите целую сумму в рублях больше нуля или оставьте поле пустым, чтобы снять лимит.';
 if(Object.keys(errors).length)return {message:'',errors};
 const criteria=`Изменить условия поиска: ${adultsLabel(adults)}${train?'':', '+nightsLabel(nights)}${date?', дата '+(train?'отправления':'вылета')+' '+formatDate(date):''}.`;
 const children=ages.length?'Дети: '+ages.map(age=>counted(age,'год','года','лет')).join(', ')+'.':'Без детей.';
 const animals=`Питомцев: ${pets}.`+(pets===0?' Без питомцев.':'');
 const money=budget===null?'Сними бюджет.':`Бюджет ${budget} рублей ${train?'на билеты за всех в одну сторону':'на всю поездку за всех'}.`;
 return {message:[criteria,children,animals,money,'Заново подобрать варианты, без оформления.'].join(' '),errors:{}};
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
  if(field==='start_date'&&departurePeriod(brief)&&state.selected==null)return [];
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

/** Destination-level references never imply a service at a particular demo hotel. */
export function activityReferenceView(value){
 if(!value||value.scope!=='destination_only'||value.hotel_availability_confirmed!==false||value.included_in_price!==false||!Array.isArray(value.destination_activities)||!Array.isArray(value.sources))return null;
 const names={surfing:'сёрфинг',diving:'дайвинг'},labels=[...new Set(value.destination_activities)].filter(key=>Object.hasOwn(names,key)).map(key=>names[key]);
 const sources=value.sources.slice(0,8).flatMap(row=>{
  if(!row||typeof row.url!=='string'||row.url.length>1200||typeof row.title!=='string'||!row.title.trim()||row.title.length>200)return [];
  try{const url=new URL(row.url);if(url.protocol!=='https:'||url.username||url.password)return [];return [{title:row.title,url:url.href,checked_on:isoDay(row.checked_on)}];}catch{return [];}
 });
 return labels.length&&sources.length?{labels,sources}:null;
}
