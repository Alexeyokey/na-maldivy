import test from 'node:test';
import assert from 'node:assert/strict';
import {streamChat,checkAgentHealth,fetchSessionCanvas,ChatApiError} from '../dist/api.js';
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
 assert.deepEqual(await checkAgentHealth({fetchImpl:async()=>Response.json({status:'ok',llm_provider:'fake'})}),{status:'ok',llm_provider:'fake'});
 await assert.rejects(checkAgentHealth({fetchImpl:async()=>Response.json({status:'ok',provider:'fake'})}),ChatApiError);
});
test('Structured brief is delivered intact; only the returned session id is reused',async()=>{
 const events=[];const brief={brief:{budget_rub:200000,adults:2},sources:{budget_rub:'explicit',adults:'inferred'},changes:[],needs:[]};
 await streamChat({message:'Мальдивы',sessionId:'actual-session',onEvent:(type,value)=>events.push([type,value]),fetchImpl:async(url,init)=>{
  assert.deepEqual(JSON.parse(init.body),{message:'Мальдивы',session_id:'actual-session'});
  return response(['event: brief\ndata: '+JSON.stringify(brief)+'\n\nevent: delta\ndata: {"text":"Бюджет в ответе 999000 — не бриф"}\n\nevent: done\ndata: {"session_id":"actual-session","version":2,"stage":"collecting"}\n\n']);
 }});
 assert.deepEqual(events,[['brief',brief],['done',{session_id:'actual-session',version:2,stage:'collecting'}]]);
});
test('Brief followed by an error never receives a successful done',async()=>{
 const events=[];
 await assert.rejects(streamChat({message:'Запрос',onEvent:(type)=>events.push(type),fetchImpl:async()=>response(['event: brief\ndata: {"brief":{"budget_rub":300000}}\n\nevent: error\ndata: {"message":"Откат"}\n\n'])}),e=>e.code==='agent');
 assert.deepEqual(events,['brief']);
});
test('Session restoration reads the real snapshot and handles expiry or another session',async()=>{
 const snapshot={session_id:'trip-a',version:2,brief:{budget_rub:200000},sources:{budget_rub:'explicit'}};
 assert.deepEqual(await fetchSessionCanvas({sessionId:'trip-a',fetchImpl:async(url,init)=>{assert.equal(url,'/api/session/trip-a');assert.equal(init.method,undefined);return Response.json(snapshot);}}),snapshot);
 await assert.rejects(fetchSessionCanvas({sessionId:'trip-a',fetchImpl:async()=>new Response('',{status:404})}),e=>e.code==='session_missing');
 await assert.rejects(fetchSessionCanvas({sessionId:'trip-a',fetchImpl:async()=>Response.json({...snapshot,session_id:'trip-b'})}),e=>e.code==='protocol');
});
