import test from 'node:test';
import assert from 'node:assert/strict';
import {defaultState,parseRequest,TEST_CASES,searchHotels,nights,allHotels,planTotal,connections,currentTransport,addDays,missingRequiredCriteria,recommendationsReady,requiredCriteriaQuestion,recommendHotels} from '../dist/engine.js';
const now=new Date('2026-10-25T12:00:00');
for(const t of TEST_CASES)test(t.q,()=>{const previous={...defaultState(now),breakfast:true,totalBudget:60000,known:{totalBudget:true}};const result=parseRequest(t.q,previous,now);assert.ok(t.check(result.state,previous,result),JSON.stringify(result));});
test('Последовательные изменения сохраняют контекст и перестраивают план',()=>{
 let s=defaultState(now);
 s=parseRequest('В Казань с 6 по 9 ноября, вдвоём, с завтраком, до 9 тысяч за ночь',s,now).state;
 const h=searchHotels(s)[0];s.selectedHotel=h.id;const oldTotal=planTotal(s,h);
 s=parseRequest('А если на два дня позже?',s,now).state;
 assert.equal(s.checkIn,'2026-11-08');assert.equal(s.checkOut,'2026-11-11');assert.equal(s.city,'kazan');assert.equal(s.breakfast,true);assert.equal(s.selectedHotel,h.id);assert.equal(planTotal(s,allHotels(s).find(x=>x.id===h.id)),oldTotal);
 s=parseRequest('С нами едет собака',s,now).state;
 assert.ok(searchHotels(s).every(x=>x.pets));assert.equal(s.checkIn,'2026-11-08');assert.equal(s.adults,2);
});
test('Неподходящий выбранный отель снимается при смене требований',()=>{
 let s={...defaultState(now),budget:15000,selectedHotel:'spb-grand'};
 const result=parseRequest('С нами едет собака',s,now);
 assert.equal(result.state.selectedHotel,null);assert.equal(result.removedSelection,true);
});
test('Итог включает отель, билеты за всех и трансфер',()=>{
 const s={...defaultState(now),adults:3,children:1,transfer:true};const h=allHotels(s)[0];
 assert.equal(planTotal(s,h),h.price*3+3400*4*2+1800);
 assert.equal(currentTransport({...s,adults:2}).price,3400*3*2);
});
test('Приезд до заселения виден как предупреждение, ранний заезд закрывает разрыв',()=>{
 const s=defaultState(now);assert.equal(connections(s)[1].ok,false);assert.equal(connections({...s,earlyCheckIn:true})[1].ok,true);
});
test('Несовместимые фильтры возвращают пустую выдачу',()=>{
 const s={...defaultState(now),budget:3000,pets:true,spa:true};assert.equal(searchHotels(s).length,0);
});
test('Непонятный запрос не стирает пожелания',()=>{
 const s={...defaultState(now),pets:true,breakfast:true};const r=parseRequest('абракадабра',s,now);assert.equal(r.recognized,false);assert.equal(r.state.pets,true);assert.equal(r.state.breakfast,true);assert.equal(r.state.checkIn,s.checkIn);
});
test('Общий бюджет ограничивает весь план, а не только отель',()=>{
 const s=parseRequest('До 45 тысяч на всю поездку',defaultState(now),now).state;assert.equal(s.totalBudget,45000);for(const h of searchHotels(s))assert.ok(planTotal(s,h)<=45000);
});
test('В дату заезда нельзя сдвинуться в прошлое',()=>{
 const s=defaultState(now);const r=parseRequest('На 30 дней раньше',s,now);assert.ok(r.state.checkIn>='2026-10-25');assert.equal(nights(r.state),3);
});
test('Питание и доплата за питомца включаются в тариф ровно один раз',()=>{
 const s=defaultState(now);const base=allHotels(s)[0].price;assert.equal(allHotels({...s,breakfast:true,pets:true})[0].price,base+750);
});
test('Пересечение месяца не меняет длительность при сдвиге',()=>{
 const s={...defaultState(now),checkIn:'2026-10-30',checkOut:'2026-11-02'};const r=parseRequest('на два дня позже',s,now);assert.equal(r.state.checkIn,'2026-11-01');assert.equal(r.state.checkOut,'2026-11-04');assert.equal(nights(r.state),3);
});

test('Панель начинает с пустого набора известных параметров',()=>{assert.deepEqual(defaultState(now).known,{});});
test('Только явно переданные параметры появляются в панели',()=>{const r=parseRequest('В Казань до 60 тысяч',defaultState(now),now);assert.equal(r.state.known.city,true);assert.equal(r.state.known.totalBudget,true);assert.equal(r.state.totalBudget,60000);assert.equal(r.state.known.adults,undefined);assert.equal(r.state.known.checkIn,undefined);});
test('Бюджет без уточнения ограничивает всю поездку',()=>{const r=parseRequest('Петербург до 40000',defaultState(now),now);assert.equal(r.state.totalBudget,40000);for(const h of searchHotels(r.state))assert.ok(planTotal(r.state,h)<=40000);});
test('Уточнение одной даты сохраняет названную длительность',()=>{let s=parseRequest('В Казань на три ночи',defaultState(now),now).state;assert.equal(s.known.checkIn,undefined);s=parseRequest('С 6 ноября',s,now).state;assert.equal(s.checkIn,'2026-11-06');assert.equal(s.checkOut,'2026-11-09');assert.equal(s.known.checkIn,true);});

test('Значения по умолчанию не считаются заполненными критериями',()=>{
 const s=defaultState(now);assert.deepEqual(missingRequiredCriteria(s),['direction','budget','dates','guests']);assert.equal(recommendationsReady(s),false);assert.deepEqual(recommendHotels(s),[]);
});
for(let mask=0;mask<16;mask++)test('Выдача для комбинации обязательных критериев '+mask,()=>{
 const s={...defaultState(now),totalBudget:60000,known:{city:!!(mask&1),totalBudget:!!(mask&2),checkIn:!!(mask&4),checkOut:!!(mask&4),adults:!!(mask&8)}};
 assert.equal(recommendationsReady(s),mask===15);
 assert.equal(recommendHotels(s).length>0,mask===15);
});
test('Пошаговый диалог не выдаёт отели до последнего ответа',()=>{
 let s=defaultState(now);
 for(const [i,q]of ['Казань','60000','С 6 по 9 ноября','2'].entries()){
  const result=parseRequest(q,s,now);s=result.state;assert.equal(result.recognized,true);
  assert.equal(recommendHotels(s).length>0,i===3);
 }
 assert.equal(s.adults,2);assert.equal(s.totalBudget,60000);assert.equal(requiredCriteriaQuestion(s),'');
});
test('Одна дата без длительности не завершает обязательные даты',()=>{
 let s=parseRequest('В Казань с 6 ноября вдвоём до 60000',defaultState(now),now).state;
 assert.deepEqual(missingRequiredCriteria(s),['dates']);assert.equal(s.known.checkOut,false);assert.deepEqual(recommendHotels(s),[]);assert.match(requiredCriteriaQuestion(s),/вернуться/);
 s=parseRequest('На три ночи',s,now).state;assert.equal(recommendationsReady(s),true);assert.equal(s.checkOut,'2026-11-09');
});
test('Недопустимые значения не открывают подбор при установленных флагах',()=>{
 const s=parseRequest('Казань с 6 по 9 ноября вдвоём до 60000',defaultState(now),now).state;
 for(const patch of [{totalBudget:0},{totalBudget:null},{adults:0},{city:'unknown'},{checkOut:s.checkIn},{checkOut:'2026-02-31'}]){
  assert.equal(recommendationsReady({...s,...patch}),false);assert.deepEqual(recommendHotels({...s,...patch}),[]);
 }
});
test('Запрос вдохновения без четырёх критериев не открывает выдачу',()=>{
 const r=parseRequest('Не знаю, куда поехать, до 50000',defaultState(now),now);assert.equal(r.inspiration,true);assert.equal(recommendationsReady(r.state),false);assert.deepEqual(recommendHotels(r.state),[]);assert.match(requiredCriteriaQuestion(r.state),/Куда/);
});

test('Короткое уточнение даты выезда не изменяет уже названный заезд',()=>{let s=parseRequest('Казань с 6 ноября вдвоём до 60000',defaultState(now),now).state;s=parseRequest('9 ноября',s,now).state;assert.equal(s.checkIn,'2026-11-06');assert.equal(s.checkOut,'2026-11-09');assert.equal(recommendationsReady(s),true);});

test('Слово поездка не выбирает поезд вместо уже выбранного самолёта',()=>{const s={...defaultState(now),transport:'flight',known:{city:true,transport:true}};const r=parseRequest('Бюджет 60000 на всю поездку',s,now);assert.equal(r.state.transport,'flight');});

test('Завтрак не сдвигает даты на завтра и не подставляет их без запроса',()=>{const base={...defaultState(now),checkIn:'2026-11-06',checkOut:'2026-11-09',breakfast:true,known:{checkIn:true,checkOut:true,breakfast:true}};const r=parseRequest('Без завтрака',base,now);assert.equal(r.state.checkIn,base.checkIn);assert.equal(r.state.checkOut,base.checkOut);assert.equal(parseRequest('Добавь завтрак',defaultState(now),now).state.known.checkIn,undefined);});
