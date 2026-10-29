import test from 'node:test';
import assert from 'node:assert/strict';
import {adultsLabel,nightsLabel,optionsLabel,formatDate,formatRating,refundLabel,transferLabel,priceRows,tripAssumptions,narrativeView,trainPresentation} from '../dist/trip-presentation.js';

test('Russian counts keep teens and large values grammatical',()=>{
 assert.deepEqual([1,2,5,11,21].map(adultsLabel),['1 взрослый','2 взрослых','5 взрослых','11 взрослых','21 взрослый']);
 assert.deepEqual([1,3,7,12,22].map(nightsLabel),['1 ночь','3 ночи','7 ночей','12 ночей','22 ночи']);
 assert.deepEqual([1,2,5,11,101].map(optionsLabel),['1 вариант','2 варианта','5 вариантов','11 вариантов','101 вариант']);
 assert.equal(nightsLabel(null),'не уточнено');
});
test('Dates and ratings have one safe consistent display',()=>{
 assert.equal(formatDate('2026-11-18T23:40:00+03:00'),'18.11.2026');
 assert.equal(formatDate('2026-02-30'),'дата не уточнена');
 assert.equal(formatDate('<img src=x>'),'дата не уточнена');
 assert.equal(formatRating(9.1),'9,1');assert.equal(formatRating(9),'9,0');
 assert.equal(formatRating(null),'нет оценки');assert.equal(formatRating(11),'нет оценки');
});
test('Only unconfirmed assumptions are presented; explicit input wins over old plan assumptions',()=>{
 const state={brief:{adults:1,month:1,nights:null,start_date:null},sources:{month:'explicit'},assumptions:[{field:'nights',text:'беру 7 ночей'},{field:'start_date',text:'минимальная цена в календаре в одну сторону'}],options:[{outbound:{departure_at:'2027-01-29T12:00:00Z'},hotel:{nights:7}}]};
 assert.deepEqual(tripAssumptions(state).map(row=>[row.field,row.source,row.display]),[['adults','default','1 взрослый'],['nights','default','7 ночей'],['start_date','inferred','29.01.2027']]);
 state.sources={adults:'explicit',nights:'explicit',start_date:'explicit'};
 assert.deepEqual(tripAssumptions(state),[]);
 state.sources={adults:'inferred'};state.brief.adults=2;
 assert.equal(tripAssumptions(state)[0].source,'inferred');
 assert.equal(tripAssumptions({brief:{},sources:{},options:[]}).length,0);
});
test('Breakdown preserves each server amount including zero and unknown amounts without summing a full price',()=>{
 const rows=priceRows({outbound:{price_rub:10000,baggage_price_rub:0},inbound:{price_rub:11000,baggage_price_rub:null},hotel:{price_rub:45000},transfer:null,known_total_rub:66000});
 assert.deepEqual(rows.map(row=>row.value),[10000,0,11000,null,45000,undefined]);
 assert.equal(rows.length,6);assert.equal(rows.some(row=>/полная/i.test(row.label)),false);
 assert.equal(refundLabel(null),'Условия возврата не уточнены');assert.equal(refundLabel(false),'Невозвратный тариф');
 assert.equal(transferLabel(null),'Трансфер не оценён');
 assert.equal(transferLabel({mode:'seaplane',duration_minutes:97,available:false}),'Гидросамолёт · 97 мин · ожидание отправления');
});
test('Long narratives keep the complete original and a concise factual index of warning categories',()=>{
 const warning='Риски: рейсы сгенерированы; обратный трансфер не оценён. Наличие не подтверждено.';
 const text='План поездки.\n\n'+('Подробное описание. '.repeat(90))+'\n\n'+warning+'\n\nОтель невозвратный.';
 const result=narrativeView(text);
 assert.equal(result.compact,true);assert.equal(result.text,text);assert.equal(result.preview,'План поездки.');
 assert.equal(result.warnings.length,4);
 assert.match(result.warnings.join(' '),/демонстрационные или сгенерированные/);
 assert.match(result.warnings.join(' '),/не подтверждены поставщиком/);
 assert.match(result.warnings.join(' '),/неоценённые или не включённые расходы/);
 assert.match(result.warnings.join(' '),/ограничения возврата/);
 assert.doesNotMatch(result.warnings.join(' '),/Отель невозвратный/,'A compact index cannot attribute a specific risk to a hotel');
 assert.equal(narrativeView('Короткий ответ.').compact,false);
 const variants=narrativeView('Описание. '.repeat(150)+' Тариф не подтверждён. Багаж не включён.');
 assert.equal(variants.warnings.length,2,'Both ё forms stay visible in the warning index');
});

const pastedAnswer=`Искали поездку из Москвы на Мальдивы в январе; предполагаю одного взрослого, 7 ночей и вылет 29.01 — это дата с минимальной ценой в доступном календаре перелётов в одну сторону, разница с максимальной — 8 022 ₽, не экономия всей поездки.

1. **Оптимальный — JEN, 4★, 9,1.** Подходит прямыми рейсами, круглосуточной стойкой регистрации и такси до отеля за 26 мин и 1 100 ₽; цена за пакет — 136 531 ₽, включая перелёты туда и обратно, багаж, отель и трансфер по прибытии. Компромисс — невозвратный отель и ночной перелёт туда. Риски: оба рейса и их цены сгенерированы и не подтверждены поставщиком; обратный трансфер, виза и другие обязательные доплаты не оценены. Тариф отеля не возвращается при переносе дат.

2. **Подешевле — Villa Nautica, 5★, 7,2.** Самый дешёвый вариант, пакет — 135 527 ₽; прямые рейсы, круглосуточная стойка регистрации и катер до отеля за 34 мин и 7 600 ₽. Компромисс — невозвратный отель и ночной перелёт туда. Риски: оба рейса и цены сгенерированы и не подтверждены поставщиком; обратный трансфер, виза и другие обязательные доплаты не оценены. Тариф отеля не возвращается при переносе дат.

3. **Комфорт — Four Seasons Resort, 5★, 9,3.** Подходит возвратным тарифом отеля и прямыми рейсами; пакет — 187 374 ₽. Компромиссы — ночной перелёт туда и гидросамолёт до отеля за 1 ч 37 мин и 35 500 ₽; его нужно бронировать заранее, есть ограничение багажа. Риски: оба рейса и цены сгенерированы и не подтверждены поставщиком; обратный трансфер, виза и другие обязательные доплаты не оценены; сроки и удержания при возврате отеля нужно уточнить.

Все данные демонстрационные: расписания и тарифы сгенерированы, наличие не подтверждено; профили отзывов отелей также синтетические. Выберите вариант или поменяйте условия — оформление начнётся после выбора и подтверждения.`;
test('The actual three-hotel answer is materially shorter without inventing hotel-specific risks',()=>{
 const result=narrativeView(pastedAnswer);
 assert.equal(result.text,pastedAnswer);assert.equal(result.compact,true);
 assert.equal(result.warnings.length,5);
 assert.ok(result.preview.length+result.warnings.join(' ').length<pastedAnswer.length/2);
 assert.doesNotMatch(result.warnings.join(' '),/JEN|Villa Nautica|Four Seasons|9,1|136 531/);
 assert.ok(result.text.includes('его нужно бронировать заранее'));
 assert.ok(result.text.includes('Тариф отеля не возвращается при переносе дат.'));
});
test('Single-line-separated fallback lists never leave a truncated offer in the overview',()=>{
 const intro='Предполагаю 1 взрослого и 7 ночей. Вот варианты.';
 const options='\n1. **JEN** — '+('Данные первого варианта. '.repeat(30))+'\n2. **Villa Nautica** — '+('Данные второго варианта. '.repeat(30));
 assert.equal(narrativeView(intro+options).preview,intro);
 const noIntro=narrativeView(options.trim());
 assert.equal(noIntro.preview,'Подробный ответ с вариантами — ниже.');
 assert.doesNotMatch(noIntro.preview,/JEN|Villa Nautica/);
});
test('Rail presentation uses the actual Train contract and never reuses flight nights or claims a timetable provider',()=>{
 const train={number:'DEMO-NIGHT',seat_class:'coupe',origin:{name:'Москва, Ленинградский вокзал'},destination:{name:'Санкт-Петербург, Московский вокзал'},departure_at:'2027-01-29T23:55:00+03:00',is_synthetic:true};
 const view=trainPresentation(train);
 assert.equal(view.number,'DEMO-NIGHT');assert.equal(view.seatClass,'Купе');
 assert.equal(view.originStation,'Москва, Ленинградский вокзал');assert.equal(view.destinationStation,'Санкт-Петербург, Московский вокзал');
 assert.equal(view.priceLabel,'Модельная демо-цена');assert.match(view.limitation,/Источник расписания в карточке не указан/);
 assert.doesNotMatch(view.limitation,/Яндекс|реальное расписание|цена поставщика/);
 const assumptions=tripAssumptions({brief:{transport:'train',adults:1,nights:7,start_date:'2027-01-29'},sources:{adults:'default',nights:'default',start_date:'inferred'},assumptions:[{field:'nights',text:'7 ночей'}],options:[train]});
 assert.deepEqual(assumptions.map(row=>row.field),['adults','start_date']);
 assert.equal(assumptions[1].label,'Дата отправления');
});
