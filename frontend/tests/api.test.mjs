import test from 'node:test';
import assert from 'node:assert/strict';
import {streamChat,checkAgentHealth,fetchSessionCanvas,ChatApiError} from '../dist/api.js';
const encoder=new TextEncoder();
function response(chunks){return new Response(new ReadableStream({start(c){for(const chunk of chunks)c.enqueue(typeof chunk==='string'?encoder.encode(chunk):chunk);c.close();}}),{headers:{'Content-Type':'text/event-stream'}});}
test('POST body matches the existing repository contract; UTF-8 chunks stream intact',async()=>{
 const raw=encoder.encode(': ping\r\nevent: delta\r\ndata: {"text":"Привет 🌴"}\r\n\r\nevent: done\r\ndata: {}\r\n\r\n');const deltas=[];
 const r=await streamChat({message:'В Казань',onDelta:(d,all)=>deltas.push(all),fetchImpl:async(url,init)=>{assert.equal(url,'/api/chat');assert.equal(init.method,'POST');assert.equal(init.credentials,'same-origin');assert.equal(init.cache,'no-store');assert.equal(init.redirect,'error');assert.deepEqual(JSON.parse(init.body),{message:'В Казань'});return response(Array.from(raw,byte=>new Uint8Array([byte])));}});
 assert.equal(r.text,'Привет 🌴');assert.deepEqual(deltas,['Привет 🌴']);
});
test('Multiline data and comments follow SSE framing',async()=>{
 const r=await streamChat({message:'Привет',fetchImpl:async()=>response([': heartbeat\n\nevent: delta\ndata: {\ndata: "text":"Ответ"}\n\nevent: done\ndata: {}\n\n'])});assert.equal(r.text,'Ответ');
});
test('Errors preserve partial answer',async()=>{
 await assert.rejects(streamChat({message:'Привет',fetchImpl:async()=>response(['event: delta\ndata: {"text":"Начинаю"}\n\nevent: error\ndata: {"message":"Попробуйте позже"}\n\n'])}),e=>e instanceof ChatApiError&&e.code==='agent'&&e.partialText==='Начинаю'&&e.message==='Попробуйте позже');
});
test('Missing done never masquerades as a completed answer',async()=>{
 await assert.rejects(streamChat({message:'Привет',fetchImpl:async()=>response(['event: delta\ndata: {"text":"Часть"}\n\n'])}),e=>e.code==='incomplete'&&e.partialText==='Часть');
});
test('Validation, HTTP error and wrong content type are explicit',async()=>{
 await assert.rejects(streamChat({message:'',fetchImpl:()=>assert.fail()}),e=>e.code==='validation');
 await assert.rejects(streamChat({message:'x'.repeat(4001),fetchImpl:()=>assert.fail()}),e=>e.code==='validation');
 await assert.rejects(streamChat({message:'hi',fetchImpl:async()=>new Response('{}',{status:422})}),e=>e.status===422);
 await assert.rejects(streamChat({message:'hi',fetchImpl:async()=>new Response('<html>')}),e=>e.code==='protocol');
});
test('Abort remains distinguishable from server failure',async()=>{
 const controller=new AbortController();controller.abort();await assert.rejects(streamChat({message:'hi',signal:controller.signal,fetchImpl:async()=>{throw new DOMException('Aborted','AbortError');}}),e=>e.name==='AbortError'&&e.code==='aborted');
});
test('A caller-provided request ID is sent unchanged together with the original message and session',async()=>{
 for(const requestId of ['a','retry_01-safe','A'.repeat(64)]){
  let calls=0;
  const result=await streamChat({message:'  Повторяю сообщение  ',sessionId:'session_01',requestId,fetchImpl:async(_,init)=>{
   calls++;assert.deepEqual(JSON.parse(init.body),{message:'  Повторяю сообщение  ',session_id:'session_01',request_id:requestId});
   return response(['event: done\ndata: {"session_id":"session_01"}\n\n']);
  }});
  assert.equal(calls,1);assert.equal(result.sessionId,'session_01');
 }
});
test('Unsafe request IDs are rejected before any network call',async()=>{
 for(const requestId of ['',null,42,{},'A'.repeat(65),'bad/identifier','bad identifier','header\r\ninjection','юникод']){
  await assert.rejects(streamChat({message:'Привет',requestId,fetchImpl:()=>assert.fail('Invalid ID reached the network')}),e=>e instanceof ChatApiError&&e.code==='validation');
 }
});
test('An explicit manual retry preserves the original request payload and sends retry only when true',async()=>{
 const original={message:'  Первое сообщение  ',sessionId:'original-session',requestId:'original-request'};
 for(const retry of [undefined,false,true]){
  let calls=0;
  await streamChat({...original,retry,fetchImpl:async(_,init)=>{
   calls++;assert.deepEqual(JSON.parse(init.body),{message:original.message,session_id:original.sessionId,request_id:original.requestId,...(retry?{retry:true}:{})});
   return response(['event: done\ndata: {"session_id":"original-session"}\n\n']);
  }});
  assert.equal(calls,1);
 }
 await streamChat({message:'Первое сообщение без сессии',requestId:'original-request',retry:true,fetchImpl:async(_,init)=>{
  assert.deepEqual(JSON.parse(init.body),{message:'Первое сообщение без сессии',request_id:'original-request',retry:true});
  return response(['event: done\ndata: {"session_id":"new-session"}\n\n']);
 }});
});
test('A retry needs a valid original request ID and a boolean flag before any network call',async()=>{
 for(const options of [{retry:true},{retry:true,requestId:null},{retry:true,requestId:''},...[null,0,1,'true','false',{}].map(retry=>({retry,requestId:'safe-request'}))]){
  await assert.rejects(streamChat({message:'Привет',...options,fetchImpl:()=>assert.fail('Invalid retry reached the network')}),e=>e instanceof ChatApiError&&e.code==='validation');
 }
});
test('Transport never automatically repeats a POST after a network failure or redirect rejection',async()=>{
 let calls=0;
 await assert.rejects(streamChat({message:'Привет',requestId:'same-request',fetchImpl:async(_,init)=>{
  calls++;assert.equal(init.redirect,'error');throw new TypeError('Failed to fetch https://user:secret@example.test/private-token');
 }}),e=>e instanceof ChatApiError&&e.code==='network'&&e.partialText===''&&!/secret|example|private-token|Failed to fetch/.test(e.message));
 assert.equal(calls,1);
});
test('A failed reader preserves partial text and hides raw connection diagnostics',async()=>{
 let reads=0,cancels=0,releases=0,posts=0;const deltas=[];
 const reader={
  async read(){if(reads++===0)return {done:false,value:encoder.encode('event: delta\ndata: {"text":"Уже найдено"}\n\n')};throw new TypeError('socket closed: auth=private-token');},
  async cancel(){cancels++;throw new Error('cancel-private-secret');},
  releaseLock(){releases++;throw new Error('release-private-secret');},
 };
 await assert.rejects(streamChat({message:'Привет',requestId:'same-request',onDelta:(_,all)=>deltas.push(all),fetchImpl:async()=>{
  posts++;return {ok:true,headers:new Headers({'Content-Type':'text/event-stream'}),body:{getReader:()=>reader}};
 }}),e=>e instanceof ChatApiError&&e.code==='network'&&e.partialText==='Уже найдено'&&e.message.includes('Связь прервалась')&&!/socket|auth|private|secret/.test(e.message));
 assert.deepEqual(deltas,['Уже найдено']);assert.equal(posts,1);assert.equal(reads,2);assert.equal(cancels,1);assert.equal(releases,1);
});
test('HTTP failures keep a status and a safe actionable message without exposing response details',async()=>{
 const statuses=new Map([[401,/войдите/],[403,/Нет доступа/],[409,/Дождитесь/],[410,/Срок восстановления ответа истёк/],[422,/длину/],[429,/Подождите/],[502,/повторить/],[503,/Подождите/],[504,/ожидания/],[500,/недоступен/]]);
 for(const [status,pattern] of statuses){
  let calls=0;
  await assert.rejects(streamChat({message:'Привет',requestId:'same-request',fetchImpl:async()=>{
   calls++;return new Response('upstream-private-secret',{status,headers:{'Content-Type':'text/event-stream'}});
  }}),e=>e instanceof ChatApiError&&e.code==='http'&&e.status===status&&e.partialText===''&&pattern.test(e.message)&&!/upstream|private|secret/.test(e.message));
  assert.equal(calls,1);
 }
});
test('An expired manual retry is not converted into a new POST and explains how to recover',async()=>{
 let calls=0;
 await assert.rejects(streamChat({message:'Привет',requestId:'expired-request',retry:true,fetchImpl:async(_,init)=>{
  calls++;assert.equal(JSON.parse(init.body).retry,true);return new Response('private cached request detail',{status:410});
 }}),e=>e.code==='http'&&e.status===410&&e.message==='Срок восстановления ответа истёк. Переписка сохранена; начните новый поиск.');
 assert.equal(calls,1);
});
test('Health validates the real repository response',async()=>{
 for(const provider of ['fake','openai','codex_local','codex_private']) assert.deepEqual(await checkAgentHealth({fetchImpl:async()=>Response.json({status:'ok',llm_provider:provider})}),{status:'ok',llm_provider:provider});
 await assert.rejects(checkAgentHealth({fetchImpl:async()=>Response.json({status:'ok',provider:'fake'})}),ChatApiError);
});
test('Health recovery checks are uncached same-origin GETs and retain authentication failures',async()=>{
 await assert.rejects(checkAgentHealth({fetchImpl:async(url,init)=>{
  assert.equal(url,'/health');assert.equal(init.method,'GET');assert.equal(init.credentials,'same-origin');assert.equal(init.cache,'no-store');assert.equal(init.redirect,'error');
  return new Response('private-detail',{status:401});
 }}),e=>e.code==='http'&&e.status===401&&!e.message.includes('private-detail'));
 await assert.rejects(checkAgentHealth({fetchImpl:async()=>{throw new TypeError('private-token');}}),e=>e.code==='network'&&!e.message.includes('private-token'));
 await assert.rejects(checkAgentHealth({fetchImpl:async()=>{throw new DOMException('private-token','AbortError');}}),e=>e.code==='aborted'&&!e.message.includes('private-token'));
});

test('The next chat request carries its session and exposes all SSE metadata intact',async()=>{
 const events=[];const source={provider:'aviasales_data_api',seat_price_rub:11251,cache_hit:true};
 const done={session_id:'private-session_01',stage:'packages_shown',version:4};
 const chunks=['event: tool_result\ndata: '+JSON.stringify({name:'search_flights',metadata:{quotes:[source]}})+'\n\n','event: packages\ndata: '+JSON.stringify({options:[{number:1,total_rub:83234}],price_source:source})+'\n\n','event: delta\ndata: {"text":"Готово"}\n\n','event: done\ndata: '+JSON.stringify(done)+'\n\n'];
 const answer=await streamChat({message:'Выбираю первый',sessionId:done.session_id,onEvent:(type,data)=>events.push({type,data}),fetchImpl:async(_,init)=>{assert.deepEqual(JSON.parse(init.body),{message:'Выбираю первый',session_id:done.session_id});return response(chunks);}});
 assert.equal(answer.sessionId,done.session_id);assert.deepEqual(answer.done,done);assert.deepEqual(answer.events,events);assert.equal(answer.events[0].data.metadata.quotes[0].seat_price_rub,11251);assert.equal(answer.events[1].data.price_source.cache_hit,true);
});

test('Chat rejects unsafe session headers and malformed terminal metadata',async()=>{
 await assert.rejects(streamChat({message:'Привет',sessionId:'private\r\nheader',fetchImpl:()=>assert.fail()}),e=>e.code==='validation');
 await assert.rejects(streamChat({message:'Привет',fetchImpl:async()=>response(['event: done\ndata: {"session_id":"bad/identifier"}\n\n'])}),e=>e.code==='protocol');
});

const {requestCheckout,downloadCheckout}=await import('../dist/api.js');
test('Checkout places session ownership only in a header for reads and JSON for mutations',async()=>{
 const calls=[];const fakeFetch=async(path,init)=>{calls.push({path,init});return Response.json({status:'ready_for_demo_payment'});};
 await requestCheckout({path:'/api/checkout/carts',method:'POST',body:{option:1,version:3,session_id:'wrong'},sessionId:'private-session',fetchImpl:fakeFetch});
 await requestCheckout({path:'/api/checkout/carts/CART-safe',sessionId:'private-session',fetchImpl:fakeFetch});
 assert.deepEqual(JSON.parse(calls[0].init.body),{option:1,version:3,session_id:'private-session'});
 assert.equal(calls[0].init.headers['X-Session-ID'],undefined);assert.equal(calls[0].init.redirect,'error');
 assert.equal(calls[1].init.headers['X-Session-ID'],'private-session');assert.equal(calls[1].init.body,undefined);assert.equal(calls[1].init.cache,'no-store');assert.ok(calls.every(call=>!call.path.includes('private-session')));
});

test('Checkout downloads use private ownership and fixed safe filenames',async()=>{
 for(const [suffix,type,filename] of [['voucher','text/html','demo-voucher.html'],['calendar.ics','text/calendar','demo-trip.ics']]){
  const result=await downloadCheckout({path:'/api/checkout/orders/DEMO-private/'+suffix,sessionId:'private-session',fetchImpl:async(path,init)=>{
   assert.equal(init.headers['X-Session-ID'],'private-session');assert.equal(init.headers.Accept,type);assert.equal(init.redirect,'error');
   return new Response('document',{headers:{'Content-Type':type+'; charset=utf-8','Content-Disposition':'attachment; filename="DEMO-private-private-session.html"'}});
  }});
  assert.equal(await result.blob.text(),'document');assert.equal(result.filename,filename);assert.equal(result.contentType,type);assert.ok(!JSON.stringify(result).includes('private'));
 }
});

test('Checkout rejects cross-origin and unsupported paths without a request',async()=>{
 for(const path of ['https://example.com/api/checkout/carts','//example.com/api/checkout/carts','/api/checkout/carts?session_id=private','/api/checkout/orders/../voucher','/api/checkout/carts/%2Fsecret','/api/chat']){
  await assert.rejects(requestCheckout({path,sessionId:'private',fetchImpl:()=>assert.fail()}),e=>e.code==='validation');
 }
 await assert.rejects(requestCheckout({path:'/api/checkout/carts',sessionId:'private',fetchImpl:()=>assert.fail()}),e=>e.code==='validation');
 await assert.rejects(downloadCheckout({path:'/api/checkout/carts/CART-safe',sessionId:'private',fetchImpl:()=>assert.fail()}),e=>e.code==='validation');
});

test('Checkout never displays backend response errors or private identifiers',async()=>{
 await assert.rejects(requestCheckout({path:'/api/checkout/orders/DEMO-private',sessionId:'private',fetchImpl:async()=>Response.json({detail:'private DEMO-private secret'},{status:403})}),e=>e.code==='http'&&e.status===403&&!/private|secret/.test(e.message));
 await assert.rejects(requestCheckout({path:'/api/checkout/orders/DEMO-private',sessionId:'private',fetchImpl:async()=>new Response('raw-private-secret',{headers:{'Content-Type':'text/plain'}})}),e=>e.code==='protocol'&&!/private|secret/.test(e.message));
 await assert.rejects(requestCheckout({path:'/api/checkout/orders/DEMO-private',sessionId:'private',fetchImpl:async()=>{throw new Error('private-secret');}}),e=>e.code==='network'&&!/private|secret/.test(e.message));
 await assert.rejects(downloadCheckout({path:'/api/checkout/orders/DEMO-private/voucher',sessionId:'private',fetchImpl:async()=>new Response('{}',{headers:{'Content-Type':'application/json'}})}),e=>e.code==='protocol');
});

test('Structured brief is delivered intact; only the returned session id is reused',async()=>{
 const events=[];const brief={brief:{budget_rub:200000,adults:2},sources:{budget_rub:'explicit',adults:'inferred'},changes:[],needs:[]};
 await streamChat({message:'Мальдивы',sessionId:'actual-session',onEvent:(type,value)=>events.push([type,value]),fetchImpl:async(url,init)=>{
  assert.deepEqual(JSON.parse(init.body),{message:'Мальдивы',session_id:'actual-session'});
  return response(['event: brief\ndata: '+JSON.stringify(brief)+'\n\nevent: delta\ndata: {"text":"Бюджет в ответе 999000 — не бриф"}\n\nevent: done\ndata: {"session_id":"actual-session","version":2,"stage":"collecting"}\n\n']);
 }});
 assert.deepEqual(events.filter(([type])=>type!=='delta'),[['brief',brief],['done',{session_id:'actual-session',version:2,stage:'collecting'}]]);
});
test('Brief followed by an error never receives a successful done',async()=>{
 const events=[];
 await assert.rejects(streamChat({message:'Запрос',onEvent:(type)=>events.push(type),fetchImpl:async()=>response(['event: brief\ndata: {"brief":{"budget_rub":300000}}\n\nevent: error\ndata: {"message":"Откат"}\n\n'])}),e=>e.code==='agent');
 assert.deepEqual(events,['brief','error']);assert.ok(!events.includes('done'));
});
test('Session restoration reads the real snapshot and handles expiry or another session',async()=>{
 const snapshot={session_id:'trip-a',version:2,brief:{budget_rub:200000},sources:{budget_rub:'explicit'}};
 assert.deepEqual(await fetchSessionCanvas({sessionId:'trip-a',fetchImpl:async(url,init)=>{assert.equal(url,'/api/session/trip-a');assert.equal(init.method,undefined);return Response.json(snapshot);}}),snapshot);
 await assert.rejects(fetchSessionCanvas({sessionId:'trip-a',fetchImpl:async()=>new Response('',{status:404})}),e=>e.code==='session_missing');
 await assert.rejects(fetchSessionCanvas({sessionId:'trip-a',fetchImpl:async()=>Response.json({...snapshot,session_id:'trip-b'})}),e=>e.code==='protocol');
});

test('Checkout deadlines cover stalled headers and JSON bodies even when fetch ignores abort',async()=>{
 for(const phase of ['headers','body'])for(const [path,method,body] of [
  ['/api/checkout/orders/DEMO-private','GET',undefined],
  ['/api/checkout/carts/CART-private/travelers','PUT',{travelers:[]}],
  ['/api/checkout/carts/CART-private/pay','POST',{outcome:'approved',idempotency_key:'same-private-key'}],
 ]){
  let requestSignal;const never=new Promise(()=>{});
  await assert.rejects(requestCheckout({path,method,body,sessionId:'private-owner',timeoutMs:15,fetchImpl:async(_,init)=>{
   requestSignal=init.signal;
   if(phase==='headers')return never;
   return {ok:true,headers:new Headers({'Content-Type':'application/json'}),json:()=>never};
  }}),error=>error.code==='timeout'&&error.name!=='AbortError'&&!/private|CART-|DEMO-|нет заказа|заказ не создан/i.test(error.message));
  assert.equal(requestSignal.aborted,true,'The deadline must also abort the underlying request');
 }
});

test('A user cancellation stays distinct from timeout for stalled checkout headers or body',async()=>{
 for(const phase of ['headers','body']){
  const abort=new AbortController();let started;const ready=new Promise(resolve=>started=resolve),never=new Promise(()=>{});
  const pending=requestCheckout({path:'/api/checkout/orders/DEMO-test',sessionId:'owner-test',signal:abort.signal,timeoutMs:1000,fetchImpl:async()=>{
   if(phase==='headers'){started();return never;}
   return {ok:true,headers:new Headers({'Content-Type':'application/json'}),json:()=>{started();return never;}};
  }});
  await ready;abort.abort();await assert.rejects(pending,error=>error.code==='aborted'&&error.name==='AbortError');
 }
 const abort=new AbortController();abort.abort();
 await assert.rejects(requestCheckout({path:'/api/checkout/orders/DEMO-test',sessionId:'owner-test',signal:abort.signal,fetchImpl:()=>assert.fail('A cancelled request cannot start')}),error=>error.code==='aborted');
});

test('A document deadline includes its stalled blob, preserves safe filenames and allows retry',async()=>{
 for(const [suffix,type] of [['voucher','text/html'],['calendar.ics','text/calendar']]){
  let attempts=0,signal;
  const options={path:'/api/checkout/orders/DEMO-private/'+suffix,sessionId:'private-owner',timeoutMs:15,fetchImpl:async(_,init)=>{
   signal=init.signal;
   return ++attempts===1?{ok:true,headers:new Headers({'Content-Type':type}),blob:()=>new Promise(()=>{})}:new Response('document',{headers:{'Content-Type':type}});
  }};
  await assert.rejects(downloadCheckout(options),error=>error.code==='timeout'&&/повторите скачивание/i.test(error.message)&&!/private|DEMO-/.test(error.message));
  assert.equal(signal.aborted,true);
  const result=await downloadCheckout(options);assert.equal(await result.blob.text(),'document');assert.doesNotMatch(result.filename,/private/);
 }
});

test('Health body parsing also ends at its own deadline without misclassifying it as user cancellation',async()=>{
 let signal;
 await assert.rejects(checkAgentHealth({timeoutMs:15,fetchImpl:async(_,init)=>{
  signal=init.signal;return {ok:true,json:()=>new Promise(()=>{})};
 }}),error=>error.code==='timeout'&&error.name!=='AbortError');
 assert.equal(signal.aborted,true);
});

test('Checkout validation errors provide a safe actionable age hint without response inputs',async()=>{
 await assert.rejects(requestCheckout({path:'/api/checkout/carts/CART-private/travelers',method:'PUT',body:{travelers:[]},sessionId:'private-owner',fetchImpl:async()=>Response.json({detail:[{input:'PRIVATE PERSON AND BIRTH DATE',msg:'private-owner'}]},{status:422})}),error=>error.status===422&&/имена/.test(error.message)&&/даты рождения/.test(error.message)&&/возраст на день отправления/.test(error.message)&&!/PRIVATE|private-owner|CART-|input/.test(error.message));
});
