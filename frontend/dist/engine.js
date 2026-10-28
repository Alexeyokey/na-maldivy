export const CITIES = [
  {id:'spb',name:'Санкт-Петербург',short:'Петербург',where:'Петербурге',aliases:['санкт-петербург','петербург','питер','спб'],factor:1,weather:'+6°',climate:'Пальто и зонт пригодятся',flight:'1 ч 30 мин',train:'4 ч 10 мин',distance:4.17,fare:3400,why:'Музеи, кофейни и долгие прогулки — для неспешных выходных.',accent:'#d6e4e7'},
  {id:'kazan',name:'Казань',short:'Казань',where:'Казани',aliases:['казан'],factor:.85,weather:'+4°',climate:'Прохладно, возможен дождь',flight:'1 ч 40 мин',train:'11 ч 30 мин',distance:11.5,fare:2800,why:'Гастрономия, старый город и новые впечатления без большого бюджета.',accent:'#e9decd'},
  {id:'sochi',name:'Сочи',short:'Сочи',where:'Сочи',aliases:['сочи','адлер'],factor:1.12,weather:'+16°',climate:'Мягкая осень у моря',flight:'3 ч 50 мин',train:'23 ч',distance:23,fare:6500,why:'Море, горный воздух и смена ритма — когда хочется выдохнуть.',accent:'#d2e5db'},
  {id:'kaliningrad',name:'Калининград',short:'Калининград',where:'Калининграде',aliases:['калининград'],factor:.93,weather:'+9°',climate:'Ветрено, захватите дождевик',flight:'2 ч 45 мин',train:null,distance:null,fare:4900,why:'Балтийский берег и старые улочки — для маленького приключения.',accent:'#e1e4d1'},
  {id:'moscow',name:'Москва',short:'Москва',where:'Москве',aliases:['москв'],factor:1.18,weather:'+5°',climate:'Осенняя погода',flight:'1 ч 30 мин',train:'4 ч 10 мин',distance:4.17,fare:3400,why:'Выставки, рестораны и насыщенный городской уикенд.',accent:'#e8d8d4'},
  {id:'yaroslavl',name:'Ярославль',short:'Ярославль',where:'Ярославле',aliases:['ярослав'],factor:.7,weather:'+4°',climate:'Тёплый шарф пригодится',flight:null,train:'3 ч 20 мин',distance:3.33,fare:1900,why:'Волга, тихие улицы и уютные отели — близко и доступно.',accent:'#e0decf'}
];
export const money = n => Math.round(n).toLocaleString('ru-RU') + ' ₽';
export const cityById = id => CITIES.find(c=>c.id===id) || CITIES[0];
export function iso(date){ return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`; }
export function addDays(value,n){ const d=new Date(value+'T12:00:00');d.setDate(d.getDate()+n);return iso(d); }
export function nights(s){ return Math.max(1,Math.round((new Date(s.checkOut+'T12:00:00')-new Date(s.checkIn+'T12:00:00'))/86400000)); }
export function dateLabel(date){return new Date(date+'T12:00:00').toLocaleDateString('ru-RU',{day:'numeric',month:'short'}).replace('.','');}
export function defaultState(now=new Date()){
  const start=new Date(now);const shift=(5-start.getDay()+7)%7;start.setDate(start.getDate()+(shift||7));
  return {city:'spb',origin:'moscow',budget:100000,totalBudget:null,adults:2,children:0,checkIn:iso(start),checkOut:addDays(iso(start),3),breakfast:false,cancel:false,center:false,pets:false,spa:false,quiet:false,seaside:false,transport:'train',transfer:true,earlyCheckIn:false,selectedHotel:null,sort:'recommended',mood:'выходные',searched:false,known:{}};
}
export function missingRequiredCriteria(s){
 const k=s.known||{};
 const validDate=value=>typeof value==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(value)&&iso(new Date(value+'T12:00:00'))===value;
 return [
  ['direction',k.city&&CITIES.some(c=>c.id===s.city)],
  ['budget',k.totalBudget&&Number.isFinite(s.totalBudget)&&s.totalBudget>0],
  ['dates',k.checkIn&&k.checkOut&&validDate(s.checkIn)&&validDate(s.checkOut)&&s.checkOut>s.checkIn],
  ['guests',k.adults&&Number.isInteger(s.adults)&&s.adults>=1&&s.adults<=6&&Number.isInteger(s.children)&&s.children>=0&&s.children<=4]
 ].filter(([,complete])=>!complete).map(([name])=>name);
}
export const recommendationsReady=s=>missingRequiredCriteria(s).length===0;
export function requiredCriteriaQuestion(s){
 switch(missingRequiredCriteria(s)[0]){
  case 'direction':return 'Куда хотите поехать? Назовите город или направление.';
  case 'budget':return 'Какой общий бюджет на всю поездку — отель, дорогу и трансфер?';
  case 'dates':return s.known?.checkIn&&!s.known?.checkOut?'Когда планируете вернуться или на сколько ночей едете?':'На какие даты планируете поездку? Укажите заезд и выезд.';
  case 'guests':return s.children?'Сколько взрослых едет вместе с детьми?':'Сколько человек едет? Укажите взрослых и детей, если они будут с вами.';
  default:return '';
 }
}
export function recommendHotels(s){return recommendationsReady(s)?searchHotels(s):[];}
const templates=[
  {id:'light',name:'Северный свет',style:'Бутик-отель',price:6400,rating:9.4,reviews:248,stars:4,center:true,pets:true,spa:false,quiet:true,seaside:false,image:'room-warm',distance:'0,8 км от центра',tag:'То самое сочетание',desc:'Тёплое дерево, большие окна и неспешные завтраки. Небольшой отель для тех, кому важны атмосфера и хороший сон.',risk:'Небольшие номера — от 18 м².',cancel:true},
  {id:'river',name:'Дом у воды',style:'Дизайн-отель',price:7800,rating:9.2,reviews:182,stars:4,center:true,pets:false,spa:true,quiet:true,seaside:true,image:'room-light',distance:'1,2 км от центра',tag:'Чуть больше удовольствия',desc:'Светлые комнаты, спокойные оттенки и спа-зона. Хороший выбор, чтобы никуда не торопиться.',risk:'Спа посещают по предварительной записи.',cancel:true},
  {id:'yard',name:'Тихий двор',style:'Камерный отель',price:5200,rating:8.9,reviews:316,stars:3,center:true,pets:true,spa:false,quiet:true,seaside:false,image:'room-design',distance:'1,6 км от центра',tag:'Бережно к бюджету',desc:'Уютная база для прогулок по городу. Всё необходимое и никаких лишних трат.',risk:'Нет лифта; номера на 2–3 этажах.',cancel:true},
  {id:'garden',name:'Зелёный сад',style:'Спа-отель',price:9200,rating:9.6,reviews:129,stars:5,center:false,pets:true,spa:true,quiet:true,seaside:true,image:'hotel-pool',distance:'4,2 км от центра',tag:'Полное переключение',desc:'Зелёная территория, бассейн и просторные номера. Для отдыха, которому не нужен плотный план.',risk:'До центра около 20 минут на такси.',cancel:true},
  {id:'loft',name:'Маленький лофт',style:'Апарт-отель',price:4200,rating:8.6,reviews:204,stars:3,center:false,pets:true,spa:false,quiet:false,seaside:false,image:'room-cozy',distance:'3,1 км от центра',tag:'Своя маленькая квартира',desc:'Номер с кухней и рабочим местом. Удобно для долгой поездки и самостоятельных завтраков.',risk:'Невозвратный тариф; оживлённая улица.',cancel:false},
  {id:'grand',name:'Гранд ателье',style:'Гранд-отель',price:11000,rating:9.7,reviews:421,stars:5,center:true,pets:false,spa:true,quiet:true,seaside:false,image:'room-warm',distance:'0,3 км от центра',tag:'Особенный случай',desc:'Высокие потолки, ресторан и сервис с вниманием к деталям. Для поездки, которую хочется запомнить.',risk:'Самый высокий тариф в подборке.',cancel:true}
];
export function allHotels(s){return templates.map((h,i)=>({...h,id:`${s.city}-${h.id}`,price:Math.round(h.price*cityById(s.city).factor/100)*100+(s.breakfast?450:0)+(s.pets?300:0)+(s.earlyCheckIn?Math.round(h.price*.35/nights(s)/100)*100:0),breakfast:s.breakfast,city:s.city,seaside:s.city==='sochi'&&h.seaside}));}
export function transportOptions(s){
 const city=cityById(s.city);const pax=s.adults+s.children;
 if(s.origin===s.city)return [{id:'none',title:'Уже на месте',time:'Без переезда',price:0,arrival:'12:00',departure:'12:00',returnDeparture:'19:00',overnight:0,note:'Транспорт между городами не нужен.'}];
 const list=[];
 if(city.train)list.push({id:'train',title:city.id==='spb'||city.id==='moscow'?'Скоростной поезд':'Поезд',time:city.train,price:city.fare*pax*2,arrival:city.distance>8?'08:30':'11:10',departure:city.distance>8?'21:00':'07:00',returnDeparture:'18:40',overnight:city.distance>8?1:0,note:'Приезд в центр. Багаж включён. Билеты туда и обратно.'});
 if(city.flight)list.push({id:'flight',title:'Самолёт',time:city.flight,price:Math.round(city.fare*1.55/100)*100*pax*2,arrival:'11:00',departure:'09:20',returnDeparture:'19:30',overnight:0,note:'Без багажа. Добавьте 2 часа на аэропорт. Билеты туда и обратно.'});
 return list;
}
export function currentTransport(s){const opts=transportOptions(s);return opts.find(t=>t.id===s.transport)||opts[0];}
export function transferPrice(s){return s.transfer?((s.adults+s.children)>4?1800:900)*2:0;}
export function planTotal(s,h){return h.price*nights(s)+currentTransport(s).price+transferPrice(s);}
export function searchHotels(s){
 let list=allHotels(s).filter(h=>h.price<=s.budget&&(!s.center||h.center)&&(!s.pets||h.pets)&&(!s.spa||h.spa)&&(!s.quiet||h.quiet)&&(!s.seaside||h.seaside)&&(!s.cancel||h.cancel)&&(!s.totalBudget||planTotal(s,h)<=s.totalBudget));
 if(s.sort==='price')list.sort((a,b)=>a.price-b.price);else if(s.sort==='rating')list.sort((a,b)=>b.rating-a.rating);else list.sort((a,b)=>(b.rating+(b.id.endsWith('light')?.5:0))-(a.rating+(a.id.endsWith('light')?.5:0)));
 return list;
}
export function reasons(s,h){const r=[];if(s.center&&h.center)r.push('Прогулки начинаются у порога');if(s.pets&&h.pets)r.push('Можно с собакой');if(s.spa&&h.spa)r.push('Есть спа для перезагрузки');if(s.quiet&&h.quiet)r.push('Тихие номера для отдыха');if(s.seaside&&h.seaside)r.push('Море рядом');if(s.breakfast)r.push('Завтрак уже включён');if(h.cancel)r.push('Бесплатная отмена за 48 часов');if(h.price<=s.budget*.85)r.push('Остаётся бюджет на впечатления');return r.slice(0,3);}
export function connections(s){
 const t=currentTransport(s); const [ah,am]=t.arrival.split(':').map(Number);const arrival=ah*60+am;const transferMinutes=s.transfer?45:75;const hotelArrival=arrival+transferMinutes;const hour=x=>`${String(Math.floor(x/60)).padStart(2,'0')}:${String(x%60).padStart(2,'0')}`;
 return [
 {ok:true,title:'Даты совпадают',text:`Приезд ${dateLabel(s.checkIn)}, отель до ${dateLabel(s.checkOut)}. ${nights(s)} ночи.`},
 {ok:s.earlyCheckIn||hotelArrival>=14*60,title:s.earlyCheckIn?'Ранний заезд добавлен':hotelArrival<14*60?'До заселения есть время':'Успеваете к заселению',text:s.earlyCheckIn?'В демо ранний заезд с 10:00 подтверждён; доплата включена в отель.':`В отеле примерно в ${hour(hotelArrival)}, заселение с 14:00.${hotelArrival<14*60?' Багаж можно оставить на стойке.':''}`},
 {ok:true,title:'Обратная дорога без спешки',text:`Выезд из отеля до 12:00, отправление в ${t.returnDeparture}. Есть запас на дорогу.`}
 ];
}
const numberWords={'один':1,'одного':1,'одна':1,'одну':1,'два':2,'двое':2,'двух':2,'две':2,'три':3,'трое':3,'трех':3,'три':3,'четыре':4,'четверо':4,'пять':5,'пяти':5,'семь':7,'неделю':7};
const num = v=>numberWords[v]||Number(v);
const months=['январ','феврал','март','апрел','ма','июн','июл','август','сентябр','октябр','ноябр','декабр'];
function amount(v,unit){return Number(v.replace(/\s/g,'').replace(',','.'))*(unit&&/тыс|[кk]/.test(unit)?1000:1);}
export function parseRequest(input,previous,now=new Date()){
 const s={...previous,known:{...previous.known}},text=input.toLowerCase().replace(/ё/g,'е').replace(/\u00a0/g,' '); const changes=[];let understood=false; const set=(key,value,label)=>{understood=true;s.known[key]=true;if(s[key]!==value){s[key]=value;changes.push(label);}};
 const originMatch=text.match(/из\s+([а-я-]+)/);let originCity=null;
 if(originMatch){originCity=CITIES.find(c=>c.aliases.some(a=>originMatch[1].includes(a)));if(originCity)set('origin',originCity.id,'город отправления');}
 const found=CITIES.filter(c=>c.aliases.some(a=>text.includes(a))).filter(c=>!originCity||c.id!==originCity.id||new RegExp(`(?:в|до)\\s+${c.aliases[0]}`).test(text));
 if(found.length)set('city',found[found.length-1].id,'направление');
 const inspiration=/не знаю|куда (?:поехать|съездить)|предложи направ|выбери (?:город|направ)|хочу (?:к морю|на море|в тепло)|вдохнов/.test(text)&&!found.length;
 let nightLimit=null;
 let m=text.match(/(?:до|бюджет(?:ом)?|не (?:больше|дороже)|максимум)\s*(\d[\d\s]*(?:[.,]\d+)?)\s*(тыс\.?|тысяч[аи]?|[кk])?/);
 if(!m&&missingRequiredCriteria(previous)[0]==='budget')m=text.match(/^\s*(\d[\d\s]*(?:[.,]\d+)?)\s*(тыс\.?|тысяч[аи]?|[кk])?\s*(?:рублей|руб|₽)?\s*$/);
 if(m){const value=amount(m[1],m[2]);if(value>0){if(/за ночь|в сутки|за сутки|в ночь/.test(text)){nightLimit=value;set('budget',value,'общий бюджет');}else{set('totalBudget',value,'общий бюджет');s.budget=100000;}}}
 m=text.match(/(?:бюджет|цен[ау])\s*(?:на\s*)?(\d+)\s*%\s*(?:меньше|ниже)/)||text.match(/(?:бюджет|цен[ау])\s*(?:меньше|ниже)\s*на\s*(\d+)\s*%/);
 if(m){const base=s.totalBudget||planTotal(s,searchHotels(s)[0]||allHotels(s)[0]);set('totalBudget',Math.max(1000,Math.round(base*(1-Number(m[1])/100)/100)*100),'общий бюджет');s.budget=100000;}
 else if(/дешевле|бюджет меньше|подешевле/.test(text)&&!text.match(/\d+\s*%/)){const base=s.totalBudget||planTotal(s,searchHotels(s)[0]||allHotels(s)[0]);set('totalBudget',Math.max(1000,Math.round(base*.8/100)*100),'общий бюджет');s.budget=100000;}
 if(/вдвоем|на двоих|нас двое/.test(text))set('adults',2,'гости');if(/втроем|нас трое/.test(text))set('adults',3,'гости');if(/один|одна|соло/.test(text)&&!/ребен|одна ночь|один день/.test(text))set('adults',1,'гости');
 m=text.match(/([1-6]|двое|трое|четверо|два|три|четыре)\s*(?:взросл|гост|человек)/);if(m)set('adults',Math.min(6,num(m[1])),'гости');
 m=text.match(/^(?:нас\s+)?([1-6]|двое|трое|четверо|один|одна|два|три|четыре)\s*[.!]?$/);if(m&&missingRequiredCriteria(previous)[0]==='guests')set('adults',num(m[1]),'гости');
 m=text.match(/([1-4]|два|двое|три)\s*(?:ребен|дет)/);if(m)set('children',num(m[1]),'дети');else if(/с ребенком|с доч|с сын/.test(text))set('children',1,'дети');if(/без детей|без ребенка/.test(text))set('children',0,'дети');
 const features=[['pets',/с собак|с питом|с кот|едет собак/,/без собак|без питом|без кот/,'питомец'],['breakfast',/с завтрак|завтрак(?:и)? (?:включ|нуж|добав)|добавь завтрак/,/без завтрак|убери завтрак/,'завтрак'],['spa',/со? спа|с бассейн|хочу спа|добавь спа/,/без спа|без бассейн|убери спа/,'спа'],['center',/в центре|ближе к центру|центр(?:е)? (?:важен|нужен)/,/не(?:обязательно)? в центре|центр не важен|подальше от центра/,'расположение'],['cancel',/бесплатн.{0,5}отмен|с отмен|возвратный тариф/,/без отмен|невозврат|отмена не важна/,'отмена'],['quiet',/тишин|тихий|тихо|спокойн|перезагруз/,/тишина не важна|можно шумно/,'тишина'],['seaside',/у моря|рядом с морем|на берегу/,/море не важно|необязательно у моря/,'море']];
 for(const [key,yes,no,label]of features){if(no.test(text))set(key,false,label);else if(yes.test(text))set(key,true,label);}
 if(/ранний заезд|пораньше заселиться/.test(text))set('earlyCheckIn',true,'ранний заезд');if(/без раннего|убери ранний/.test(text))set('earlyCheckIn',false,'ранний заезд');
 if(/(?:^|[^а-я])поезд(?:ом|е|а)?(?:$|[^а-я])|по железной дороге|(?:^|\s)жд(?:\s|$)|сапсан/.test(text))set('transport','train','транспорт');if(/самолет|перелет|лететь/.test(text))set('transport','flight','транспорт');
 if(/без трансфера|убери трансфер|сами доберемся/.test(text))set('transfer',false,'трансфер');else if(/трансфер|встретить в аэропорту/.test(text))set('transfer',true,'трансфер');
 m=text.match(/на\s*(\d+|один|одну|два|две|три|четыре|пять|семь|неделю)\s*(ноч[ьи]|дня|дней|день)?/);let duration=null;if(m&&(m[2]||m[1]==='неделю'))duration=Math.min(30,num(m[1]));
 const shift=text.match(/на\s*(\d+|два|две|три|четыре|один|одну|пять|семь)\s*(?:дня|дней|день)\s*(позже|раньше)/);
 if(shift){const delta=num(shift[1])*(shift[2]==='раньше'?-1:1);const next=addDays(s.checkIn,delta);if(next>=iso(now)){set('checkIn',next,'даты');set('checkOut',addDays(s.checkOut,delta),'даты');s.known.checkIn=!!previous.known?.checkIn;s.known.checkOut=!!previous.known?.checkOut;}duration=null;}
 else {
  const range=text.match(/(?:с\s*)?(\d{1,2})\s*(?:по|[-–—])\s*(\d{1,2})\s*(январ[ьяе]|феврал[ьяе]|март[ае]?|апрел[ьяе]|ма[йяе]|июн[ьяе]|июл[ьяе]|август[ае]?|сентябр[ьяе]|октябр[ьяе]|ноябр[ьяе]|декабр[ьяе])/);
  if(range){const month=months.findIndex(x=>range[3].startsWith(x));let year=now.getFullYear();let start=iso(new Date(year,month,Number(range[1]),12));if(start<iso(now)){year++;start=iso(new Date(year,month,Number(range[1]),12));}let end=new Date(year,month,Number(range[2]),12);if(end<=new Date(start+'T12:00:00'))end=new Date(year,month+1,Number(range[2]),12);set('checkIn',start,'даты');set('checkOut',iso(end),'даты');duration=null;}
  else if(text.match(/(?:с\s+)?(\d{1,2})\s+(январ[ьяе]|феврал[ьяе]|март[ае]?|апрел[ьяе]|ма[йяе]|июн[ьяе]|июл[ьяе]|август[ае]?|сентябр[ьяе]|октябр[ьяе]|ноябр[ьяе]|декабр[ьяе])/)){const single=text.match(/(?:с\s+)?(\d{1,2})\s+(январ[ьяе]|феврал[ьяе]|март[ае]?|апрел[ьяе]|ма[йяе]|июн[ьяе]|июл[ьяе]|август[ае]?|сентябр[ьяе]|октябр[ьяе]|ноябр[ьяе]|декабр[ьяе])/);const month=months.findIndex(x=>single[2].startsWith(x));let year=now.getFullYear();let start=iso(new Date(year,month,Number(single[1]),12));if(start<iso(now))start=iso(new Date(year+1,month,Number(single[1]),12));if(previous.known?.checkIn&&!previous.known?.checkOut&&!duration&&!/(?:с\s+\d|заезд|приезд|начиная)/.test(text)){set('checkOut',start,'даты');}else{set('checkIn',start,'даты');set('checkOut',addDays(start,duration||nights(previous)),'даты');s.known.checkOut=!!(duration||previous.known?.duration||previous.known?.checkOut);}duration=null;}
  else if(/выходн/.test(text)){const d=defaultState(now);set('checkIn',d.checkIn,'даты');set('checkOut',addDays(d.checkIn,2),'даты');duration=null;}
  else if(/(?:^|[^а-я])завтра(?:$|[^а-я])/.test(text)){const start=addDays(iso(now),1);set('checkIn',start,'даты');set('checkOut',addDays(start,duration||nights(previous)),'даты');s.known.checkOut=!!(duration||previous.known?.duration||previous.known?.checkOut);duration=null;}
 }
 if(duration){set('checkOut',addDays(s.checkIn,duration),'длительность');s.known.duration=true;if(!s.known.checkIn)s.known.checkOut=false;}
 if(/романт|годовщин/.test(text)){s.mood='вдвоём';s.known.mood=true;set('quiet',true,'атмосфера');}if(/с детьми|семейн/.test(text)){s.mood='семейная поездка';s.known.mood=true;set('quiet',true,'атмосфера');}if(/мор[ею]|в тепло|теплее|отдохнуть|перезагруз/.test(text)){s.mood='перезагрузка';s.known.mood=true;}
 const options=transportOptions(s);if(!options.some(t=>t.id===s.transport))set('transport',options[0].id,'доступный транспорт');
 if(nightLimit!==null)set('totalBudget',nightLimit*nights(s)+currentTransport(s).price+transferPrice(s),'общий бюджет');
 let removedSelection=false;if(s.selectedHotel&&!searchHotels(s).some(h=>h.id===s.selectedHotel)){s.selectedHotel=null;removedSelection=true;}
 const recognized=understood||changes.length||inspiration||/подбери|найди|отел|поехали|покажи|сравни|поездк|привет|вариант|план|куп|брон|оформ|ранний|дешевле/.test(text);
 s.searched=true;
 return {state:s,changes:[...new Set(changes)],inspiration,recognized,removedSelection,intent:/сравни/.test(text)?'compare':/оплат|куп|оформ|корзин|брониру/.test(text)?'checkout':/план поездки|маршрут|собери план/.test(text)?'plan':'search'};
}
export const TEST_CASES=[
 {q:'В Питер на выходные вдвоём до 8 500 за ночь с завтраком',check:s=>s.city==='spb'&&s.adults===2&&s.budget===8500&&s.breakfast&&nights(s)===2},
 {q:'А если на два дня позже?',check:(s,p)=>s.checkIn===addDays(p.checkIn,2)&&nights(s)===nights(p)},
 {q:'С нами едет собака',check:s=>s.pets&&searchHotels(s).every(h=>h.pets)},
 {q:'Бюджет на 20% меньше',check:(s,p)=>s.totalBudget===Math.round(p.totalBudget*.8/100)*100},
 {q:'Казань с 6 по 9 ноября, два взрослых и 1 ребёнок',check:s=>s.city==='kazan'&&s.adults===2&&s.children===1&&nights(s)===3},
 {q:'Без завтрака и без трансфера',check:s=>!s.breakfast&&!s.transfer},
 {q:'Хочу в Сочи со спа, до 15 тысяч за ночь',check:s=>s.city==='sochi'&&s.spa&&s.budget===15000},
 {q:'Не знаю, куда поехать',check:(s,p,r)=>r.inspiration},
 {q:'Из Москвы в Петербург на поезде',check:s=>s.origin==='moscow'&&s.city==='spb'&&s.transport==='train'},
 {q:'До 60 тысяч на всю поездку',check:s=>s.totalBudget===60000&&searchHotels(s).every(h=>planTotal(s,h)<=60000)},
 {q:'Добавь ранний заезд',check:s=>s.earlyCheckIn&&connections(s).every(c=>c.ok)},
 {q:'Хочу в Ярославль на три ночи',check:s=>s.city==='yaroslavl'&&nights(s)===3&&s.transport==='train'}
];
