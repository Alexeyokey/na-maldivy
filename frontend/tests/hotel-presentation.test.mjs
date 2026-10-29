import test from 'node:test';
import assert from 'node:assert/strict';
import {hotelDetailRows} from '../dist/hotel-presentation.js';

const facts=hotel=>Object.fromEntries(hotelDetailRows(hotel).map(({label,value})=>[label,value]));

test('Hotel facts preserve unknown values without promising family, pet or reception services',()=>{
 for(const hotel of [undefined,null,{}, {adults_only:null,pets_allowed:null,reception_24h:null,early_check_in_available:null}]){
  const rows=facts(hotel);
  assert.equal(rows['Заезд'],'Время не уточнено');assert.equal(rows['Выезд'],'Время не уточнено');
  assert.equal(rows['Стойка регистрации'],'Режим работы не уточнён');
  assert.equal(rows['С детьми'],'Правила не уточнены');assert.equal(rows['С питомцами'],'Правила не уточнены');
  assert.equal(rows['Ранний заезд'],'Возможность не уточнена');
 }
});

test('Known restrictions and a zero early-check-in price stay distinct from unknown values',()=>{
 const rows=facts({check_in_time:'00:00:00',check_out_time:'12:30:00',adults_only:true,pets_allowed:false,reception_24h:false,reception_closes_at:'23:45:00',early_check_in_available:true,early_check_in_price_rub:0});
 assert.equal(rows['Заезд'],'С 00:00');assert.equal(rows['Выезд'],'До 12:30');
 assert.equal(rows['Стойка регистрации'],'До 23:45');assert.equal(rows['С детьми'],'Только для взрослых');
 assert.equal(rows['С питомцами'],'Не разрешено');assert.equal(rows['Ранний заезд'],'По запросу · 0 ₽');
 const permitted=facts({adults_only:false,pets_allowed:true,reception_24h:true,reception_closes_at:'23:00:00',early_check_in_available:false,early_check_in_price_rub:100});
 assert.equal(permitted['С детьми'],'Дети принимаются');assert.equal(permitted['С питомцами'],'Разрешено; условия уточняются');
 assert.equal(permitted['Стойка регистрации'],'Круглосуточно');assert.equal(permitted['Ранний заезд'],'Не предусмотрен');
});

test('Hotel facts reject malformed clocks and truthy non-booleans instead of inventing known conditions',()=>{
 for(const value of ['24:00:00','12:60:00','12:30:60','12:30:00+03:00','12:30<script>','<img src=x onerror=alert(1)>',0])assert.equal(facts({check_in_time:value})['Заезд'],'Время не уточнено');
 const rows=facts({adults_only:0,pets_allowed:'true',reception_24h:1,early_check_in_available:true,early_check_in_price_rub:-1});
 assert.equal(rows['С детьми'],'Правила не уточнены');assert.equal(rows['С питомцами'],'Правила не уточнены');
 assert.equal(rows['Стойка регистрации'],'Режим работы не уточнён');assert.equal(rows['Ранний заезд'],'По запросу · цена не уточнена');
 assert.equal(facts({reception_24h:false})['Стойка регистрации'],'Не круглосуточно; часы уточняются');
});
