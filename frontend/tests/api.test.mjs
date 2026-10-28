import test from 'node:test';
import assert from 'node:assert/strict';
import {streamChat,checkAgentHealth,ChatApiError} from '../dist/api.js';
const encoder=new TextEncoder();
function response(chunks){return new Response(new ReadableStream({start(c){for(const chunk of chunks)c.enqueue(typeof chunk==='string'?encoder.encode(chunk):chunk);c.close();}}),{headers:{'Content-Type':'text/event-stream'}});}
test('POST body matches the existing repository contract; UTF-8 chunks stream intact',async()=>{
 const raw=encoder.encode(': ping\r\nevent: delta\r\ndata: {"text":"Привет 🌴"}\r\n\r\nevent: done\r\ndata: {}\r\n\r\n');const deltas=[];
 const r=await streamChat({message:'В Казань',onDelta:(d,all)=>deltas.push(all),fetchImpl:async(url,init)=>{assert.equal(url,'/api/chat');assert.equal(init.method,'POST');assert.deepEqual(JSON.parse(init.body),{message:'В Казань'});return response(Array.from(raw,byte=>new Uint8Array([byte])));}});
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
test('Health validates the real repository response',async()=>{
 for(const provider of ['fake','openai','codex_local']) assert.deepEqual(await checkAgentHealth({fetchImpl:async()=>Response.json({status:'ok',llm_provider:provider})}),{status:'ok',llm_provider:provider});
 await assert.rejects(checkAgentHealth({fetchImpl:async()=>Response.json({status:'ok',provider:'fake'})}),ChatApiError);
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
