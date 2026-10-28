import {mountApiApp} from './api-app.js';
import {cancelDemoBooking,isBookingCancelled} from './bookings.js';
import {streamChat,checkAgentHealth} from './api.js';
import {CITIES,money,cityById,defaultState,dateLabel,addDays,iso,nights,allHotels,searchHotels,reasons,transportOptions,currentTransport,transferPrice,planTotal,connections,parseRequest,TEST_CASES,missingRequiredCriteria,recommendationsReady,requiredCriteriaQuestion,recommendHotels} from './engine.js';

const paths={
 arch:'M4 17a8 8 0 0 1 16 0M4 20h16',plus:'M12 5v14M5 12h14',chat:'M5 4h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2h-8l-6 3v-3H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2ZM7 9h10M7 13h6',heart:'M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z',bag:'M5 7h14l1 14H4L5 7ZM9 7V5a3 3 0 0 1 6 0v2',help:'M9.1 9a3 3 0 1 1 5.8 1c-.8 1-2.9 1.5-2.9 3M12 17h.01M22 12A10 10 0 1 1 2 12a10 10 0 0 1 20 0Z',chart:'M4 19V5M4 19h17M8 15v-4M13 15V7M18 15v-7',pin:'M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 1 1 16 0ZM15 10a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z',spark:'m12 2 2.7 7.3L22 12l-7.3 2.7L12 22l-2.7-7.3L2 12l7.3-2.7L12 2Z',arrow:'M5 12h14m-6-6 6 6-6 6',up:'M12 19V5m-6 6 6-6 6 6',send:'M12 19V5m-6 6 6-6 6 6',check:'m5 12 4 4L19 6',close:'m6 6 12 12M6 18 18 6',sliders:'M4 7h10m4 0h2M4 17h2m4 0h10M14 4v6M7 14v6',users:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M16 4a4 4 0 0 1 0 8M22 21v-2a4 4 0 0 0-3-3.9M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z',coffee:'M3 8h14v7a5 5 0 0 1-5 5H8a5 5 0 0 1-5-5V8ZM17 8h2a3 3 0 0 1 0 6h-2M6 2v3M10 2v3M14 2v3',shield:'m12 3 8 4v5c0 5-8 9-8 9s-8-4-8-9V7l8-4Zm-4 9 3 3 5-6',leaf:'M20 3C6 2 2 8 5 15s15 5 15-12ZM4 21l11-12',pet:'M9 15c-4 0-6 7-1 6 2 0 2-1 4-1s2 1 4 1c5 1 3-6-1-6l-3-3-3 3ZM7 6c0 2-1 3-2 3S3 8 3 6s1-3 2-3 2 1 2 3ZM13 4c0 2-1 3-2 3S9 6 9 4s1-3 2-3 2 1 2 3ZM20 6c0 2-1 3-2 3s-2-1-2-3 1-3 2-3 2 1 2 3Z',train:'M5 16V6c0-3 14-3 14 0v10l-3 3H8l-3-3ZM5 11h14M9 5v6m6-6v6M8 15h.01M16 15h.01M8 19l-2 3m10-3 2 3',plane:'m22 2-6 20-4-8-8-4L22 2ZM12 14 22 2',car:'m5 7-2 7v6h3v-3h12v3h3v-6l-2-7H5Zm-2 7h18M7 7l1-3h8l1 3M6 14h.01M18 14h.01',sun:'M12 8a4 4 0 1 1 0 8 4 4 0 0 1 0-8ZM12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5',building:'M5 21V8h14v13M3 21h18M8 8V3h8v5M9 11h1m4 0h1M9 15h1m4 0h1M10 21v-3h4v3',mountain:'m2 21 8-16 5 9 3-5 4 12H2Zm5-10 3 2 2-2',compare:'M8 3v18m8-18v18M4 7h8M12 17h8M4 3h8M12 21h8',clock:'M12 8v5l3 2M22 12A10 10 0 1 1 2 12a10 10 0 0 1 20 0Z',lock:'M6 10h12v11H6V10Zm3 0V6a3 3 0 0 1 6 0v4',download:'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5',refresh:'M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 12-2l2 2M4 17l2 2a7 7 0 0 0 12-2',bell:'M5 17h14l-2-4V8a5 5 0 0 0-10 0v5l-2 4Zm5 3h4',chevron:'m9 5 7 7-7 7',warning:'m12 3 10 18H2L12 3Zm0 6v5m0 3h.01',wave:'M2 12c3-7 5 7 8 0s5 7 8 0 4 0 4 0M2 18c3-7 5 7 8 0s5 7 8 0 4 0 4 0',calendar:'M4 5h16v16H4V5Zm4-3v6m8-6v6M4 11h16',moon:'M20 15A9 9 0 0 1 9 4 9 9 0 1 0 20 15Z',edit:'m15 4 5 5L8 21H3v-5L15 4Z',memory:'M9 3H5v4M19 7V3h-4M5 17v4h4m6 0h4v-4M8 8h8v8H8V8Z'
};
const icon=(name,cls='')=>`<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name]||paths.spark}"/></svg>`;
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const config=window.NA_MALDIVY_CONFIG||{mode:'demo',apiEnabled:false};
let preferredMode;try{preferredMode=localStorage.getItem('na-maldivy-mode');}catch{}
const CHAT_MODE=config.apiEnabled&&config.mode==='api'?'api':'demo';
const key='ostrovok-chat-demo-v2'+(CHAT_MODE==='api'?':api':'');
let connectionStatus=CHAT_MODE==='api'?'connecting':'demo',agentProvider='fake';
let requestGeneration=0,activeRequest=null,modalOpener=null,unread=false;
const renderedMessageIds=new Set(),messageAnimations=new Map();let messageSerial=0;
const messageId=()=>globalThis.crypto?.randomUUID?.()||'m-'+Date.now()+'-'+(++messageSerial);

let saved=null;try{saved=JSON.parse(localStorage.getItem(key));}catch{}
let state={...defaultState(),...(saved?.state||{})};
if(!CITIES.some(c=>c.id===state.city))state.city='spb';
if(!/^\d{4}-\d{2}-\d{2}$/.test(state.checkIn)||state.checkIn<iso(new Date())){const d=defaultState();state.checkIn=d.checkIn;state.checkOut=d.checkOut;state.selectedHotel=null;state.known={...state.known,checkIn:false,checkOut:false,duration:false};}
let messages=Array.isArray(saved?.messages)?saved.messages.slice(-30).map(m=>({...m,id:m.id||messageId()})):[];
for(const m of messages)renderedMessageIds.add(m.id);
let favorites=Array.isArray(saved?.favorites)?saved.favorites:[];
let bookings=Array.isArray(saved?.bookings)?saved.bookings:[];
let metrics={requests:0,clarifications:0,completed:0,started:1,parseTimes:[],firstRecommendationMs:null,...saved?.metrics};
state.known={...state.known};
let panelShown=Object.values(state.known).some(Boolean);
let comparison=[];let busy=false;let lastTests=null;let toastTimer;
const root=document.getElementById('app');const modal=document.getElementById('modal');
function persist(){try{localStorage.setItem(key,JSON.stringify({state,messages:messages.slice(-30),favorites,bookings,metrics}));}catch{toast('Браузер не разрешил сохранить предпочтения. Текущая поездка продолжает работать.');}}
function toast(t){clearTimeout(toastTimer);const el=document.getElementById('toast');el.textContent=t;el.classList.add('show');toastTimer=setTimeout(()=>el.classList.remove('show'),3500);}
function guestText(s=state){return `${s.adults} взр.${s.children?` · ${s.children} дет.`:''}${s.pets?' · питомец':''}`;}
function getHotel(id,s=state){const city=id.split('-')[0];return allHotels({...s,city}).find(h=>h.id===id);}
function selected(){return state.selectedHotel?getHotel(state.selectedHotel):null;}
function selectedOrFirst(){return recommendationsReady(state)?selected()||recommendHotels(state)[0]||null:null;}
function dateRange(s=state){return `${dateLabel(s.checkIn)} — ${dateLabel(s.checkOut)}`;}
function tripSubtitle(s=state){return `${cityById(s.city).short}${s.known?.checkIn?' · '+dateRange(s):''}${s.known?.adults?' · '+guestText(s):''}`;}
function sidebar(){
 const k=state.known||{};
 if(!Object.values(k).some(Boolean))return `<button class="drawer-close" data-action="close-filters" aria-label="Закрыть параметры">${icon('close')}</button>`;
 const opts=transportOptions(state);const features=[['breakfast','coffee','Завтрак'],['cancel','shield','Отмена'],['center','pin','В центре'],['pets','pet','С питомцем'],['spa','leaf','Спа'],['quiet','moon','Тишина']].filter(([f])=>k[f]);
 return `<button class="drawer-close" data-action="close-filters" aria-label="Закрыть параметры">${icon('close')}</button><div class="sidebar-title"><h2>Ваша поездка</h2><span class="synced">${icon('refresh')} Из чата</span></div><div style="height:23px"></div>
 ${k.city?`<div class="side-field"><div class="field-label"><label for="city">Куда едем</label></div><div class="city-field">${icon('pin')}<select class="side-select" id="city" data-field="city">${CITIES.map(c=>`<option value="${c.id}" ${state.city===c.id?'selected':''}>${c.name}</option>`).join('')}</select></div></div>`:''}
 ${k.mood?`<div class="side-field"><div class="field-label">Настроение</div><p class="side-known-value">${esc(state.mood)}</p></div>`:''}
 ${k.checkIn?`<div class="side-field"><div class="field-label"><span>Когда</span><small>${k.checkOut?nights(state)+' ночи':''}</small></div><div class="date-pair"><div class="date-box"><label for="checkIn">ЗАЕЗД</label><input type="date" id="checkIn" data-field="checkIn" min="${iso(new Date())}" value="${state.checkIn}"></div><div class="date-box"><label for="checkOut">ВЫЕЗД</label><input type="date" id="checkOut" data-field="checkOut" min="${addDays(state.checkIn,1)}" value="${k.checkOut?state.checkOut:''}"></div></div></div>`:k.duration?`<div class="side-field"><div class="field-label">Длительность</div><p class="side-known-value">${nights(state)} ночи</p></div>`:''}
 ${k.adults||k.children?`<div class="side-field"><div class="field-label"><span>Кто с нами</span></div><button class="guest-picker" data-action="guests"><span>${icon('users')}${k.adults?guestText():state.children+' дет.'}</span>${icon('chevron')}</button></div>`:''}
 ${k.totalBudget||k.budget?`<div class="side-divider"></div><div class="side-field"><div class="field-label"><label for="budget">Бюджет на всю поездку</label><small>за всех</small></div><div class="budget-values"><strong id="budget-value">${money(state.totalBudget||0)}</strong></div><input class="budget-slider" id="budget" data-field="totalBudget" type="range" min="5000" max="${Math.max(200000,state.totalBudget||0)}" step="1000" value="${state.totalBudget||5000}" style="--progress:${Math.max(0,((state.totalBudget||5000)-5000)/(Math.max(200000,state.totalBudget||0)-5000)*100)}%" aria-valuetext="${money(state.totalBudget||0)} на всю поездку"><div class="range-labels"><span>5 000 ₽</span><span>${money(Math.max(200000,state.totalBudget||0))}</span></div><div class="budget-quick">${[2.5,5,10].map(p=>`<button data-action="budget-increase" data-percent="${p}" aria-label="Увеличить бюджет на ${p}%: ${money(Math.max(1000,Math.round((state.totalBudget||0)*p/100/1000)*1000))}"><b>+${String(p).replace('.',',')}%</b><small>+${money(Math.max(1000,Math.round((state.totalBudget||0)*p/100/1000)*1000))}</small></button>`).join('')}</div><p class="budget-includes">Отель, дорога и трансфер</p></div>`:''}
 ${features.length?`<div class="side-divider"></div><div class="side-field"><div class="field-label"><span>Ваши пожелания</span></div><div class="side-features">${features.map(([f,i,t])=>`<button class="feature-chip ${state[f]?'on':''}" data-action="feature" data-feature="${f}" aria-pressed="${state[f]}">${icon(state[f]?'check':i)}${t}</button>`).join('')}</div></div>`:''}
 ${k.transport||k.origin?`<div class="side-divider"></div><div class="side-field"><div class="field-label"><span>Как добираемся</span></div>${k.origin?`<div class="origin-row">Из <select id="origin" data-field="origin" aria-label="Город отправления">${CITIES.map(c=>`<option value="${c.id}" ${state.origin===c.id?'selected':''}>${c.name}</option>`).join('')}</select></div>`:''}<div class="transport-toggle">${opts.map(t=>`<button data-action="transport" data-transport="${t.id}" class="${currentTransport(state).id===t.id?'on':''}" aria-pressed="${currentTransport(state).id===t.id}">${icon(t.id==='flight'?'plane':t.id==='train'?'train':'pin')}${t.title}</button>`).join('')}</div></div>`:''}
 ${recommendationsReady(state)?`<button class="side-plan" data-action="plan"><span>${selected()&&k.checkIn?'Ваш план · '+money(planTotal(state,selected())):'Собрать поездку'}</span>${icon('arrow')}</button>`:''}`;
}
function card(h,{compact=false}={}){
 return `<article class="hotel-card ${state.selectedHotel===h.id?'selected-card':''}" data-hotel="${h.id}"><div class="hotel-photo"><img src="./assets/${h.image}.jpg" alt="Пример интерьера — ${esc(h.name)}" loading="lazy"><button class="photo-open" data-action="hotel" data-id="${h.id}" aria-label="Посмотреть ${esc(h.name)}"></button><button class="heart ${favorites.includes(h.id)?'on':''}" data-action="favorite" data-id="${h.id}" aria-label="${favorites.includes(h.id)?'Убрать из':'Добавить в'} избранное: ${esc(h.name)}" aria-pressed="${favorites.includes(h.id)}">${icon('heart')}</button><span class="card-photo-label">${h.tag}</span></div><div class="card-content"><div class="hotel-topline"><span class="rating">${String(h.rating).replace('.',',')}</span><span>${h.rating>=9.4?'Превосходно':'Отлично'} · ${h.reviews} отзывов*</span></div><h3><button data-action="hotel" data-id="${h.id}" style="padding:0;text-align:left">${h.name}</button></h3><div class="hotel-location">${icon('pin')}${cityById(h.city).short} · ${h.distance}</div><div class="hotel-tags"><span>${icon(h.breakfast?'coffee':'shield')}${h.breakfast?'Завтрак включён':h.cancel?'Бесплатная отмена':'Выгодный тариф'}</span><span>${icon(state.pets?'pet':'check')}${state.pets?'Можно с питомцем':'Под ваши пожелания'}</span></div><div class="card-price-row"><div><span class="price-amount">${money(h.price)}</span><span class="price-unit">/ ночь</span><div class="price-total">${state.known.checkIn?money(h.price*nights(state))+' за '+nights(state)+' ночи':'Даты можно уточнить в чате'}${state.known.adults?' · '+guestText():''}</div></div><button class="round-arrow" data-action="hotel" data-id="${h.id}" aria-label="Подробнее об отеле ${h.name}">${icon('arrow')}</button></div></div>${compact?'':`<button class="compare-add ${comparison.includes(h.id)?'selected':''}" data-action="compare-add" data-id="${h.id}">${icon(comparison.includes(h.id)?'check':'compare')}${comparison.includes(h.id)?'В сравнении':'Сравнить'}</button>`}</article>`;
}
function results(){
 if(!recommendationsReady(state))return '';
 const list=recommendHotels(state);const first=list[0];
 if(!list.length)return `<div class="empty-result"><strong>Пока нет точного совпадения.</strong><br>Пожелания сохранены. Можно увеличить бюджет, убрать один из фильтров или выбрать другой город.<div class="action-row"><button class="action-button" data-action="raise-budget">Бюджет +5 000 ₽</button><button class="action-button" data-prompt="Центр не важен, без спа">Смягчить расположение и спа</button><button class="action-button" data-prompt="Не знаю, куда поехать">Другие направления</button></div></div>`;
 return `<div class="search-caption"><span>${list.length} ${list.length===1?'вариант':list.length>=2&&list.length<=4?'варианта':'вариантов'}${state.known.checkIn?' · '+dateRange():''}${state.known.adults?' · '+guestText():''}</span><select aria-label="Сортировка отелей" data-field="sort"><option value="recommended" ${state.sort==='recommended'?'selected':''}>Сначала подходящие</option><option value="price" ${state.sort==='price'?'selected':''}>Сначала дешевле</option><option value="rating" ${state.sort==='rating'?'selected':''}>По рейтингу</option></select></div><div class="hotel-grid">${list.slice(0,3).map(h=>card(h)).join('')}</div><div class="reasons-panel"><b>Почему ${first.name.toLowerCase()}?</b>${reasons(state,first).map(esc).join(' · ')}.<br>Компромисс: ${esc(first.risk)}</div><div class="action-row"><button class="action-button" data-action="compare-results">${icon('compare')} Сравнить варианты</button><button class="action-button" data-action="transport-modal">${icon('train')} Как добраться</button><button class="action-button" data-action="plan">${icon('bag')} Собрать поездку</button></div><p class="demo-data-note">* Отели, цены, рейтинги и отзывы — тестовые данные, не реальные предложения.</p>`;
}
function directions(){
 if(!recommendationsReady(state))return '';
 const list=state.mood==='перезагрузка'?[cityById('sochi'),cityById('kaliningrad'),cityById('kazan')]:[cityById('spb'),cityById('kazan'),cityById('sochi')];
 return `<div class="direction-grid">${list.map(c=>{const temp={...state,city:c.id,center:false,spa:false,seaside:false,transport:c.train?'train':'flight',budget:20000,totalBudget:null};const h=searchHotels(temp).sort((a,b)=>a.price-b.price)[0];return `<button class="direction-card" data-action="direction" data-city="${c.id}"><div class="direction-art" style="background:${c.accent}">${icon(c.id==='sochi'?'sun':c.id==='spb'?'building':'mountain')}</div><div class="direction-body"><h3>${c.name}</h3><p>${c.why}</p><div class="direction-meta">${c.weather} · ${c.climate}<br>${c.flight?'Самолёт '+c.flight:'Поезд '+c.train} из Москвы*</div><div class="direction-bottom"><b>от ${money(h?planTotal(temp,h):30000)}</b>${icon('arrow')}</div></div></button>`;}).join('')}</div><p class="demo-data-note">* Сценарные данные для поездки из Москвы: условная осенняя погода, примерное время в пути и стоимость за ${nights(state)} ночи для ${guestText()}. Это не прогноз и не актуальные билеты.</p>`;
}
function inlinePlan(h){if(!recommendationsReady(state))return '';if(!h)return `<div class="empty-result">Сначала подберём подходящий отель. Напишите город или измените фильтры.</div>`;const t=currentTransport(state);return `<div class="plan-inline"><div class="section-head"><h2>${cityById(state.city).short}, мы едем</h2><span>${dateRange()}</span></div><div class="plan-line">${icon('building')}<div><b>${h.name}</b><small>${nights(state)} ночи · ${guestText()}${state.breakfast?' · завтрак':''}</small></div><b>${money(h.price*nights(state))}</b></div><div class="plan-line">${icon(t.id==='flight'?'plane':'train')}<div><b>${t.title} · ${t.time}</b><small>${cityById(state.origin).short} → ${cityById(state.city).short} · туда и обратно</small></div><b>${money(t.price)}</b></div>${state.transfer?`<div class="plan-line">${icon('car')}<div><b>Трансфер до отеля и обратно</b><small>Встреча по прибытии · под даты поездки</small></div><b>${money(transferPrice(state))}</b></div>`:''}<div class="plan-sum"><span>Вся поездка</span><strong>${money(planTotal(state,h))}</strong></div></div><div class="action-row"><button class="action-button primary" data-action="checkout">Перейти к оформлению ${icon('arrow')}</button><button class="action-button" data-action="plan">Проверить детали</button></div>`;}
function messageExtras(m,isLast){
 if(CHAT_MODE!=='demo'||!isLast||!recommendationsReady(state)||['clarify','error','api'].includes(m.kind))return '';
 if(m.kind==='inspire')return directions();
 if(m.kind==='plan')return inlinePlan(selectedOrFirst());
 if(m.kind==='unknown')return '';
 return results();
}
function stream(){
 if(!messages.length&&!busy)return '';
 const lastAssistant=messages.findLastIndex(m=>m.role==='assistant');
 const content=messages.map((m,i)=>{
  const fresh=!renderedMessageIds.has(m.id),latest=i===lastAssistant;if(fresh)messageAnimations.set(m.id,performance.now());const age=performance.now()-(messageAnimations.get(m.id)??-Infinity),entering=age<340;
  const classes=`message ${m.role} ${entering?'message-enter':''} ${m.kind==='error'?'error':''} ${busy&&CHAT_MODE==='api'&&latest&&m.kind==='api'?'is-streaming':''}`;
  if(m.role==='user')return `<div class="${classes}" data-message-id="${m.id}" ${entering?`style="animation-delay:-${Math.round(age)}ms"`:''}><div class="user-bubble">${esc(m.text)}</div></div>`;
  const text=CHAT_MODE==='demo'&&latest&&!recommendationsReady(state)&&m.kind!=='error'?requiredCriteriaQuestion(state):m.text;
  return `<div class="${classes}" data-message-id="${m.id}" ${entering?`style="animation-delay:-${Math.round(age)}ms"`:''}><div class="assistant-label"><span class="assistant-mini">${icon('arch')}</span>${CHAT_MODE==='api'?'О!дин запрос':'Островок · рядом'}</div><div class="assistant-text">${esc(text)}</div>${m.kind==='error'?`<div class="message-error-note">${m.text&&m.errorText?esc(m.errorText):''}<button class="retry-action" data-action="retry" data-id="${m.id}">${icon('refresh')}Повторить</button></div>`:''}${m.changes?.length?`<div class="change-tags">${m.changes.map(c=>`<span>${icon('check')}${esc(c)}</span>`).join('')}</div>`:''}<div class="${fresh?'results-enter':''}">${messageExtras(m,latest)}</div></div>`;
 }).join('');
 const loading=busy&&!(CHAT_MODE==='api'&&messages.at(-1)?.kind==='api'&&messages.at(-1)?.text);
 return `<div class="chat-stream">${content}${loading?`<div class="typing-pill" role="status"><span class="assistant-mini">${icon('arch')}</span><span>${CHAT_MODE==='api'?'Готовим ответ':'Сверяем пожелания'}</span><span class="typing" aria-hidden="true"><i></i><i></i><i></i></span></div>`:''}</div>`;
}
function upgrades(){
 if(CHAT_MODE!=='demo'||!recommendationsReady(state)||!messages.length||busy)return '';
 const options=[];
 if(!state.breakfast)options.push(['breakfast','coffee','Утро с завтраком','+'+money(450*nights(state))+' к поездке']);
 if(!state.spa){const target=searchHotels({...state,spa:true,budget:100000,totalBudget:null}).sort((a,b)=>a.price-b.price)[0];const baseline=selectedOrFirst();if(target&&baseline){const delta=Math.max(0,target.price-baseline.price);options.push(['spa','leaf','Немного спа',delta?'+'+money(delta*nights(state))+' к поездке':'без доплаты']);}}
 if(!state.earlyCheckIn)options.push(['earlyCheckIn','sun','Заселиться пораньше','+35% одной ночи']);
 if(!options.length)options.push(['inspiration','spark','Ещё немного вдохновения','выбрать город']);
 return options.map(([f,i,t,p])=>`<button class="upgrade" data-action="upgrade" data-feature="${f}">${icon(i)}${t}<b>${p}</b></button>`).join('');
}
function connectionBadge(){
 const label=CHAT_MODE==='demo'?'Демо':connectionStatus==='connecting'?'Подключение…':connectionStatus==='offline'?'Нет связи':agentProvider==='fake'?'Тестовый агент':'Агент проекта';
 return `<button class="connection-badge" data-status="${connectionStatus}" data-action="mode" aria-label="Режим работы: ${label}"><i></i>${label}</button>`;
}
function captureView(){
 const area=root.querySelector('.chat-scroll'),input=root.querySelector('#chat-input'),active=document.activeElement;
 return {top:area?.scrollTop||0,nearBottom:!area||area.scrollHeight-area.scrollTop-area.clientHeight<100,draft:input?.value||'',height:input?.style.height||'',focus:active?.id,selection:input?[input.selectionStart,input.selectionEnd]:null,cars:[...root.querySelectorAll('.hotel-grid,.upgrade-row')].map(el=>el.scrollLeft),side:root.querySelector('.sidebar')?.scrollTop||0,drawer:root.querySelector('.sidebar')?.classList.contains('open')};
}
function render({scroll=false,forceBottom=false}={}){
 for(const m of messages)if(!m.id)m.id=messageId();
 const view=captureView();

 const hasCriteria=CHAT_MODE==='demo'&&Object.values(state.known).some(Boolean);const revealCriteria=hasCriteria&&!panelShown;panelShown=hasCriteria;
 const drawerOpen=view.drawer;
 root.innerHTML=`<div class="app-shell ${hasCriteria?'has-criteria':'no-criteria'} ${revealCriteria?'reveal-criteria':''}"><header class="topbar"><button class="brand" data-action="home" aria-label="Островок, главный экран"><span class="brand-mark">${icon('arch')}</span>островок</button><div class="header-actions">${connectionBadge()}<button class="text-icon ${CHAT_MODE==='api'?'hidden':''}" data-action="favorites" aria-label="Избранное">${icon('heart')}<span>Избранное</span><span class="saved-count">${favorites.length}</span></button>${hasCriteria?`<button class="mobile-filters" data-action="filters">${icon('sliders')}Поездка</button>`:''}<button class="avatar" data-action="memory" aria-label="Предпочтения">ВЫ</button></div></header><nav class="rail" aria-label="Основная навигация"><button class="new-trip" data-action="new" aria-label="Новая поездка">${icon('plus')}</button><button class="active" data-action="home" aria-label="Чат с Островком">${icon('chat')}</button><button class="${CHAT_MODE==='api'?'hidden':''}" data-action="bookings" aria-label="Мои поездки">${icon('bag')}</button><button class="${CHAT_MODE==='api'?'hidden':''}" data-action="favorites" aria-label="Избранное">${icon('heart')}</button><button class="rail-bottom ${CHAT_MODE==='api'?'hidden':''}" data-action="metrics" aria-label="Метрики и тесты">${icon('chart')}</button><button data-action="about" aria-label="О демо">${icon('help')}</button></nav><main class="workspace"><div class="chat-scroll" role="log" aria-live="polite" aria-relevant="additions text" aria-busy="${busy}" aria-label="Диалог и рекомендации">${stream()}</div>${comparison.length?`<div class="comparison-bar"><span>В сравнении: ${comparison.length} из 3</span><button data-action="compare-open">Сравнить</button><button class="bar-close" data-action="compare-clear" aria-label="Очистить сравнение">${icon('close')}</button></div>`:''}<button class="scroll-latest ${unread?'':'hidden'}" data-action="scroll-latest">${icon('arrow')}К новым сообщениям</button><footer class="composer-wrap">${upgrades()?`<div class="upgrade-row">${upgrades()}</div>`:''}<form id="chat-form" class="input-box"><textarea id="chat-input" aria-label="Расскажите о поездке" placeholder="Расскажите, куда хочется…" rows="1" maxlength="4000"></textarea><button class="send-button ${busy?'is-busy':''}" type="${busy?'button':'submit'}" ${busy?'data-action="stop"':''} aria-label="${busy?'Остановить ответ':'Отправить сообщение'}">${busy?'<span class="stop-square"></span>':icon('send')}</button></form><div class="composer-foot"><span>${CHAT_MODE==='demo'?'Демонстрационные данные · Без реальной оплаты':'Агент проекта · Ответы приходят с сервера'}</span><button data-action="about">${icon('shield')}Как это работает</button><button class="${CHAT_MODE==='api'?'hidden':''}" data-action="bookings">${icon('bag')}Мои поездки</button><button class="${CHAT_MODE==='api'?'hidden':''}" data-action="metrics">${icon('chart')}Тесты</button></div></footer></main><aside class="sidebar ${drawerOpen?'open':''}" aria-label="Параметры поездки">${sidebar()}</aside><button class="drawer-backdrop ${drawerOpen?'open':''}" data-action="close-filters" aria-label="Закрыть панель параметров"></button></div>`;
 const area=root.querySelector('.chat-scroll'),input=root.querySelector('#chat-input');
 input.value=view.draft;input.style.height=view.height;
 if(view.focus==='chat-input'){input.focus({preventScroll:true});if(view.selection)input.setSelectionRange(...view.selection);}
 else if(view.focus){const target=document.getElementById(view.focus);target?.focus({preventScroll:true});}
 [...root.querySelectorAll('.hotel-grid,.upgrade-row')].forEach((el,i)=>el.scrollLeft=view.cars[i]||0);
 root.querySelector('.sidebar').scrollTop=view.side;
 const lastMessage=area.querySelector('.message:last-of-type');
 if(forceBottom||scroll&&view.nearBottom){const max=area.scrollHeight-area.clientHeight;area.scrollTop=lastMessage&&lastMessage.offsetHeight>area.clientHeight?Math.max(0,Math.min(max,lastMessage.offsetTop-area.offsetTop-20)):max;}else area.scrollTop=view.top;
 if(scroll&&!view.nearBottom&&!forceBottom){unread=true;root.querySelector('.scroll-latest').classList.remove('hidden');}
 area.addEventListener('scroll',()=>{if(area.scrollHeight-area.scrollTop-area.clientHeight<80){unread=false;root.querySelector('.scroll-latest').classList.add('hidden');}},{passive:true});
 for(const m of messages)renderedMessageIds.add(m.id);
 if(busy)root.querySelectorAll('.sidebar button:not(.drawer-close),.sidebar input,.sidebar select,.upgrade').forEach(el=>el.disabled=true);
 root.querySelectorAll('img').forEach(img=>img.addEventListener('error',()=>{img.style.opacity='0';img.closest('.hotel-photo')?.classList.add('image-unavailable');},{once:true}));
 if(revealCriteria&&window.matchMedia('(max-width:950px)').matches&&!view.draft&&!view.focus)openFilters();

}
function openModal(title,body,footer='',subtitle=''){
 if(!modal.open)modalOpener=document.activeElement;
 modal.innerHTML=`<div class="modal-head"><div><h2 id="modal-title">${title}</h2>${subtitle?`<small>${subtitle}</small>`:''}</div><button class="close-modal" data-action="close-modal" aria-label="Закрыть окно">${icon('close')}</button></div><div class="modal-body">${body}</div>${footer?`<div class="modal-footer">${footer}</div>`:''}`;
 modal.setAttribute('aria-labelledby','modal-title');if(!modal.open)modal.showModal();modal.scrollTop=0;
}
function closeModal(){modal.close();if(modalOpener?.isConnected)modalOpener.focus({preventScroll:true});}
function ensureRequiredCriteria(){
 if(recommendationsReady(state))return true;
 closeModal();
 const question=requiredCriteriaQuestion(state);
 const last=messages.at(-1);
 if(last?.role!=='assistant'||last.text!==question){messages.push({role:'assistant',kind:'clarify',text:question});metrics.clarifications++;}
 comparison=[];persist();render({scroll:true});return false;
}
function checksHtml(){return connections(state).map(c=>`<div class="check-row ${c.ok?'':'warning'}">${icon(c.ok?'check':'clock')}<div><b>${c.title}</b>${c.text}</div></div>`).join('');}
function hotelModal(id){
 if(!ensureRequiredCriteria())return;
 const h=getHotel(id);if(!h)return;
 const rr=reasons(state,h);const total=state.known.checkIn?h.price*nights(state):h.price;
 openModal(h.name,`<img class="modal-photo" src="./assets/${h.image}.jpg" alt="Пример интерьера ${h.name}"><div class="detail-meta"><span class="rating">${String(h.rating).replace('.',',')}</span><span>${h.style} · ${h.stars}★ · ${cityById(h.city).name}</span></div><p class="detail-description">${h.desc}</p><div class="detail-section"><h3>Почему вам подойдёт</h3><div class="detail-reasons">${rr.map(r=>`<span>${icon('check')}${r}</span>`).join('')}</div></div><div class="risk-note"><b>Честно о компромиссе.</b> ${h.risk}<br>${h.cancel?'Бесплатная отмена до 12:00 за 2 дня до заезда. Затем штраф — одна ночь.':'Невозвратный тариф. При отмене стоимость проживания не возвращается.'}${state.pets&&h.pets?'<br>Доплата за питомца 300 ₽ / ночь уже включена.':''}</div><div class="detail-section"><h3>Что входит в цену</h3><p class="detail-description">${state.known.adults?'Номер на '+guestText()+'. ':''}${state.known.checkIn?nights(state)+' ночи. ':''} ${state.breakfast?'Завтрак включён.':'Завтрак можно добавить за 450 ₽ / ночь.'} ${state.earlyCheckIn?'Ранний заезд включён.':'Заселение с 14:00, выезд до 12:00.'} Налоги включены.</p></div><p class="demo-data-note">Это вымышленный отель из демо-каталога. Фотография иллюстративная, Unsplash. Цены и отзывы тестовые.</p>`,`<div><strong>${money(total)}</strong><small>${state.known.checkIn?dateRange()+' · '+nights(state)+' ночи':'За ночь · даты уточним в чате'}</small></div><button class="primary-button" data-action="select-hotel" data-id="${h.id}">Добавить в поездку ${icon('plus')}</button>`,`${h.distance} · демо-отель`);
}
function selectHotel(id){
 if(!ensureRequiredCriteria())return;
 const h=getHotel(id);if(!h)return;
 state.city=h.city;state.known.city=true;state.selectedHotel=id;state.searched=true;comparison=[];
 if(!state.known.checkIn){messages.push({role:'assistant',kind:'clarify',text:`${h.name} сохранил для поездки. На какие даты планируем отдых?`});persist();closeModal();render({scroll:true});return;}
 messages.push({role:'assistant',kind:'plan',text:`${h.name} добавлен в поездку. Собрал дорогу и трансфер на те же даты. Ниже — полная стоимость; перед оплатой проверим время приезда и заселения.`});persist();closeModal();render({scroll:true});
}
function transportHtml(){
 return `<div class="transport-options">${transportOptions(state).map(t=>`<button class="transport-option ${currentTransport(state).id===t.id?'on':''}" data-action="choose-transport" data-transport="${t.id}">${icon(t.id==='flight'?'plane':t.id==='train'?'train':'pin')}<div><b>${t.title} · ${t.time}</b><small>${t.note}<br>${t.overnight?`Отправление ${dateLabel(addDays(state.checkIn,-t.overnight))} в ${t.departure}`:`Отправление ${dateLabel(state.checkIn)} в ${t.departure}`} · приезд ${dateLabel(state.checkIn)} в ${t.arrival}<br>Обратно ${dateLabel(state.checkOut)} в ${t.returnDeparture}</small><strong>${money(t.price)}</strong></div></button>`).join('')}</div><p class="demo-data-note">Стоимость за всех гостей, туда и обратно. Маршруты и расписания сценарные. ${state.origin!=='moscow'?'Для этого города отправления используются упрощённые тестовые тарифы.':''}</p>`;
}
function transferHtml(){return `<div class="transfer-box"><div><b>Встретить и отвезти в отель</b><small>Трансфер в обе стороны · ${money((state.adults+state.children>4?1800:900)*2)}</small></div><button class="toggle ${state.transfer?'on':''}" role="switch" aria-checked="${state.transfer}" aria-label="Включить трансфер" data-action="transfer"></button></div>`;}
function transportModal(){if(!ensureRequiredCriteria())return;openModal('Дорога — часть путешествия',transportHtml()+transferHtml()+`<div class="detail-section"><h3>Проверили стыковки</h3>${checksHtml()}</div>`,`<span class="demo-data-note">${guestText()} · ${dateRange()}</span><button class="primary-button" data-action="close-modal">Готово ${icon('check')}</button>`,`${cityById(state.origin).short} → ${cityById(state.city).short}`);}
function planModal(){
 if(!ensureRequiredCriteria())return;
 const h=selectedOrFirst();if(!h){openModal('Соберём вашу поездку',`<div class="modal-empty">${icon('bag')}<h3>Начнём с отеля</h3>По текущим пожеланиям нет подходящего варианта.<br>Измените бюджет или фильтры.<button class="primary-button" data-action="close-modal">Вернуться к подбору</button></div>`);return;}
 openModal('Всё складывается',`<div class="demo-note">${selected()?'Выбранный отель сохранён.':'В плане пока рекомендованный отель — можно заменить его в чате.'} Состав поездки и итог пересчитываются вместе с параметрами.</div>${inlinePlan(h).replace(/<div class="action-row">[\s\S]*$/,'')}<div class="detail-section"><h3>Выберите дорогу</h3>${transportHtml()}${transferHtml()}</div><div class="detail-section"><h3>Стыковки под контролем</h3>${checksHtml()}</div>${!state.earlyCheckIn?`<button class="action-button" data-action="early-plan">${icon('sun')} Добавить ранний заезд · +35% одной ночи</button>`:''}`,`<div><strong>${money(planTotal(state,h))}</strong><small>Отель, дорога${state.transfer?' и трансфер':''} · за всех</small></div><button class="primary-button" data-action="checkout">К оформлению ${icon('arrow')}</button>`,tripSubtitle());
}
function checkoutModal(){
 if(!ensureRequiredCriteria())return;
 const h=selectedOrFirst();if(!h){planModal();return;}
 state.selectedHotel=h.id;persist();
 openModal('Остался один приятный шаг',`<div class="demo-note">Демонстрационное оформление. Не вводите реальные личные или платёжные данные: здесь нет списаний и настоящих броней.</div><div class="plan-inline" style="margin-top:0"><div class="plan-line">${icon('building')}<div><b>${h.name} · ${cityById(state.city).short}</b><small>${dateRange()} · ${nights(state)} ночи · ${guestText()}</small></div><b>${money(h.price*nights(state))}</b></div><div class="plan-line">${icon('train')}<div><b>${currentTransport(state).title} и трансфер</b><small>Туда и обратно · ${state.transfer?'встреча включена':'без трансфера'}</small></div><b>${money(currentTransport(state).price+transferPrice(state))}</b></div></div><div class="detail-section"><h3>Данные для демо-поездки</h3><form id="checkout-form"><div class="checkout-grid"><label>Имя<input name="name" required autocomplete="off" maxlength="50" value="Александр" placeholder="Любое тестовое имя"></label><label>Фамилия<input name="surname" required autocomplete="off" maxlength="50" value="Путешественник"></label><label class="full">Email для демо<input name="email" type="email" required autocomplete="off" value="traveler@example.com"></label></div><label class="checkbox-label"><input type="checkbox" name="terms" required checked><span>Понимаю, что это демонстрация. Билеты и номер не будут забронированы, деньги не спишутся.</span></label></form></div><div class="detail-section"><h3>Условия и время</h3>${checksHtml()}<p class="demo-data-note">${h.cancel?'Отель: отмена без штрафа за 48 часов до заезда.':'Отель: невозвратный тариф.'} Билеты: возврат с условным сбором 1 000 ₽ с пассажира.</p></div>`,`<div><strong>${money(planTotal(state,h))}</strong><small>Все услуги и тестовые сборы включены</small></div><button class="primary-button" type="submit" form="checkout-form">${icon('lock')}Оплатить понарошку</button>`,tripSubtitle());
}
function completeBooking(form){
 if(!form.reportValidity())return;
 const h=selected();if(!h)return;
 const data=new FormData(form);const booking={id:'DEMO-'+String(Date.now()).slice(-6),state:{...state},hotel:h.id,total:planTotal(state,h),name:String(data.get('name')),created:new Date().toISOString(),status:'confirmed',early:state.earlyCheckIn,reminder:false};
 bookings.unshift(booking);metrics.completed++;persist();render();
 openModal('Можно собирать чемодан',`<div class="success-view"><div class="success-circle">${icon('check')}</div><h3>Ваша поездка собралась!</h3><p>${esc(booking.name)}, ${cityById(state.city).short} ждёт.<br>${dateRange()} · ${h.name}<br>Демо-заказ <b>${booking.id}</b></p><div class="booking-badge" style="justify-content:center;margin-top:20px">${icon('shield')}Оплата имитирована. Деньги не списывались.</div><p>План сохранён в «Моих поездках».<br>Там можно изменить даты, добавить ранний заезд<br>скачать напоминание или отменить поездку.</p><button class="primary-button" data-action="bookings">Моя поездка ${icon('arrow')}</button></div>`);
}
function favoritesModal(){
 if(favorites.length&&!ensureRequiredCriteria())return;
 const list=favorites.map(id=>getHotel(id)).filter(Boolean);
 openModal('Места, которые запомнились',list.length?`<div class="hotel-grid">${list.map(h=>card(h)).join('')}</div><p class="demo-data-note">Избранное сохраняется только в этом браузере. В карточках — цена на текущие даты.</p>`:`<div class="modal-empty">${icon('heart')}<h3>Здесь будет ваше «то самое»</h3>Нажмите сердечко на карточке отеля,<br>чтобы сохранить его для следующей поездки.<button class="primary-button" data-action="close-modal">Вернуться к подбору</button></div>`, '',`${list.length} сохранённых вариантов`);
}
function compareModal(){
 if(!ensureRequiredCriteria())return;
 if(comparison.length<2){const available=searchHotels(state);comparison=available.slice(0,3).map(h=>h.id);}
 const list=comparison.map(id=>getHotel(id)).filter(Boolean);if(!list.length){toast('Для сравнения нужны подходящие варианты.');return;}
 const rows=[['Цена за ночь',h=>money(h.price)],['Вся поездка',h=>state.known.checkIn?money(planTotal(state,h)):'Уточним даты в чате'],['Почему вам подходит',h=>reasons(state,h).join('. ')],['Отмена',h=>h.cancel?'Бесплатно за 48 часов':'Невозвратный тариф'],['Питание',()=>state.breakfast?'Завтрак включён':'Без завтрака · можно добавить'],['Расположение',h=>h.distance],['С питомцем',h=>h.pets?'Можно · +300 ₽/ночь':'Нельзя'],['Спа',h=>h.spa?'Есть · по записи':'Нет'],['Компромисс / риск',h=>h.risk]];
 openModal('Сравним по важному для вас',`<div class="comparison-scroll"><table class="compare-table"><thead><tr><th>Ваши критерии</th>${list.map((h,i)=>`<td class="compare-name ${i===0?'best':''}"><img src="./assets/${h.image}.jpg" alt="Интерьер ${h.name}">${h.name}<small>${i===0?'Рекомендуем по вашим пожеланиям':h.style}</small></td>`).join('')}</tr></thead><tbody>${rows.map(([title,fn])=>`<tr><th>${title}</th>${list.map((h,i)=>`<td class="${i===0?'best':''}">${esc(fn(h))}</td>`).join('')}</tr>`).join('')}<tr><th></th>${list.map(h=>`<td><button data-action="select-hotel" data-id="${h.id}">Выбрать ${icon('arrow')}</button></td>`).join('')}</tr></tbody></table></div><p class="demo-data-note">Демо-каталог. Сравнение учитывает текущие даты, состав гостей, питание, дорогу и трансфер.</p>`,'',tripSubtitle());render();
}
function guestsModal(){openModal('Кто отправляется в путь',`<div class="guests-row"><div>Взрослые<small>От 18 лет</small></div><div class="stepper"><button data-action="guest-step" data-field="adults" data-delta="-1" aria-label="Меньше взрослых" ${state.adults<=1?'disabled':''}>−</button><strong>${state.adults}</strong><button data-action="guest-step" data-field="adults" data-delta="1" aria-label="Больше взрослых" ${state.adults>=6?'disabled':''}>+</button></div></div><div class="guests-row"><div>Дети<small>Демо-тариф без разделения по возрасту</small></div><div class="stepper"><button data-action="guest-step" data-field="children" data-delta="-1" aria-label="Меньше детей" ${state.children<=0?'disabled':''}>−</button><strong>${state.children}</strong><button data-action="guest-step" data-field="children" data-delta="1" aria-label="Больше детей" ${state.children>=4?'disabled':''}>+</button></div></div><div class="guests-row"><div>Питомец<small>Покажем только отели, где ему рады</small></div><button class="toggle ${state.pets?'on':''}" role="switch" aria-checked="${state.pets}" data-action="guest-pet" aria-label="С питомцем"></button></div>`,`<span class="demo-data-note">${guestText()}</span><button class="primary-button" data-action="guests-done">Готово ${icon('check')}</button>`);}
function bookingDateTime(value){
 const date=new Date(value);return Number.isNaN(date.getTime())?'':date.toLocaleString('ru-RU',{day:'numeric',month:'long',year:'numeric',hour:'2-digit',minute:'2-digit'});
}
function editableBooking(id){
 const b=bookings.find(item=>item.id===id);
 if(!b)return null;
 if(isBookingCancelled(b)){toast('Эта поездка уже отменена.');bookingsModal();return null;}
 return b;
}
function bookingCard(b){
 const h=getHotel(b.hotel,b.state),cancelled=isBookingCancelled(b);
 return `<article class="plan-inline booking-record ${cancelled?'booking-cancelled':''}" data-booking-id="${esc(b.id)}"><div class="booking-record-head"><span class="booking-badge ${cancelled?'cancelled-badge':''}">${icon(cancelled?'close':'check')}${cancelled?'Поездка отменена':'Оплата имитирована'}</span><span class="booking-order-number">${esc(b.id)}</span></div><div class="section-head"><h2>${cityById(b.state.city).short} · ${esc(h?.name||'Отель')}</h2></div><p class="detail-description">${dateRange(b.state)} · ${guestText(b.state)}<br>Стоимость заказа — ${money(b.total)}${!cancelled&&b.early?'<br>Ранний заезд с 10:00 добавлен.':''}${!cancelled&&b.reminder?'<br>Напоминание подготовлено для календаря.':''}</p>${cancelled?`<div class="cancellation-summary"><b>Вся поездка отменена в демо.</b><p>${b.cancelledAt?'Дата отмены: '+esc(bookingDateTime(b.cancelledAt))+'. ':''}Возврат имитируется: реальной оплаты и списаний не было.</p>${b.reminder?'<p>Если вы импортировали поездку в календарь, удалите событие вручную.</p>':''}</div>`:`<div class="booking-actions"><button class="action-button" data-action="booking-change" data-id="${esc(b.id)}">${icon('calendar')}Изменить даты</button><button class="action-button" data-action="booking-early" data-id="${esc(b.id)}">${icon('sun')}${b.early?'Ранний заезд добавлен':'Запросить ранний заезд'}</button><button class="action-button" data-action="booking-transfer" data-id="${esc(b.id)}">${icon('car')}${b.state.transfer?'Трансфер включён':'Добавить трансфер'}</button><button class="action-button" data-action="booking-reminder" data-id="${esc(b.id)}">${icon('download')}В календарь</button></div><div class="booking-cancel-row"><button class="cancel-trip-button" data-action="booking-cancel" data-id="${esc(b.id)}">${icon('close')}Отменить поездку</button></div>`}</article>`;
}
function bookingsModal(){
 if(!bookings.length){openModal('Мои поездки',`<div class="modal-empty">${icon('bag')}<h3>Впереди столько всего</h3>Соберите поездку в чате и пройдите<br>демо-оплату — план появится здесь.<button class="primary-button" data-action="close-modal">Начать путешествие</button></div>`);return;}
 openModal('Мои поездки',bookings.map(bookingCard).join('')+`<p class="demo-data-note">Заказы и статусы хранятся в этом браузере. Бронирования, изменения, отмена и возврат оплаты имитируются. Запросы в отели и перевозчикам не отправляются.</p>`);
}
function cancelBookingModal(id){
 const b=editableBooking(id);if(!b)return;
 const h=getHotel(b.hotel,b.state);
 openModal('Отменить поездку?',`<div class="cancel-booking-intro"><span class="cancel-booking-icon">${icon('bag')}</span><h3>${cityById(b.state.city).short} · ${esc(h?.name||'Отель')}</h3><p>${dateRange(b.state)} · ${guestText(b.state)}</p></div><div class="cancellation-scope"><h3>Отменится весь демо-заказ</h3><p>${icon('check')}Отель на все даты поездки</p><p>${icon('check')}Дорога туда и обратно</p>${b.state.transfer?`<p>${icon('check')}Трансфер в обе стороны</p>`:''}</div><div class="cancellation-total"><span>Стоимость заказа</span><strong>${money(b.total)}</strong></div><div class="demo-note">Это демонстрация. Реальной оплаты не было, поэтому возврат только имитируется. Заказ останется в истории со статусом «Отменена».</div>`,`<button class="secondary-button" data-action="bookings">Сохранить поездку</button><button class="danger-button" data-action="booking-cancel-confirm" data-id="${esc(b.id)}">Да, отменить поездку</button>`,`Заказ ${esc(b.id)}`);
}
function confirmBookingCancellation(id){
 const b=editableBooking(id);if(!b)return;
 const cancelled=cancelDemoBooking(b);
 bookings=bookings.map(item=>item.id===id?cancelled:item);
 persist();bookingsModal();toast('Поездка отменена. Возврат имитирован — реальной оплаты не было.');
}
function bookingDates(id){const b=editableBooking(id);if(!b)return;openModal('Поменяем даты',`<div class="demo-note">Изменение демо-заказа. Стоимость будет пересчитана по количеству ночей; дорога и трансфер сдвинутся вместе.</div><form id="booking-dates" data-id="${id}"><div class="checkout-grid"><label>Заезд<input type="date" name="checkIn" min="${iso(new Date())}" value="${b.state.checkIn}" required></label><label>Выезд<input type="date" name="checkOut" min="${addDays(b.state.checkIn,1)}" value="${b.state.checkOut}" required></label></div></form>`,`<span class="demo-data-note">Остальные пожелания сохранятся</span><button class="primary-button" type="submit" form="booking-dates">Пересобрать поездку</button>`);}
function downloadICS(b){
 if(isBookingCancelled(b)){toast('Для отменённой поездки напоминание не создаётся.');return;}
 const s=b.state;const compact=d=>d.replaceAll('-','');const body=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//Ostrovok Demo//Trip//RU','CALSCALE:GREGORIAN','BEGIN:VEVENT',`UID:${b.id}@ostrovok-demo`,`DTSTAMP:${new Date().toISOString().replace(/[-:]/g,'').replace(/\.\d{3}/,'')}`,`DTSTART;VALUE=DATE:${compact(s.checkIn)}`,`DTEND;VALUE=DATE:${compact(s.checkOut)}`,`SUMMARY:Демо-поездка: ${cityById(s.city).name}`,`DESCRIPTION:Демонстрационный план ${b.id}. Реальных бронирований нет.`, 'BEGIN:VALARM','TRIGGER:-P1D','ACTION:DISPLAY','DESCRIPTION:Завтра ваша демо-поездка','END:VALARM','END:VEVENT','END:VCALENDAR'].join('\r\n');
 const url=URL.createObjectURL(new Blob([body],{type:'text/calendar;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=`${b.id}.ics`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);b.reminder=true;persist();bookingsModal();toast('Файл календаря скачан. Импортируйте его, чтобы включить напоминание.');
}
function memoryModal(){openModal('Приятно, когда вас помнят',`<p class="about-text">Предпочтения, история диалога, избранное и демо-заказы сохраняются локально в этом браузере.</p><div class="memory-pref">${tripSubtitle()}<br>${state.totalBudget?'До '+money(state.totalBudget)+' на всю поездку':'Бюджет ещё не указан'} · ${[state.breakfast?'завтрак':'',state.center?'в центре':'',state.pets?'с питомцем':'',state.spa?'спа':'',state.cancel?'бесплатная отмена':''].filter(Boolean).join(' · ')}</div><button class="secondary-button" data-action="clear-memory">Очистить локальную память</button>`);}
function runTests(){const now=new Date('2026-10-25T12:00:00');return TEST_CASES.map(t=>{const previous={...defaultState(now),breakfast:true,totalBudget:60000,known:{totalBudget:true}};const start=performance.now();try{const r=parseRequest(t.q,previous,now);return {q:t.q,ok:!!t.check(r.state,previous,r),ms:performance.now()-start};}catch(e){return {q:t.q,ok:false,ms:performance.now()-start,error:e.message};}});}
function metricsModal(){
 const passed=lastTests?.filter(t=>t.ok).length;const conn=connections(state);const parseAvg=metrics.parseTimes.length?metrics.parseTimes.reduce((a,b)=>a+b,0)/metrics.parseTimes.length:0;
 openModal('Качество, которое можно проверить',`<div class="metrics-grid"><div class="metric"><strong>${metrics.completed}/${metrics.started}</strong><small>Поездок с демо-оплатой / начатых</small></div><div class="metric"><strong>${metrics.clarifications}</strong><small>Уточнений за ${metrics.requests} запросов</small></div><div class="metric"><strong>${metrics.firstRecommendationMs===null?'—':metrics.firstRecommendationMs+' мс'}</strong><small>До первой рекомендации в сессии</small></div></div><div class="detail-section"><h3>Текущие стыковки</h3>${conn.map(c=>`<div class="test-row">${icon(c.ok?'check':'clock')}<div>${c.title} · ${c.ok?'проверено':'требует внимания'}</div></div>`).join('')}</div><p class="demo-data-note">Среднее время разбора: ${parseAvg.toFixed(1)} мс. Показатели вычисляются в браузере. Демо-каталог не измеряет качество поиска по реальному рынку.</p><div class="test-run"><button class="primary-button" data-action="run-tests">${icon('chart')}Запустить 12 тестовых запросов</button><small>${lastTests?`${passed} / ${lastTests.length} пройдено`:'Запросы, правки, даты, бюджет, питомцы'}</small></div>${lastTests?`<div class="test-results">${lastTests.map(t=>`<div class="test-row ${t.ok?'':'fail'}">${icon(t.ok?'check':'warning')}<div>${esc(t.q)}<br><small>${t.ok?'Пройден':'Ошибка'} · ${t.ms.toFixed(1)} мс</small></div></div>`).join('')}</div>`:''}`,'','Живые счётчики и воспроизводимые проверки');
}
function aboutModal(){if(CHAT_MODE==='api'){modeModal();return;}openModal('От «хочу» до готовой поездки',`<div class="about-text"><p>Это автономное демо по заданию хакатона: <b>чат — основной способ управлять поездкой</b>. Он разбирает типовые русскоязычные запросы без внешнего ИИ.</p><h3>Попробуйте в чате</h3><ul><li>«В Питер на выходные вдвоём до 50 тысяч на всю поездку»</li><li>«Казань с 6 по 9 ноября с завтраком»</li><li>«А если на два дня позже?»</li><li>«С нами едет собака»</li><li>«Бюджет на 20% меньше»</li><li>«До 60 тысяч на всю поездку»</li><li>«Не знаю, куда поехать»</li></ul><h3>Что уже работает</h3><p>Отели с объяснением и компромиссами, сравнение, выбор направления, поезд и самолёт, трансфер, общий план, проверка времени, демо-оплата и действия после покупки. Варианты появляются только после указания направления, общего бюджета, дат и числа гостей. До этого чат уточняет только недостающее. Параметры и чат синхронизированы; память сохраняется между сессиями.</p><h3>Границы демо</h3><p>Отели вымышлены. Цены, рейтинги, расписания и погода — сценарные данные. Свободное понимание любых формулировок и реальные сервисы бронирования не подключены. Отдельной отправки данных и платежей нет. Сторонний шрифт может загружаться с Google Fonts; фотографии — Unsplash.</p></div>`,`<button class="secondary-button" data-action="metrics">${icon('chart')}Проверить качество</button><button class="primary-button" data-action="close-modal">Начнём ${icon('arrow')}</button>`);}

async function send(text,{retryId=null}={}){
 text=text.trim();if(!text||busy)return;
 const generation=++requestGeneration;activeRequest=new AbortController();busy=true;
 const start=performance.now();let live=null,actionIntent=null;
 if(retryId)messages=messages.filter(m=>m.id!==retryId);
 else messages.push({id:messageId(),role:'user',text});
 const input=root.querySelector('#chat-input');if(input){input.value='';input.style.height='';input.focus({preventScroll:true});}
 unread=false;render({scroll:true,forceBottom:true});
 try{
  if(CHAT_MODE==='api'){
   await streamChat({message:text,endpoint:config.chatEndpoint||'/api/chat',signal:activeRequest.signal,onDelta:(_,all)=>{
    if(generation!==requestGeneration)return;
    if(!live){live={id:messageId(),role:'assistant',kind:'api',text:all};messages.push(live);render({scroll:true});}
    else{live.text=all;const area=root.querySelector('.chat-scroll'),near=area.scrollHeight-area.scrollTop-area.clientHeight<100;root.querySelector(`[data-message-id="${live.id}"] .assistant-text`).textContent=all;if(near)area.scrollTop=area.scrollHeight;else{unread=true;root.querySelector('.scroll-latest').classList.remove('hidden');}}
   }});
   if(generation!==requestGeneration)return;
   if(!live)messages.push({role:'assistant',kind:'api',text:'Агент завершил ответ без текста.'});
   connectionStatus='online';
  }else{
   await new Promise(resolve=>requestAnimationFrame(resolve));
   if(generation!==requestGeneration)return;
   const parseStart=performance.now();const result=parseRequest(text,state);metrics.parseTimes.push(performance.now()-parseStart);metrics.parseTimes=metrics.parseTimes.slice(-100);metrics.requests++;state=result.state;actionIntent=result.intent;
 let reply,kind='search';
 if(!recommendationsReady(state)){reply=requiredCriteriaQuestion(state);kind='clarify';metrics.clarifications++;}
 else if(result.inspiration){reply='Не обязательно сразу знать, куда. Вот три разных настроения для поездки: объяснил, чем хорош каждый вариант, добавил примерный бюджет и дорогу. Выберите то, что откликается.';kind='inspire';}
 else if(!state.known.city||!result.recognized){reply=!state.known.city?'Куда хочется поехать? Назовите город или напишите, что хочется от отдыха — помогу выбрать направление.':'Не совсем понял эту формулировку. Можно назвать даты, бюджет или новое пожелание — например, «с нами едет собака». Остальные условия сохранены.';kind='unknown';metrics.clarifications++;}
 else if((result.intent==='plan'||result.intent==='checkout')&&!state.known.checkIn){reply='На какие даты планируем поездку? От этого зависят отель, билеты и стыковки.';kind='clarify';metrics.clarifications++;}
 else if(result.intent==='plan'||result.intent==='checkout'){reply=selectedOrFirst()?'Всё в одном плане: отель, дорога туда и обратно, трансфер. Параметры сохраняются, а изменения в чате пересчитывают поездку.':'По этим условиям пока нет отеля. Измените бюджет или один из фильтров, и я соберу план.';kind=selectedOrFirst()?'plan':'search';}
 else if(result.changes.length){reply=`${result.changes.includes('даты')?'Даты сдвинулись — дорогу и трансфер перестроил вместе с отелем. ':''}${result.changes.includes('питомец')&&state.pets?'Четвероногий попутчик — это важно. Оставил только отели, где можно с питомцем; доплата 300 ₽ за ночь включена. ':''}${result.changes.includes('общий бюджет')?'Пересчитал отель, дорогу и трансфер под общий бюджет. ':''}Подобрал варианты в ${cityById(state.city).where} ${state.known.checkIn?'на '+dateRange():'без фиксированных дат'}. ${state.children?'Для семьи приоритет — тишина и удобная дорога. ':''}Остальные пожелания сохранены.${result.removedSelection?' Ранее выбранный отель больше не подходит — предложил замену.':''}`;}
 else reply=`Вот что подойдёт для поездки в ${cityById(state.city).where}. Учёл даты, бюджет и детали справа. У каждого варианта есть своё преимущество — и компромисс, о котором расскажу честно.`;
   messages.push({role:'assistant',kind,text:reply,changes:result.changes});
   if(metrics.firstRecommendationMs===null&&recommendationsReady(state)&&['search','inspire','plan'].includes(kind))metrics.firstRecommendationMs=Math.round(performance.now()-start);
  }
 }catch(error){
  if(generation!==requestGeneration)return;
  if(error.name==='AbortError'){
   if(!live)messages.push({role:'assistant',kind:CHAT_MODE==='api'?'api':'clarify',text:'Ответ остановлен. Можно продолжить другим сообщением.'});
  }else{
   const errorText=error.message||'Не получилось закончить ответ. Попробуйте ещё раз.';
   if(CHAT_MODE==='api')connectionStatus='offline';
   if(live){live.kind='error';live.errorText=errorText;live.retryText=text;}
   else messages.push({role:'assistant',kind:'error',text:errorText,retryText:text});
  }
 }finally{
  if(generation===requestGeneration){busy=false;activeRequest=null;persist();render({scroll:true});}
 }
 if(generation===requestGeneration&&CHAT_MODE==='demo'&&recommendationsReady(state)){if(actionIntent==='compare')compareModal();else if(actionIntent==='checkout'&&selectedOrFirst())checkoutModal();}
}
function cancelRequest(){activeRequest?.abort();}
async function refreshConnection(){
 if(CHAT_MODE!=='api')return;
 try{const health=await checkAgentHealth({endpoint:config.healthEndpoint||'/health',signal:AbortSignal.timeout(5000)});agentProvider=health.llm_provider;connectionStatus='online';}
 catch{connectionStatus='offline';}
 render();
}
function modeModal(){
 openModal('О!дин запрос',`<p class="about-text">Выберите, с кем продолжить разговор. История каждого режима сохраняется отдельно.</p><div class="mode-switch"><button class="${CHAT_MODE==='demo'?'on':''}" data-action="set-mode" data-mode="demo"><b>Демонстрация</b><small>Подбор поездки без ключей. Все предложения учебные.</small></button><button class="${CHAT_MODE==='api'?'on':''}" data-action="set-mode" data-mode="api" ${config.apiEnabled?'':'disabled'}><b>Агент проекта</b><small>${config.apiEnabled?'Ответы сервера na-maldivy в реальном времени.':'Доступен при запуске интерфейса из репозитория.'}</small></button></div>${CHAT_MODE==='api'?`<div class="demo-note">${connectionStatus==='offline'?'Сервер недоступен. Убедитесь, что сервис agent запущен.':agentProvider==='fake'?'Демо-агент работает по правилам. Поиск, карточки и оформление приходят с сервера.':'Подключён сервер репозитория. Интерфейс показывает только фактические ответы API.'}</div><button class="secondary-button" data-action="check-connection">${icon('refresh')}Проверить подключение</button>`:''}`,'','Островок · команда «На Мальдивы»');
}
function validateSelection(){if(state.selectedHotel&&!searchHotels(state).some(h=>h.id===state.selectedHotel)){state.selectedHotel=null;toast('Выбранный отель не подходит к новым условиям. Подобрали замену.');}}
function updateParameters(label,{message=true}={}){
 const opts=transportOptions(state);if(!opts.some(t=>t.id===state.transport))state.transport=opts[0].id;
 validateSelection();state.searched=true;
 if(message){const ready=recommendationsReady(state);messages.push({role:'assistant',kind:ready?'search':'clarify',text:ready?`Обновил ${label}. Отели, стоимость дороги и общий план пересчитаны — остальные пожелания сохранил.`:requiredCriteriaQuestion(state),changes:[label]});if(!ready)metrics.clarifications++;}
 comparison=[];persist();render({scroll:message});
}
function openFilters(){const s=root.querySelector('.sidebar');s.classList.add('open');root.querySelector('.drawer-backdrop').classList.add('open');s.setAttribute('role','dialog');s.setAttribute('aria-modal','true');s.querySelector('button')?.focus();}
function closeFilters(){root.querySelector('.sidebar').classList.remove('open');root.querySelector('.drawer-backdrop').classList.remove('open');root.querySelector('.sidebar').removeAttribute('role');root.querySelector('.sidebar').removeAttribute('aria-modal');root.querySelector('.mobile-filters')?.focus();}
function newTrip(){requestGeneration++;activeRequest?.abort();activeRequest=null;busy=false;unread=false;const input=root.querySelector('#chat-input');if(input)input.value='';state={...defaultState(),budget:state.budget,breakfast:state.breakfast,cancel:state.cancel,pets:state.pets,quiet:state.quiet};messages=[];comparison=[];metrics.started++;metrics.firstRecommendationMs=null;persist();closeModal();render();toast(CHAT_MODE==='api'?'Новый разговор с агентом.':'Новая поездка. Ваши предпочтения сохранены.');}

document.addEventListener('click',e=>{
 if(CHAT_MODE==='api')return;
 const button=e.target.closest('[data-action],[data-prompt]');if(!button||button.disabled)return;
 if(button.dataset.prompt){closeModal();send(button.dataset.prompt);return;}
 const a=button.dataset.action;const id=button.dataset.id;const feature=button.dataset.feature;
 switch(a){
 case 'close-modal':closeModal();break;
 case 'mode':modeModal();break;
 case 'set-mode':if(button.dataset.mode!==CHAT_MODE){try{localStorage.setItem('na-maldivy-mode',button.dataset.mode);persist();location.reload();}catch{toast('Не удалось сохранить режим в этом браузере.');}}else closeModal();break;
 case 'check-connection':closeModal();connectionStatus='connecting';render();refreshConnection();break;
 case 'stop':cancelRequest();break;
 case 'retry':{const m=messages.find(x=>x.id===id);if(m?.retryText)send(m.retryText,{retryId:id});break;}
 case 'scroll-latest':{unread=false;const area=root.querySelector('.chat-scroll');area.scrollTo({top:area.scrollHeight,behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});button.classList.add('hidden');break;}
 case 'filters':openFilters();break;case 'close-filters':closeFilters();break;
 case 'home':closeModal();root.querySelector('.chat-scroll').scrollTo({top:0,behavior:'smooth'});break;
 case 'new':newTrip();break;
 case 'hotel':hotelModal(id);break;
 case 'favorite':favorites=favorites.includes(id)?favorites.filter(x=>x!==id):[...favorites,id];persist();render();if(modal.open&&modal.querySelector('.modal-head h2')?.textContent==='Места, которые запомнились')favoritesModal();else if(modal.open&&modal.querySelector('.heart'))favoritesModal();toast(favorites.includes(id)?'Сохранено. Вернёмся к этому месту.':'Отель убран из избранного.');break;
 case 'favorites':favoritesModal();break;
 case 'select-hotel':selectHotel(id);break;
 case 'plan':planModal();break;case 'checkout':checkoutModal();break;
 case 'transport-modal':transportModal();break;
 case 'feature':state.known[feature]=true;state[feature]=!state[feature];updateParameters(button.textContent.trim().toLowerCase());break;
 case 'upgrade':{
  if(feature==='inspiration'){send('Не знаю, куда поехать');break;}
  const before=selectedOrFirst();const oldTotal=before?planTotal(state,before):0;
  state.known[feature]=true;state[feature]=true;
  const options=searchHotels({...state,totalBudget:null,budget:100000});
  const after=feature==='spa'?options.sort((a,b)=>a.price-b.price)[0]:options.find(h=>h.id===before?.id)||options[0];
  const delta=after?Math.max(0,planTotal(state,after)-oldTotal):0;
  if(state.totalBudget)state.totalBudget+=delta;state.budget=100000;
  updateParameters(feature==='spa'?'отдых со спа':feature==='breakfast'?'завтрак':'ранний заезд');
  toast(after?(delta?'Общий бюджет увеличен на '+money(delta)+'.':'Улучшение добавлено без доплаты.'):'Нет подходящего отеля с этим пожеланием.');break;
 }
 case 'transport':state.known.transport=true;state.transport=button.dataset.transport;updateParameters('транспорт');break;
 case 'choose-transport':{const wasPlan=modal.querySelector('.modal-head h2')?.textContent==='Всё складывается';state.transport=button.dataset.transport;persist();render();wasPlan?planModal():transportModal();break;}
 case 'transfer':{const wasPlan=modal.querySelector('.modal-head h2')?.textContent==='Всё складывается';state.transfer=!state.transfer;persist();render();wasPlan?planModal():transportModal();break;}
 case 'early-plan':state.earlyCheckIn=true;validateSelection();persist();render();planModal();break;
 case 'direction':state.city=button.dataset.city;state.known.city=true;state.seaside=state.city==='sochi'&&state.seaside;state.selectedHotel=null;send(`Подбери отели в ${cityById(state.city).name}`);break;
 case 'raise-budget':{const base=state.totalBudget||(selectedOrFirst()?planTotal(state,selectedOrFirst()):30000);state.totalBudget=base+5000;state.budget=100000;state.known.totalBudget=true;updateParameters('общий бюджет');break;}
 case 'budget-increase':{const base=state.totalBudget||30000;state.totalBudget=base+Math.max(1000,Math.round(base*Number(button.dataset.percent)/100/1000)*1000);state.budget=100000;state.known.totalBudget=true;updateParameters('общий бюджет');break;}
 case 'compare-add':if(comparison.includes(id))comparison=comparison.filter(x=>x!==id);else if(comparison.length<3)comparison.push(id);else toast('Сравним до трёх отелей. Уберите один, чтобы добавить новый.');render();break;
 case 'compare-open':case 'compare-results':compareModal();break;case 'compare-clear':comparison=[];render();break;
 case 'guests':guestsModal();break;
 case 'guest-step':state[button.dataset.field]=Math.max(button.dataset.field==='adults'?1:0,Math.min(button.dataset.field==='adults'?6:4,state[button.dataset.field]+Number(button.dataset.delta)));guestsModal();break;
 case 'guest-pet':state.pets=!state.pets;guestsModal();break;
 case 'guests-done':state.known.adults=true;if(state.children)state.known.children=true;if(state.pets)state.known.pets=true;closeModal();updateParameters('состав гостей');break;
 case 'bookings':bookingsModal();break;
 case 'booking-change':bookingDates(id);break;
 case 'booking-cancel':cancelBookingModal(id);break;
 case 'booking-cancel-confirm':confirmBookingCancellation(id);break;
 case 'booking-early':{const b=editableBooking(id);if(!b)break;if(b.early){toast('Ранний заезд уже добавлен.');break;}b.early=true;b.state.earlyCheckIn=true;b.total=planTotal(b.state,getHotel(b.hotel,b.state));persist();bookingsModal();toast('Демо-запрос на ранний заезд подтверждён. Стоимость пересчитана.');break;}
 case 'booking-transfer':{const b=editableBooking(id);if(!b)break;if(b.state.transfer){toast('Трансфер уже включён в эту поездку.');break;}b.state.transfer=true;b.total=planTotal(b.state,getHotel(b.hotel,b.state));persist();bookingsModal();toast('Трансфер добавлен на даты поездки.');break;}
 case 'booking-reminder':{const b=editableBooking(id);if(b)downloadICS(b);break;}
 case 'metrics':metricsModal();break;case 'run-tests':lastTests=runTests();metricsModal();break;
 case 'about':aboutModal();break;case 'memory':CHAT_MODE==='api'?modeModal():memoryModal();break;
 case 'clear-memory':requestGeneration++;activeRequest?.abort();activeRequest=null;busy=false;state=defaultState();messages=[];favorites=[];bookings=[];comparison=[];metrics={requests:0,clarifications:0,completed:0,started:1,parseTimes:[],firstRecommendationMs:null};try{localStorage.removeItem(key);}catch{}closeModal();render();toast('Память этого демо очищена.');break;
 }
});
document.addEventListener('submit',e=>{
 if(CHAT_MODE==='api')return;
 if(e.target.id==='chat-form'){e.preventDefault();const input=document.getElementById('chat-input');send(input.value);}
 if(e.target.id==='checkout-form'){e.preventDefault();completeBooking(e.target);}
 if(e.target.id==='booking-dates'){e.preventDefault();const form=e.target;const b=editableBooking(form.dataset.id);if(!b)return;const data=new FormData(form);const start=String(data.get('checkIn')),end=String(data.get('checkOut'));if(start>=end){toast('Дата выезда должна быть позже даты заезда.');return;}b.state.checkIn=start;b.state.checkOut=end;b.total=planTotal(b.state,getHotel(b.hotel,b.state));persist();bookingsModal();toast('Даты отеля, дороги и трансфера изменены вместе.');}
});
document.addEventListener('keydown',e=>{
 if(CHAT_MODE==='api')return;
 if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();root.querySelector('#chat-input')?.focus();return;}
 if(e.target.id==='chat-input'&&e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();send(e.target.value);}
 if(e.key==='Escape')closeFilters();
 if(e.key==='Tab'&&root.querySelector('.sidebar.open')){const side=root.querySelector('.sidebar');const focusable=[...side.querySelectorAll('button:not(:disabled),select,input')];const first=focusable[0],last=focusable.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}
});
document.addEventListener('input',e=>{
 if(CHAT_MODE==='api')return;
 if(e.target.id==='chat-input'){e.target.style.height='28px';e.target.style.height=Math.min(100,e.target.scrollHeight)+'px';}
 if(e.target.dataset.field==='totalBudget'){const v=Number(e.target.value);document.getElementById('budget-value').textContent=money(v);e.target.style.setProperty('--progress',(v-Number(e.target.min))/(Number(e.target.max)-Number(e.target.min))*100+'%');e.target.setAttribute('aria-valuetext',money(v)+' на всю поездку');}
 if(e.target.form?.id==='booking-dates'&&e.target.name==='checkIn'){e.target.form.elements.checkOut.min=addDays(e.target.value,1);}
});
document.addEventListener('change',e=>{
 if(CHAT_MODE==='api')return;
 const f=e.target.dataset.field;if(!f||e.target.dataset.action)return;state.known[f]=true;
 if(f==='totalBudget'){state.totalBudget=Number(e.target.value);state.budget=100000;}
 else if(f==='checkIn'){const duration=nights(state);if(!e.target.value||e.target.value<iso(new Date())){toast('Выберите будущую дату заезда.');render();return;}state.checkIn=e.target.value;state.checkOut=addDays(state.checkIn,duration);state.known.checkOut=!!(state.known.checkOut||state.known.duration);}
 else if(f==='checkOut'){if(!e.target.value||e.target.value<=state.checkIn){toast('Дата выезда должна быть позже заезда.');render();return;}state.checkOut=e.target.value;}
 else if(f==='city'){state.city=e.target.value;state.selectedHotel=null;comparison=[];}
 else state[f]=e.target.value;
 updateParameters(({totalBudget:'общий бюджет',checkIn:'даты',checkOut:'даты',city:'направление',origin:'город отправления',sort:'порядок вариантов'})[f]||'параметры',{message:f!=='sort'});
});
modal.addEventListener('click',e=>{if(CHAT_MODE==='api')return;if(e.target===modal){const r=modal.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)closeModal();}});
if(CHAT_MODE==='api')mountApiApp({root,modal,icon,config});
else{render();refreshConnection();}
