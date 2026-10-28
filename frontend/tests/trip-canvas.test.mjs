import test from 'node:test';
import assert from 'node:assert/strict';
import {emptyCanvas,restoreCanvas,applyCanvasEvent,canvasFields,canvasEditMessage} from '../dist/trip-canvas.js';
const initial=()=>({brief:{origin:'MOW',destination:null,start_date:null,nights:null,month:null,adults:1,children_ages:[],pets:0,budget_rub:200000,direct_only:false,refundable_only:false,visa_free_only:false,needs:[],mood:null},sources:{origin:'explicit',budget_rub:'explicit'},needs:[]});

test('Пустой холст и значения модели по умолчанию не открывают панель',()=>{
 assert.deepEqual(canvasFields(emptyCanvas()),[]);
 const event=initial();event.brief.origin=null;event.brief.budget_rub=null;event.sources={};
 assert.deepEqual(canvasFields(applyCanvasEvent(emptyCanvas(),'brief',event)),[]);
});
test('Панель берёт только фактические поля и источники холста',()=>{
 const c=applyCanvasEvent(emptyCanvas(),'brief',initial());
 assert.deepEqual(canvasFields(c),['origin','budget_rub']);assert.equal(c.brief.destination,null);
 assert.equal(c.sources.budget_rub,'explicit');assert.equal(emptyCanvas().brief,null);
});
test('Снятые ограничения удаляются полным новым брифом; false не теряется',()=>{
 let c=applyCanvasEvent(emptyCanvas(),'brief',initial());
 c=applyCanvasEvent(c,'brief',{brief:{destination:'MLE',budget_rub:null,direct_only:false},sources:{destination:'inferred',direct_only:'explicit'}});
 assert.deepEqual(canvasFields(c),['destination','direct_only']);assert.equal(c.brief.origin,undefined);
 assert.equal(c.sources.origin,undefined);assert.equal(c.brief.direct_only,false);
});
test('Прерванный ход не изменяет сохранённый холст',()=>{
 const committed=applyCanvasEvent(emptyCanvas(),'brief',initial());
 const event=initial();event.brief.budget_rub=300000;
 const preview=applyCanvasEvent(committed,'brief',event);
 assert.equal(preview.brief.budget_rub,300000);assert.equal(committed.brief.budget_rub,200000);
 event.brief.budget_rub=1;assert.equal(preview.brief.budget_rub,300000);
});
test('Подтверждённый холст сохраняет сессию и версию между перезагрузками',()=>{
 const c=applyCanvasEvent(applyCanvasEvent(emptyCanvas(),'brief',initial()),'done',{session_id:'trip-a',version:2,stage:'collecting'});
 assert.deepEqual(restoreCanvas(JSON.parse(JSON.stringify(c))),c);
 assert.throws(()=>applyCanvasEvent(c,'done',{session_id:'trip-a',version:1}));
 assert.equal(applyCanvasEvent(c,'done',{session_id:'trip-b',version:0}).version,0);
});
test('Невалидные значения не попадают в холст; неизвестные поля отбрасываются',()=>{
 for(const patch of [{budget_rub:-1},{budget_rub:'200000'},{start_date:'2026-02-31'},{adults:0},{nights:61},{children_ages:[18]}])assert.throws(()=>applyCanvasEvent(emptyCanvas(),'brief',{brief:patch}));
 assert.deepEqual(restoreCanvas({brief:{budget_rub:-1}}),emptyCanvas());
 assert.deepEqual(applyCanvasEvent(emptyCanvas(),'brief',{brief:{unsafe:'x',budget_rub:10000},sources:{unsafe:'explicit',budget_rub:'unknown'}}).brief,{budget_rub:10000});
});
test('Текст агента и посторонние события не создают поля холста',()=>{
 const c=emptyCanvas();assert.equal(applyCanvasEvent(c,'delta',{text:'Бюджет 999000, двое, Мальдивы'}),c);
 assert.equal(applyCanvasEvent(c,'options',{items:[]}),c);
});
test('Правки панели становятся обычным запросом по текущему контракту агента',()=>{
 assert.equal(canvasEditMessage('budget_rub',210000),'Бюджет 210000 рублей на всю поездку за всех.');
 assert.equal(canvasEditMessage('adults','3'),'3 взрослых.');
 assert.equal(canvasEditMessage('nights',7),'На 7 ночей.');
 assert.match(canvasEditMessage('destination','MLE'),/Мальдивы/);
 assert.match(canvasEditMessage('start_date','2026-11-20'),/20 ноября 2026/);
 assert.throws(()=>canvasEditMessage('budget_rub','NaN'));
});
