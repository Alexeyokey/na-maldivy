import test from 'node:test';
import assert from 'node:assert/strict';
import {CITIES,defaultState,allHotels,searchHotels,planTotal,reasons,addDays} from '../dist/engine.js';

const base={...defaultState(new Date('2026-10-28T12:00:00')),checkIn:'2026-11-06',checkOut:'2026-11-09'};

test('Поездка ровно в бюджет доступна, на рубль дороже — исключается',()=>{
 const s={...base,totalBudget:34600};
 assert.equal(planTotal(s,allHotels(s).find(h=>h.id==='spb-light')),34600);
 assert.ok(searchHotels(s).some(h=>h.id==='spb-light'));
 assert.ok(!searchHotels({...s,totalBudget:34599}).some(h=>h.id==='spb-light'));
});

test('Дешёвый отель не проходит, если дорога с трансфером превышают остаток',()=>{
 const s={...base,totalBudget:20000};
 assert.ok(allHotels(s).some(h=>h.price*3<20000));
 assert.equal(searchHotels(s).length,0);
});

test('Пересчёт после самолёта, трансфера, гостей и дат повторно применяет бюджет',()=>{
 const s={...base,transfer:false,totalBudget:32800};
 assert.ok(searchHotels(s).some(h=>h.id==='spb-light'));
 for(const patch of [{transport:'flight'},{transfer:true},{adults:3},{checkOut:'2026-11-10'}]){
  const next={...s,...patch};
  assert.ok(planTotal(next,allHotels(next).find(h=>h.id==='spb-light'))>s.totalBudget);
  assert.ok(!searchHotels(next).some(h=>h.id==='spb-light'));
 }
});

test('Нулевой, отрицательный и повреждённый бюджет не снимают ограничение',()=>{
 for(const totalBudget of [0,-1,NaN,Infinity,'34600'])assert.equal(searchHotels({...base,totalBudget}).length,0);
 assert.ok(searchHotels({...base,totalBudget:null}).length>0);
});

test('Обещание остатка бюджета учитывает всю поездку',()=>{
 const s={...base,totalBudget:34600};const h=allHotels(s).find(h=>h.id==='spb-light');
 for(const totalBudget of [34600,34599])assert.ok(!reasons({...s,totalBudget},h).includes('Остаётся бюджет на впечатления'));
 assert.ok(reasons({...s,totalBudget:50000},h).includes('Остаётся бюджет на впечатления'));
 assert.ok(!reasons({...s,totalBudget:null},h).includes('Остаётся бюджет на впечатления'));
});

test('Ранний заезд — разовые 35% базовой ночи в выбранном городе',()=>{
 for(const city of ['spb','kazan','sochi'])for(const duration of [1,3,7,30]){
  const s={...base,city,checkOut:addDays(base.checkIn,duration),breakfast:true,pets:true};
  const early={...s,earlyCheckIn:true};
  const h=allHotels(s).find(h=>h.id.endsWith('-light'));
  const earlyHotel=allHotels(early).find(x=>x.id===h.id);
  const cityBase=Math.round(6400*CITIES.find(c=>c.id===city).factor/100)*100;
  assert.equal(planTotal(early,earlyHotel)-planTotal(s,h),Math.round(cityBase*.35),`${city}, ${duration} ночей`);
 }
});

test('Полная цена и фильтр совпадают с независимым расчётом по всему каталогу',()=>{
 const prices={light:6400,river:7800,yard:5200,garden:9200,loft:4200,grand:11000};
 for(const city of CITIES)for(const duration of [1,3,7])for(const [adults,children] of [[1,0],[2,0],[3,1],[6,4]])for(const transport of [city.train?'train':'flight','flight']){
  if(transport==='flight'&&!city.flight)continue;
  for(const extras of [false,true]){
   const s={...base,city:city.id,adults,children,transport,checkOut:addDays(base.checkIn,duration),breakfast:extras,pets:extras,earlyCheckIn:extras,transfer:extras};
   for(const h of allHotels(s)){
    const baseNight=Math.round(prices[h.id.split('-')[1]]*city.factor/100)*100;
    const lodging=(baseNight+(extras?750:0))*duration+(extras?Math.round(baseNight*.35):0);
    const fare=s.city===s.origin?0:(transport==='flight'?Math.round(city.fare*1.55/100)*100:city.fare)*(adults+children)*2;
    const transfer=extras?((adults+children>4?1800:900)*2):0;
    const expected=lodging+fare+transfer;
    assert.equal(planTotal(s,h),expected,JSON.stringify({s,h:h.id}));
    for(const delta of [-1,0,1]){
     const eligible=(!s.pets||h.pets);
     assert.equal(searchHotels({...s,totalBudget:expected+delta}).some(x=>x.id===h.id),eligible&&delta>=0,h.id);
    }
   }
  }
 }
});
