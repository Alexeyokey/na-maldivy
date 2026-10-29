import test from 'node:test';
import assert from 'node:assert/strict';
import {formatAssistantText as format} from '../dist/message-format.js';

function assertSafeHtml(html){
 const stack=[];let end=0;
 for(const match of html.matchAll(/<[^>]*>/g)){
  assert.doesNotMatch(html.slice(end,match.index),/[<>]/);end=match.index+match[0].length;
  const token=match[0];
  if(token==='<br>'){assert.ok(['p','li','strong','em'].includes(stack.at(-1)));continue;}
  const closing=/^<\/(p|strong|em|code|ol|ul|li)>$/.exec(token);
  if(closing){assert.equal(stack.pop(),closing[1],html);continue;}
  const opening=/^<(p|strong|em|code|ol|ul|li)(?: (start|value)="(0|[1-9][0-9]{0,8})")?>$/.exec(token);
  assert.ok(opening,'Only fixed safe tags and canonical integer attributes may be generated: '+token);
  const [,tag,attribute]=opening;
  if(attribute)assert.ok(tag==='ol'&&attribute==='start'||tag==='li'&&attribute==='value');
  if(tag==='ol'||tag==='ul'||tag==='p')assert.equal(stack.length,0,'Block elements cannot nest');
  if(tag==='li')assert.ok(['ol','ul'].includes(stack.at(-1)),'List items must stay inside a list');
  stack.push(tag);
 }
 assert.doesNotMatch(html.slice(end),/[<>]/);assert.deepEqual(stack,[],'All generated tags must close');
}

const exactUserAnswer=`Искали поездку из Москвы на Мальдивы в январе; предполагаю одного взрослого, 7 ночей и вылет 29.01 — это дата с минимальной ценой в доступном календаре перелётов в одну сторону, разница с максимальной — 8 022 ₽, не экономия всей поездки.

1. **Оптимальный — JEN, 4★, 9,1.** Подходит прямыми рейсами, круглосуточной стойкой регистрации и такси до отеля за 26 мин и 1 100 ₽; цена за пакет — 136 531 ₽, включая перелёты туда и обратно, багаж, отель и трансфер по прибытии. Компромисс — невозвратный отель и ночной перелёт туда. Риски: оба рейса и их цены сгенерированы и не подтверждены поставщиком; обратный трансфер, виза и другие обязательные доплаты не оценены. Тариф отеля не возвращается при переносе дат.

2. **Подешевле — Villa Nautica, 5★, 7,2.** Самый дешёвый вариант, пакет — 135 527 ₽; прямые рейсы, круглосуточная стойка регистрации и катер до отеля за 34 мин и 7 600 ₽. Компромисс — невозвратный отель и ночной перелёт туда. Риски: оба рейса и цены сгенерированы и не подтверждены поставщиком; обратный трансфер, виза и другие обязательные доплаты не оценены. Тариф отеля не возвращается при переносе дат.

3. **Комфорт — Four Seasons Resort, 5★, 9,3.** Подходит возвратным тарифом отеля и прямыми рейсами; пакет — 187 374 ₽. Компромиссы — ночной перелёт туда и гидросамолёт до отеля за 1 ч 37 мин и 35 500 ₽; его нужно бронировать заранее, есть ограничение багажа. Риски: оба рейса и цены сгенерированы и не подтверждены поставщиком; обратный трансфер, виза и другие обязательные доплаты не оценены; сроки и удержания при возврате отеля нужно уточнить.

Все данные демонстрационные: расписания и тарифы сгенерированы, наличие не подтверждено; профили отзывов отелей также синтетические. Выберите вариант или поменяйте условия — оформление начнётся после выбора и подтверждения.`;

test('The pasted travel answer becomes two paragraphs and one numbered list despite blank lines between items',()=>{
 const html=format(exactUserAnswer);assertSafeHtml(html);
 assert.equal((html.match(/<p>/g)||[]).length,2);assert.equal((html.match(/<ol /g)||[]).length,1);
 assert.deepEqual([...html.matchAll(/<li value="(\d+)">/g)].map(match=>match[1]),['1','2','3']);
 assert.ok(html.includes('<strong>Оптимальный — JEN, 4★, 9,1.</strong>'));
 assert.ok(html.includes('<strong>Подешевле — Villa Nautica, 5★, 7,2.</strong>'));
 assert.ok(html.includes('<strong>Комфорт — Four Seasons Resort, 5★, 9,3.</strong>'));
 assert.ok(html.includes('вылет 29.01'));assert.ok(html.endsWith('после выбора и подтверждения.</p>'));
 assert.doesNotMatch(html,/\*\*/);
});

test('Paragraphs, single line breaks and CRLF remain readable without empty blocks',()=>{
 assert.equal(format('\r\n  \r\nПервый абзац.\r\nСледующая строка.\r\n\r\n\r\nВторой абзац.\r'),'<p>Первый абзац.<br>Следующая строка.</p><p>Второй абзац.</p>');
 for(const value of ['',null,undefined,' \n\t\r\n'])assert.equal(format(value),'');
 assert.equal(format(42),'<p>42</p>');
});

test('Simple emphasis nests safely and inline code never reparses its contents',()=>{
 assert.equal(format('**Жирный *курсив* и `код <x>`**'),'<p><strong>Жирный <em>курсив</em> и <code>код &lt;x&gt;</code></strong></p>');
 assert.equal(format('`**нежирный** *неem* <tag> & "кавычки"`'),'<p><code>**нежирный** *неem* &lt;tag&gt; &amp; &quot;кавычки&quot;</code></p>');
 assert.equal(format('*Курсив **и жирный** текст*'),'<p><em>Курсив <strong>и жирный</strong> текст</em></p>');
 assert.equal(format('\\*буквальные\\* и \\`кавычки\\`'),'<p>*буквальные* и `кавычки`</p>');
});

test('Explicit counters survive gaps, repeated labels, zero and both ordered marker styles',()=>{
 const html=format('3. три\n\n7) семь\n\n7. снова семь\n8. восемь');assertSafeHtml(html);
 assert.equal(html,'<ol start="3"><li value="3">три</li><li value="7">семь</li><li value="7">снова семь</li><li value="8">восемь</li></ol>');
 assert.equal(format('0. ноль'),'<ol start="0"><li value="0">ноль</li></ol>');
 assert.equal(format('999999999. верхняя граница'),'<ol start="999999999"><li value="999999999">верхняя граница</li></ol>');
 for(const text of ['01. ведущий ноль','1000000000. слишком большое число','999999999999999999999999. число','29.01 — дата','9,1 — рейтинг'])assert.equal(format(text),'<p>'+text+'</p>');
});

test('Bullets, numbered blocks and continuation lines close correctly when block types change',()=>{
 const html=format('- **Первый**\n  продолжение\n\n+ Второй\n* Третий\n• Четвёртый\n\n4. Четыре\n\nТекст\nещё строка');assertSafeHtml(html);
 assert.equal(html,'<ul><li><strong>Первый</strong><br>продолжение</li><li>Второй</li><li>Третий</li><li>Четвёртый</li></ul><ol start="4"><li value="4">Четыре</li></ol><p>Текст<br>ещё строка</p>');
 assert.equal(format('Параграф\n1. пункт\n- другой список'),'<p>Параграф</p><ol start="1"><li value="1">пункт</li></ol><ul><li>другой список</li></ul>');
});

test('Input tags, entities, quotes and SVG cannot create executable HTML or attributes',()=>{
 const input='<script>alert("x")</script> <img src=x onerror="alert(1)"> <svg/onload=alert(1)> & \'quote\'';
 const html=format(input);assertSafeHtml(html);
 assert.equal(html,'<p>&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &lt;img src=x onerror=&quot;alert(1)&quot;&gt; &lt;svg/onload=alert(1)&gt; &amp; &#39;quote&#39;</p>');
 assert.equal(format('&lt;img&gt; &#x3c;script&#x3e;'),'<p>&amp;lt;img&amp;gt; &amp;#x3c;script&amp;#x3e;</p>');
 for(const input of ['**<img src=x onerror=alert(1)>**','1. <script>x</script>','1" onclick="alert(1). text','`</code><img src=x onerror=alert(1)>`'])assertSafeHtml(format(input));
});

test('Links, images, URLs and unsupported HTML syntax remain inert text',()=>{
 const input='[нажать](javascript:alert(1)) ![img](data:text/html,<script>x</script>) https://example.com/?a="b" <https://example.com>';
 const html=format(input);assertSafeHtml(html);assert.doesNotMatch(html,/<(?:a|img|script|iframe|style)\b/i);
 assert.ok(html.includes('[нажать](javascript:alert(1))'));assert.ok(html.includes('&lt;https://example.com&gt;'));
});

test('Every streamed prefix stays balanced; incomplete and unsupported markers remain visible',()=>{
 for(const sample of [exactUserAnswer,'**Жирный *курсив* и `код <x>`**','***жирный?** *хвост','**начало','*начало','`начало','****','``']){
  for(let end=0;end<=sample.length;end++)assertSafeHtml(format(sample.slice(0,end)));
 }
 assert.equal(format('**начало'),'<p>**начало</p>');assert.equal(format('*начало'),'<p>*начало</p>');assert.equal(format('`начало'),'<p>`начало</p>');
 assert.equal(format('***тройные***'),'<p>***тройные***</p>');
});

test('Long unmatched marker streams remain bounded and never recurse into a formatting stack',()=>{
 const input='**a *b ** c `x` '.repeat(15000)+'`unfinished'+('*'.repeat(100000));
 const started=performance.now(),html=format(input),elapsed=performance.now()-started;
 assertSafeHtml(html);assert.ok(html.length<input.length*8,'Output growth stays bounded');
 assert.ok(elapsed<5000,'Marker parsing must not exhibit catastrophic backtracking');
});
