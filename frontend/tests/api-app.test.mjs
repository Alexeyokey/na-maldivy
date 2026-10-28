import test from 'node:test';
import assert from 'node:assert/strict';
import {mountApiApp} from '../dist/api-app.js';

const sid='durable-ui-session';
const order={order_id:'DEMO-durable-ui',cart_id:'CART-durable-ui',session_id:sid,status:'paid_demo',is_simulated:true,package:{hotel:{name:'Durable Demo Hotel',check_in:'2026-11-18',check_out:'2026-11-25'},known_total_rub:80000}};
const until=async predicate=>{
 const deadline=Date.now()+2000;
 while(!predicate()){
  assert.ok(Date.now()<deadline,'The mounted app did not finish restoring its durable order');
  await new Promise(resolve=>setImmediate(resolve));
 }
};

test('The mounted chat keeps its paid-order sidebar and document dialog after the in-memory session expires',async t=>{
 let stored=JSON.stringify({schemaVersion:2,sessionId:sid,version:1,stage:'booked',currentOrderId:order.order_id,orders:[{sessionId:sid,orderId:order.order_id}],messages:[{role:'user',text:'**Подтверждаю** <img src=x onerror=alert(1)>'},{role:'assistant',text:'Сравним варианты.\n\n1. **JEN** — поездка [review_id:rv_001]\n\n2. **Villa Nautica** — второй вариант.\n\nУсловия нужно уточнить.'}]});
 const storage={getItem:()=>stored,setItem:(_,value)=>stored=value},calls=[];
 const document=Object.assign(new EventTarget(),{visibilityState:'visible',activeElement:null});
 const window=new EventTarget(),input={value:'',selectionStart:0,selectionEnd:0};
 const area={scrollTop:0,scrollHeight:100,clientHeight:100},sidebar={classList:{contains:()=>false}};
 const root=Object.assign(new EventTarget(),{innerHTML:'',querySelector:selector=>({'#api-chat-input':input,'.chat-scroll':area,'.sidebar':sidebar}[selector]||null)});
 const modal=Object.assign(new EventTarget(),{innerHTML:'',open:false,setAttribute(){},showModal(){this.open=true;},close(){this.open=false;}});
 const globals={document,window,sessionStorage:storage,fetch:async(path,init={})=>{
  calls.push({path,method:init.method||'GET'});
  if(path==='/health')return Response.json({status:'ok',llm_provider:'fake'});
  if(path==='/api/session/'+sid)return Response.json({detail:'Expired session'},{status:404});
  if(path==='/api/checkout/orders/'+order.order_id)return Response.json(order);
  throw new Error('Unexpected route');
 }};
 const original=Object.fromEntries(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const [key,value] of Object.entries(globals))Object.defineProperty(globalThis,key,{value,writable:true,configurable:true});
 let controller;
 t.after(()=>{
  controller?.dispose();
  for(const [key,descriptor] of Object.entries(original))if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];
 });
 const click=(action,extra={})=>{
  const target={dataset:{apiAction:action,...extra},closest(){return this;}};
  const event=new Event('click');Object.defineProperty(event,'target',{value:target});document.dispatchEvent(event);
 };
 controller=mountApiApp({root,modal,icon:()=>'',config:{}});
 await until(()=>controller.state.sessionLost&&controller.state.order&&controller.state.connection==='online');
 assert.match(root.innerHTML,/<strong>JEN<\/strong>/);
 assert.match(root.innerHTML,/<ol\b/);assert.match(root.innerHTML,/<strong>Villa Nautica<\/strong>/);
 assert.match(root.innerHTML,/\*\*Подтверждаю\*\* &lt;img src=x onerror=alert\(1\)&gt;/,'User input remains literal escaped text');
 assert.doesNotMatch(root.innerHTML,/rv_001|<img src=x/,'Formatting cannot expose source IDs or execute message HTML');
 assert.match(root.innerHTML,/Демо-заказ и документы/);
 assert.match(root.innerHTML,/Демо-заказ и документы доступны/);
 const documentsButton=root.innerHTML.match(/<button[^>]*data-api-action="success"[^>]*>/)?.[0];
 assert.ok(documentsButton);assert.doesNotMatch(documentsButton,/disabled/);
 assert.doesNotMatch(root.innerHTML,/Восстановить этот ответ уже нельзя/);
 click('success');
 assert.equal(modal.open,true);assert.match(modal.innerHTML,/Скачать демо-ваучер/);assert.match(modal.innerHTML,/Скачать календарь \.ics/);
 assert.doesNotMatch(modal.innerHTML,/api-checkout-form/);
 click('restart-session');await until(()=>controller.state.connection==='online');
 assert.equal(controller.state.sessionId,null);assert.equal(controller.state.order,null);assert.equal(controller.state.orders.length,1);
 click('orders');assert.match(modal.innerHTML,/Открыть документы/);
 click('order',{orderIndex:'0'});assert.match(modal.innerHTML,/Скачать демо-ваучер/);
 assert.ok(calls.every(call=>call.method==='GET'),'Document restoration cannot create another payment or chat turn');
});

const packageOption=index=>({
 index,kind:'package',destination:'MLE',can_confirm:true,known_total_rub:66000+index,
 travelers:{adults:1,children_ages:[],pets:0},
 hotel:{name:'Server Hotel '+index,stars:4,rating:9.1,refundable:index===1?true:index===2?false:null,check_in:'2027-01-29',check_out:'2027-02-05',nights:7,price_rub:45000},
 outbound:{from:'MOW',to:'MLE',departure_at:'2027-01-29T12:00:00+03:00',arrival_at:'2027-01-29T23:00:00+05:00',price_rub:10000,baggage_price_rub:0,demo_source:{kind:'deterministic_mock'}},
 inbound:{from:'MLE',to:'MOW',departure_at:'2027-02-05T12:00:00+05:00',arrival_at:'2027-02-05T23:00:00+03:00',price_rub:11000,baggage_price_rub:null,demo_source:{kind:'deterministic_mock'}},
 transfer:index===3?null:{mode:'boat',price_rub:index,duration_minutes:34,available:true},
 unpriced_components:['return_transfer','visa_and_other_required_extras'],issues:[],risks:['Расписание сгенерировано. Наличие не подтверждено.'],
});

async function mountedSearch(t,options={}){
 const calls=[],storage={getItem:()=>null,setItem(){}};
 const document=Object.assign(new EventTarget(),{visibilityState:'visible',activeElement:null});
 const window=new EventTarget(),input={value:'',selectionStart:0,selectionEnd:0};
 const area={scrollTop:0,scrollHeight:100,clientHeight:100},sidebar={classList:{contains:()=>false}};
 const root=Object.assign(new EventTarget(),{innerHTML:'',querySelector:selector=>({'#api-chat-input':input,'.chat-scroll':area,'.sidebar':sidebar}[selector]||null)});
 const modalStatus={textContent:'',scrollIntoView(){}},modalClose={isConnected:true,focus(){document.activeElement=this;}};
 const modal=Object.assign(new EventTarget(),{innerHTML:'',open:false,setAttribute(){},showModal(){this.open=true;},close(){this.open=false;},querySelector:selector=>selector==='#api-modal-status'?modalStatus:selector==='.close-modal'?modalClose:null});
 const globals={document,window,sessionStorage:storage,fetch:async(path,init={})=>{
  calls.push({path,method:init.method||'GET',body:init.body});
  if(path==='/health')return Response.json({status:'ok',llm_provider:'fake'});
  throw new Error('Unexpected route in mounted presentation test: '+path);
 }};
 const original=Object.fromEntries(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const [key,value] of Object.entries(globals))Object.defineProperty(globalThis,key,{value,writable:true,configurable:true});
 let controller;
 t.after(()=>{controller?.dispose();for(const [key,descriptor] of Object.entries(original))if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];});
 const click=(action,extra={})=>{
  const target={dataset:{apiAction:action,...extra},closest(){return this;}};
  const event=new Event('click');Object.defineProperty(event,'target',{value:target});document.dispatchEvent(event);
 };
 controller=mountApiApp({root,modal,icon:()=>'',config:{},...options});
 await until(()=>controller.state.connection==='online');
 Object.assign(controller.state,{sessionId:'search-ui-session',stage:'packages_shown',optionKind:'packages',version:1,brief:{origin:'MOW',destination:'MLE',month:1,year:2027,adults:1,nights:null,start_date:null},sources:{month:'explicit'},assumptions:[{field:'nights',text:'беру 7 ночей'},{field:'start_date',text:'минимальная цена календаря в одну сторону; не экономия всей поездки'}],options:[1,2,3].map(packageOption),messages:[{role:'assistant',text:'Подобрали текущие варианты.'}]});
 controller.notify();
 return {root,modal,modalStatus,modalClose,controller,calls,click,document};
}

test('Hotel and rail captions include every child in the server-priced party, including infants',async t=>{
 const {root,controller}=await mountedSearch(t);
 Object.assign(controller.state.brief,{adults:2,children_ages:[0],pets:0});controller.notify();
 assert.match(root.innerHTML,/3 варианта с сервера · 2 взрослых · 1 ребёнок \(0 лет\)/);
 const amounts=controller.state.options.map(option=>option.known_total_rub);
 assert.deepEqual(amounts,[66001,66002,66003],'Party captions do not calculate or replace server prices');
 Object.assign(controller.state,{stage:'trains_shown',optionKind:'trains',options:[{index:1,kind:'train',number:'DEMO-NIGHT',seat_class:'coupe',origin:{iata:'MOW',name:'Москва'},destination:{iata:'LED',name:'Петербург'},departure_at:'2027-01-29T23:55:00+03:00',arrival_at:'2027-01-30T07:55:00+03:00',price_rub:5044,is_synthetic:true}]});
 controller.state.brief.transport='train';controller.notify();
 assert.match(root.innerHTML,/1 вариант с сервера · 2 взрослых · 1 ребёнок \(0 лет\)/);
 assert.equal(controller.state.options[0].price_rub,5044);
});

test('Declined payment returns keyboard focus to its submitter and success replacement focuses the dialog close control',async t=>{
 const {root,modal,modalClose,controller,click,document}=await mountedSearch(t);
 document.body={isConnected:true,focus(){document.activeElement=this;}};
 const input=root.querySelector('#api-chat-input');input.focus=()=>{document.activeElement=input;};
 document.activeElement=document.body;
 const option=packageOption(1);Object.assign(controller.state,{stage:'checkout_pending',cart:{cart_id:'CART-focus',expected_travelers:{adults:1,children_ages:[]},package:option}});
 click('checkout');
 const decline={dataset:{outcome:'declined'},disabled:false,isConnected:true,focus(){document.activeElement=this;}},approve={...decline,dataset:{outcome:'approved'}};
 const status={textContent:''},form={id:'api-checkout-form',dataset:{count:'1'},isConnected:true,reportValidity:()=>true,querySelectorAll:()=>[decline,approve],querySelector:()=>status};
 modal.contains=element=>element===form&&form.isConnected;
 let html=modal.innerHTML;Object.defineProperty(modal,'innerHTML',{configurable:true,get:()=>html,set:value=>{html=value;if(!value.includes('api-checkout-form'))form.isConnected=false;}});
 const original=globalThis.FormData;globalThis.FormData=class {get(key){return {'name-0':'Demo Adult','birth-0':'1990-01-01','ack-demo':'on','ack-extras':'on'}[key];}};
 t.after(()=>{globalThis.FormData=original;});
 let finish;controller.pay=async()=>{document.activeElement=document.body;return await new Promise(resolve=>finish=resolve);};
 const submit=submitter=>{const event=new Event('submit',{cancelable:true});Object.defineProperties(event,{target:{value:form},submitter:{value:submitter}});document.dispatchEvent(event);};
 decline.focus();submit(decline);assert.equal(decline.disabled,true);finish({status:'payment_declined'});
 await until(()=>decline.disabled===false);assert.equal(document.activeElement,decline);
 approve.focus();submit(approve);controller.state.order=order;controller.state.orders=[{sessionId:sid,order}];finish({status:'paid_demo'});
 await until(()=>!form.isConnected);assert.equal(modal.open,true);assert.equal(document.activeElement,modalClose);
 modal.open=false;modal.dispatchEvent(new Event('close'));
 assert.equal(document.activeElement,input,'A body or disconnected checkout opener cannot strand keyboard focus after Escape');
});

test('A late declined payment cannot restore focus to a checkout replaced by another dialog',async t=>{
 const {modal,modalClose,controller,click,document}=await mountedSearch(t);
 Object.assign(controller.state,{stage:'checkout_pending',cart:{cart_id:'CART-late-focus',expected_travelers:{adults:1,children_ages:[]},package:packageOption(1)}});click('checkout');
 const submitter={dataset:{outcome:'declined'},disabled:false,isConnected:true,focus(){document.activeElement=this;}},form={id:'api-checkout-form',dataset:{count:'1'},isConnected:true,reportValidity:()=>true,querySelectorAll:()=>[submitter],querySelector:()=>({textContent:''})};
 modal.contains=element=>element===form&&form.isConnected;
 const original=globalThis.FormData;globalThis.FormData=class {get(key){return {'name-0':'Demo Adult','birth-0':'1990-01-01','ack-demo':'on','ack-extras':'on'}[key];}};t.after(()=>{globalThis.FormData=original;});
 let finish;controller.pay=async()=>await new Promise(resolve=>finish=resolve);
 const event=new Event('submit',{cancelable:true});Object.defineProperties(event,{target:{value:form},submitter:{value:submitter}});document.dispatchEvent(event);
 form.isConnected=false;submitter.isConnected=false;click('about');
 assert.equal(document.activeElement,modalClose);finish({status:'payment_declined'});await new Promise(resolve=>setImmediate(resolve));
 assert.equal(document.activeElement,modalClose);assert.match(modal.innerHTML,/Как устроена демонстрация/);
});

test('Mounted checkout supplies an already-born infant date for January and explains an impossible future age',async t=>{
 const {modal,controller,click}=await mountedSearch(t,{now:()=>new Date('2026-10-29T12:00:00Z')});
 const option=packageOption(1);option.outbound.departure_at='2027-01-18T12:00:00+03:00';
 Object.assign(controller.state,{stage:'checkout_pending',cart:{cart_id:'CART-child-date',expected_travelers:{adults:1,children_ages:[0]},package:option}});
 click('checkout');
 assert.match(modal.innerHTML,/name="birth-1" type="date" value="2026-10-29" min="2026-01-19" max="2026-10-29" required/);
 assert.doesNotMatch(modal.innerHTML,/value="2027-01-01"/);
 option.outbound.departure_at='2028-01-18T12:00:00+03:00';click('checkout');
 assert.match(modal.innerHTML,/name="birth-1" type="date" value="" max="2026-10-29" aria-describedby="api-birth-note-1" required/);
 assert.match(modal.innerHTML,/Уже родившийся ребёнок не может иметь указанный возраст/);
});

test('Document download progress, success and failure remain inside the open accessible dialog',async t=>{
 const {modal,modalStatus,controller,click,document}=await mountedSearch(t);
 const saved={sessionId:'search-ui-session',order:{...order,session_id:'search-ui-session'}};
 controller.state.orders=[saved];controller.state.order=saved.order;
 click('success');assert.equal(modal.open,true);
 assert.match(modal.innerHTML,/id="api-modal-status"[^>]*role="status"[^>]*aria-live="polite"/);
 let release,linkClicked=false;const result=new Promise(resolve=>release=resolve);
 controller.download=async(kind,entry)=>{assert.equal(kind,'voucher');assert.equal(entry,saved);return result;};
 document.createElement=()=>({click(){linkClicked=true;},remove(){}});document.body={append(){}};
 click('download-voucher',{orderIndex:'0'});assert.equal(modalStatus.textContent,'Готовим демо-ваучер…');
 release({blob:new Blob(['demo']),filename:'demo-voucher.html'});
 await until(()=>modalStatus.textContent==='Демо-ваучер скачан.');assert.equal(linkClicked,true);assert.equal(modal.open,true);
 controller.download=async()=>{throw new Error('Документ временно недоступен.');};
 click('download-calendar',{orderIndex:'0'});
 await until(()=>modalStatus.textContent==='Документ временно недоступен.');assert.equal(modal.open,true);
});

test('Comparison rerenders retain keyboard focus and mobile horizontal positions for the same current results',async t=>{
 const {root,controller,click,document}=await mountedSearch(t);
 click('compare',{index:'1'});click('compare',{index:'2'});
 const originalQuery=root.querySelector,input=originalQuery('#api-chat-input');
 input.focus=()=>{document.activeElement=input;};input.setSelectionRange=()=>{};
 let html=root.innerHTML,grid={scrollLeft:580},table={scrollLeft:170},area={scrollTop:250,scrollHeight:1000,clientHeight:100};
 const makeButton=()=>({dataset:{apiAction:'compare',index:'3'},disabled:false,isConnected:true,classList:{contains:()=>false},closest:selector=>selector==='.api-results'?{}:null,focus(){document.activeElement=this;}});
 let button=makeButton();document.activeElement=button;const oldButton=button;
 root.contains=element=>element===button||element===input;
 root.querySelector=selector=>({'.hotel-grid':grid,'.api-comparison-scroll':table,'.chat-scroll':area}[selector]||originalQuery(selector));
 root.querySelectorAll=selector=>selector.includes('[data-api-action]')&&button?[button]:[];
 Object.defineProperty(root,'innerHTML',{configurable:true,get:()=>html,set:value=>{
  html=value;if(button){button.isConnected=false;if(document.activeElement===button)document.activeElement=null;}
  button=value.includes('data-api-action="compare"')?makeButton():null;
  grid={scrollLeft:0};table={scrollLeft:0};area={scrollTop:0,scrollHeight:1000,clientHeight:100};
 }});
 click('compare',{index:'3'});
 assert.notEqual(button,oldButton);assert.equal(document.activeElement,button);
 assert.match(root.innerHTML,/scope="col">Вариант 3/);
 assert.equal(grid.scrollLeft,580);assert.equal(table.scrollLeft,170);assert.equal(area.scrollTop,250);
 controller.state.stale=true;controller.state.brief.destination='DXB';controller.notify();
 assert.equal(grid.scrollLeft,0,'A replacement search starts at the beginning instead of reviving the preceding carousel position');
 assert.equal(document.activeElement,input,'A removed result control falls back to the chat instead of losing all keyboard focus');
});

test('Unloaded order references remain distinguishable from an empty history and can retry after session expiry',async t=>{
 const {root,modal,modalStatus,controller,click}=await mountedSearch(t);
 Object.assign(controller.state,{sessionLost:true,ordersSyncNeeded:true,orders:[]});controller.notify();
 assert.match(root.innerHTML,/<button class="text-icon"[^>]*aria-label="Мои поездки"/);
 const reconnectButton=root.innerHTML.match(/<button[^>]*data-api-action="reconnect"[^>]*>/)?.[0];
 assert.ok(reconnectButton);assert.doesNotMatch(reconnectButton,/disabled/);
 click('orders');assert.match(modal.innerHTML,/Не все сохранённые поездки загружены/);
 assert.doesNotMatch(modal.innerHTML,/Сохранённых демо-заказов пока нет/);
 const known={sessionId:sid,order};controller.state.orders=[known];click('orders');
 assert.match(modal.innerHTML,/Durable Demo Hotel/);assert.match(modal.innerHTML,/Открыть документы/);
 let release,attempts=0;const waiting=new Promise(resolve=>release=resolve);
 controller.reconnect=async()=>{attempts++;await waiting;controller.state.ordersSyncNeeded=false;controller.notify();};
 click('reload-orders');assert.equal(attempts,1);assert.equal(modalStatus.textContent,'Загружаем сохранённые поездки…');
 release();await until(()=>!modal.innerHTML.includes('Не все сохранённые поездки загружены'));
 assert.equal(modal.open,true);assert.match(modal.innerHTML,/Durable Demo Hotel/);assert.equal(controller.state.orders[0],known);
 assert.equal(controller.state.sessionLost,true,'History recovery does not fabricate a new search session');
});

test('Mobile trip parameters use the native modal and restore the stable trigger after native Escape close',async t=>{
 const {root,modal,controller,click,document}=await mountedSearch(t);
 const trigger=()=>({dataset:{apiAction:'filters'},isConnected:true,classList:{contains:()=>false},closest:selector=>selector==='.topbar'?{}:null,focus(){document.activeElement=this;}});
 let currentTrigger=trigger(),nativeOpens=0;
 const closeControl={focus(){document.activeElement=this;}};
 root.contains=element=>element===currentTrigger;
 root.querySelectorAll=selector=>selector.includes('[data-api-action]')?[currentTrigger]:[];
 modal.showModal=function(){this.open=true;nativeOpens++;closeControl.focus();};
 modal.close=function(){this.open=false;this.dispatchEvent(new Event('close'));};
 currentTrigger.focus();click('filters');
 assert.equal(nativeOpens,1);assert.equal(modal.open,true);assert.equal(document.activeElement,closeControl);
 assert.match(modal.innerHTML,/<h2 id="api-modal-title">Ваша поездка<\/h2>/);
 assert.match(modal.innerHTML,/Москва → Мальдивы/);assert.match(modal.innerHTML,/Изменить условия/);
 assert.doesNotMatch(modal.innerHTML,/drawer-close|data-api-action="close-filters"/);
 assert.equal((modal.innerHTML.match(/<h2\b/g)||[]).length,1);
 currentTrigger.isConnected=false;currentTrigger=trigger();controller.notify();
 assert.equal(modal.open,true);assert.equal(document.activeElement,closeControl);
 const cancel=new Event('cancel',{cancelable:true});modal.dispatchEvent(cancel);
 assert.equal(cancel.defaultPrevented,false,'Escape is handled by the native dialog rather than blocked by a custom drawer');
 modal.close();assert.equal(modal.open,false);assert.equal(document.activeElement,currentTrigger);
 assert.match(root.innerHTML,/<aside class="sidebar " aria-label="Параметры поездки">/,'Desktop keeps its persistent sidebar');
});

test('Preference dates have a tomorrow constraint and reject past dates without sending a chat turn',async t=>{
 const {modal,modalStatus,controller,click,document}=await mountedSearch(t,{now:()=>new Date('2026-10-29T12:00:00Z')});
 click('edit-preferences');
 assert.match(modal.innerHTML,/name="start_date" type="date" min="2026-10-30"/);
 assert.match(modal.innerHTML,/Детей, питомцев и бюджет можно изменить в чате/);
 assert.match(modal.innerHTML,/ребёнок 5 лет и собака, бюджет 200 тысяч рублей/);
 const sent=[];controller.send=async text=>{sent.push(text);};let date='2026-10-29';
 const original=globalThis.FormData;globalThis.FormData=class {get(key){return {adults:'1',nights:'7',start_date:date}[key];}};
 t.after(()=>{globalThis.FormData=original;});
 const submit=()=>{const event=new Event('submit',{cancelable:true});Object.defineProperty(event,'target',{value:{id:'api-preferences-form',dataset:{transport:'flight'},reportValidity:()=>true}});document.dispatchEvent(event);};
 submit();assert.equal(sent.length,0);assert.equal(modal.open,true);assert.equal(modalStatus.textContent,'Выберите дату не ранее 30.10.2026.');
 date='2026-10-28';submit();assert.equal(sent.length,0);
 date='2026-10-30';submit();await until(()=>sent.length===1);assert.match(sent[0],/дата вылета 30\.10\.2026/);
});

test('A new judge session renders three usable future-dated quick prompts',async t=>{
 const {root,controller,calls}=await mountedSearch(t);
 controller.reset();
 const prompts=[...root.innerHTML.matchAll(/data-api-prompt="([^"]+)"/g)].map(row=>row[1]);
 assert.equal(prompts.length,3);
 assert.ok(prompts.every(text=>/Из Москвы в .+ \d{2}\.\d{2}\.\d{4} на неделю вдвоём/.test(text)));
 assert.doesNotMatch(root.innerHTML,/\.jpg|Unsplash/);
 assert.ok(calls.every(row=>row.method==='GET'),'Rendering quick prompts must not send a chat request');
});

test('Mounted search shows unconfirmed sources, server-only price rows and accepts assumptions without checkout or POST',async t=>{
 const {root,controller,calls,click}=await mountedSearch(t);
 assert.match(root.innerHTML,/Предположения для расчёта/);assert.match(root.innerHTML,/Взрослые: 1 взрослый/);
 assert.match(root.innerHTML,/Продолжительность: 7 ночей/);assert.match(root.innerHTML,/Дата вылета: 29\.01\.2027/);
 assert.match(root.innerHTML,/По умолчанию/);assert.match(root.innerHTML,/Предложено агентом/);
 assert.match(root.innerHTML,/Рейтинг 9,1 \/ 10/);assert.match(root.innerHTML,/3 варианта с сервера · 1 взрослый/);
 assert.match(root.innerHTML,/<dt>Багаж туда<\/dt><dd>0 ₽<\/dd>/);
 assert.match(root.innerHTML,/<dt>Багаж обратно<\/dt><dd>неизвестно<\/dd>/);
 assert.match(root.innerHTML,/<dt>Трансфер в отель<\/dt><dd>неизвестно<\/dd>/);
 assert.match(root.innerHTML,/трансфер обратно в аэропорт, визы и прочие обязательные доплаты/);
 assert.doesNotMatch(root.innerHTML,/Codex|Luna|connection-badge|Полная цена:/);
 const messages=structuredClone(controller.state.messages);
 click('accept-assumptions');
 assert.equal(controller.state.assumptionsAccepted,true);assert.equal(controller.state.stage,'packages_shown');
 assert.equal(controller.state.selected,null);assert.equal(controller.state.cart,null);
 assert.deepEqual(controller.state.messages,messages);assert.ok(calls.every(call=>call.method==='GET'));
 assert.match(root.innerHTML,/Вы приняли эти условия для поиска/);
 controller.state.sources={adults:'explicit',nights:'explicit',start_date:'explicit'};controller.notify();
 assert.doesNotMatch(root.innerHTML,/api-assumptions/,'Old plan assumptions cannot reclassify explicit user conditions');
});

test('Mounted comparison is local, permits up to three current options and disappears when criteria are stale',async t=>{
 const {root,controller,calls,click}=await mountedSearch(t);
 controller.state.options.push(packageOption(4));controller.notify();
 click('compare',{index:'1'});assert.doesNotMatch(root.innerHTML,/<table>/);
 click('compare',{index:'2'});assert.match(root.innerHTML,/<table>/);
 assert.match(root.innerHTML,/scope="col">Вариант 1<br>Server Hotel 1/);assert.match(root.innerHTML,/scope="col">Вариант 2<br>Server Hotel 2/);
 assert.match(root.innerHTML,/Возвратный тариф/);assert.match(root.innerHTML,/Невозвратный тариф/);assert.match(root.innerHTML,/Катер · 34 мин/);
 click('compare',{index:'3'});assert.match(root.innerHTML,/scope="col">Вариант 3<br>Server Hotel 3/);
 click('compare',{index:'4'});assert.doesNotMatch(root.innerHTML,/scope="col">Вариант 4/);assert.match(root.innerHTML,/Можно сравнить до трёх вариантов/);
 click('compare',{index:'2'});assert.doesNotMatch(root.innerHTML,/scope="col">Вариант 2/);
 assert.ok(calls.every(call=>call.method==='GET'),'Comparison must not call search or booking');
 click('plan',{index:'1'});assert.equal(controller.state.options.length,4);
 const oldOptions=controller.state.options;controller.state.stale=true;controller.state.stage='collecting';controller.state.brief.destination='LED';
 controller.state.profile={status:'available',segment_review_count:1,evidence:[{quote:'Obsolete evidence',sentiment:'positive'}]};controller.notify();
 assert.equal(controller.state.options,oldOptions,'Presentation itself does not mutate server options');
 assert.doesNotMatch(root.innerHTML,/Server Hotel|Obsolete evidence|<table>/);assert.match(root.innerHTML,/Предыдущие предложения больше не показываются как актуальные/);
 assert.doesNotMatch(root.innerHTML,/data-api-action="choose"/);
});

test('The hotel explanation button for one adult requests reviews without inventing a family segment',async t=>{
 const {root,controller,click}=await mountedSearch(t);
 Object.assign(controller.state.brief,{adults:1,children_ages:[],pets:0});controller.notify();
 assert.match(root.innerHTML,/data-api-action="profile" data-index="2"/);
 const brief=structuredClone(controller.state.brief),sent=[];
 controller.send=async text=>{sent.push(text);};
 click('profile',{index:'2'});
 await until(()=>sent.length===1);
 assert.equal(sent[0],'Почему вариант 2 подходит? Что говорят отзывы?');
 assert.doesNotMatch(sent[0],/семь|семе|дет|family/i,'The backend must choose the review segment from the actual brief');
 assert.deepEqual(controller.state.brief,brief);
 assert.equal(controller.state.selected,null);assert.equal(controller.state.cart,null);
});

test('Stale search closes its composition dialog and quick correction sends explicit conditions rather than booking confirmation',async t=>{
 const {root,modal,controller,click,document}=await mountedSearch(t);
 click('plan',{index:'1'});assert.equal(modal.open,true);assert.match(modal.innerHTML,/Server Hotel 1/);
 controller.state.stale=true;controller.notify();assert.equal(modal.open,false);
 click('edit-preferences');assert.equal(modal.open,true);assert.match(modal.innerHTML,/api-preferences-form/);
 assert.match(modal.innerHTML,/не начало оформления/);
 const sent=[];controller.send=async text=>{sent.push(text);};
 const original=globalThis.FormData;globalThis.FormData=class {get(key){return {adults:'2',nights:'10',start_date:'2027-02-03'}[key];}};
 t.after(()=>{globalThis.FormData=original;});
 const event=new Event('submit',{cancelable:true});Object.defineProperty(event,'target',{value:{id:'api-preferences-form',reportValidity:()=>true}});document.dispatchEvent(event);
 await until(()=>sent.length===1);
 assert.equal(sent[0],'Изменить условия поиска: 2 взрослых, 10 ночей, дата вылета 03.02.2027. Заново подобрать варианты, без оформления.');
 assert.doesNotMatch(sent[0],/^Подтверждаю$/);assert.equal(modal.open,false);
 assert.match(root.innerHTML,/Уточняем условия поездки/);
});

test('Long assistant message exposes risks while retaining the entire safe formatted answer behind a native disclosure',async t=>{
 const {root,controller}=await mountedSearch(t);
 const risk='Риски: обратный трансфер не оценён. Наличие не подтверждено.';
 controller.state.messages=[{role:'assistant',text:'**Краткий план.**\n\n'+'Развёрнутое описание отелей. '.repeat(60)+'\n\n'+risk+'\n\n<img src=x onerror=alert(1)>'}];controller.notify();
 const warningPosition=root.innerHTML.indexOf('api-visible-warnings'),detailsPosition=root.innerHTML.indexOf('api-message-details');
 assert.ok(warningPosition>0&&warningPosition<detailsPosition);assert.match(root.innerHTML.slice(warningPosition,detailsPosition),/неоценённые или не включённые расходы/);
 assert.match(root.innerHTML.slice(warningPosition,detailsPosition),/Все оговорки и условия по вариантам/);
 assert.match(root.innerHTML,/<summary>Подробнее — весь ответ<\/summary>/);assert.match(root.innerHTML,/<strong>Краткий план\.<\/strong>/);
 assert.match(root.innerHTML,/&lt;img src=x onerror=alert\(1\)&gt;/);assert.doesNotMatch(root.innerHTML,/<img src=x/);
 assert.equal(root.innerHTML.includes('Развёрнутое описание отелей. '.repeat(60)),true);
});

test('An authoritative empty search in collecting explains the server reason without showing obsolete offers',async t=>{
 const {root,controller}=await mountedSearch(t);
 Object.assign(controller.state,{stage:'collecting',stale:false,options:[],notes:['В доступном календаре нет обратных рейсов на выбранные даты.']});controller.notify();
 assert.match(root.innerHTML,/Подтверждённых вариантов пока нет/);assert.match(root.innerHTML,/В доступном календаре нет обратных рейсов/);
 assert.doesNotMatch(root.innerHTML,/Server Hotel|api-search-update|data-api-action="choose"/);
});

test('Rail cards show stations, class and local times; rail edits cannot resend nights from the former hotel stay',async t=>{
 const {root,modal,controller,click,document,calls}=await mountedSearch(t);
 Object.assign(controller.state,{stage:'trains_shown',optionKind:'trains',brief:{transport:'train',origin:'MOW',destination:'LED',adults:2,nights:7,start_date:'2027-01-29'},sources:{adults:'explicit',start_date:'inferred',nights:'default'},options:[{index:1,kind:'train',number:'DEMO-NIGHT',seat_class:'coupe',origin:{iata:'MOW',name:'Москва, Ленинградский вокзал'},destination:{iata:'LED',name:'Санкт-Петербург, Московский вокзал'},departure_at:'2027-01-29T23:55:00+03:00',arrival_at:'2027-01-30T07:55:00+03:00',price_rub:5044,is_synthetic:true}]});controller.notify();
 assert.match(root.innerHTML,/Поезд № DEMO-NIGHT/);assert.match(root.innerHTML,/Купе/);
 assert.match(root.innerHTML,/Москва, Ленинградский вокзал/);assert.match(root.innerHTML,/Санкт-Петербург, Московский вокзал/);
 assert.match(root.innerHTML,/29\.01\.2027 · 23:55 \(UTC\+03:00\)/);assert.match(root.innerHTML,/30\.01\.2027 · 07:55 \(UTC\+03:00\)/);
 assert.match(root.innerHTML,/За всю группу в одну сторону · обратный билет не включён/);
 assert.match(root.innerHTML,/Модельная демо-цена/);assert.match(root.innerHTML,/Оформление железнодорожных билетов не подключено/);
 assert.match(root.innerHTML,/Дата отправления/);assert.doesNotMatch(root.innerHTML,/7 ночей|Дата вылета|Server Hotel|data-api-action="choose"|data-api-action="checkout"/);
 click('edit-preferences');assert.match(modal.innerHTML,/data-transport="train"/);assert.match(modal.innerHTML,/Дата отправления/);
 assert.doesNotMatch(modal.innerHTML,/name="nights"|Ночи|Дата вылета/);
 const sent=[],readFields=[];controller.send=async text=>{sent.push(text);};
 const original=globalThis.FormData;globalThis.FormData=class {get(key){readFields.push(key);return {adults:'3',start_date:'2027-02-03'}[key];}};
 t.after(()=>{globalThis.FormData=original;});
 const event=new Event('submit',{cancelable:true});Object.defineProperty(event,'target',{value:{id:'api-preferences-form',dataset:{transport:'train'},reportValidity:()=>true}});document.dispatchEvent(event);
 await until(()=>sent.length===1);
 assert.equal(sent[0],'Изменить условия поиска: 3 взрослых, дата отправления 03.02.2027. Заново подобрать варианты, без оформления.');
 assert.equal(readFields.includes('nights'),false);assert.ok(calls.every(call=>call.method==='GET'));
});
