import http from 'node:http';
import test from 'node:test';
import assert from 'node:assert/strict';
import {once} from 'node:events';
import {createFrontendServer} from '../server.mjs';
import {streamChat} from '../dist/api.js';
const listen=async server=>{server.listen(0,'127.0.0.1');await once(server,'listening');return 'http://127.0.0.1:'+server.address().port;};
const close=server=>new Promise(resolve=>{server.closeAllConnections();server.close(resolve);});

test('Same-origin configuration, assets, health and streaming proxy match the repository',async t=>{
 let received=null,agentDone=false;
 const agent=http.createServer(async(req,res)=>{
  if(req.url==='/health'){res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify({status:'ok',llm_provider:'fake'}));return;}
  if(req.url==='/api/session/test-session'){res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify({session_id:'test-session',version:2,brief:{budget_rub:200000},sources:{budget_rub:'explicit'}}));return;}
  let body='';for await(const chunk of req)body+=chunk;received=JSON.parse(body);
  res.writeHead(200,{'Content-Type':'text/event-stream'});res.write('event: delta\ndata: {"text":"Первая часть"}\n\n');
  setTimeout(()=>{agentDone=true;res.end('event: delta\ndata: {"text":" и вторая"}\n\nevent: done\ndata: {}\n\n');},60);
 });
 const upstream=await listen(agent),web=createFrontendServer({agentUrl:upstream});const base=await listen(web);
 t.after(async()=>{await close(web);await close(agent);});
 assert.match(await (await fetch(base+'/')).text(),/О!дин запрос/);
 assert.match(await (await fetch(base+'/config.js')).text(),/"mode":"api"/);
 assert.match(await (await fetch(base+'/polish.css')).text(),/prefers-reduced-motion/);
 assert.deepEqual(await (await fetch(base+'/health')).json(),{status:'ok',llm_provider:'fake'});
 assert.deepEqual(await (await fetch(base+'/api/session/test-session')).json(),{session_id:'test-session',version:2,brief:{budget_rub:200000},sources:{budget_rub:'explicit'}});
 assert.equal((await fetch(base+'/api/session/test-session',{method:'DELETE'})).status,405);
 let sawFirst=false;
 const answer=await streamChat({message:'Проверка без ключа',endpoint:base+'/api/chat',onDelta:(_,all)=>{if(!sawFirst){assert.equal(agentDone,false,'SSE must stream before done');sawFirst=true;assert.equal(all,'Первая часть');}}});
 assert.deepEqual(received,{message:'Проверка без ключа'});assert.equal(answer.text,'Первая часть и вторая');
 assert.equal((await fetch(base+'/api/chat')).status,405);
 assert.equal((await fetch(base+'/%2e%2e%2f.env')).status,403);
});

test('An unavailable backend returns an explicit error, never demonstration data',async t=>{
 const web=createFrontendServer({agentUrl:'http://127.0.0.1:1'});const base=await listen(web);t.after(()=>close(web));
 await assert.rejects(streamChat({message:'Привет',endpoint:base+'/api/chat'}),error=>error.code==='http'&&error.status===502);
 const r=await fetch(base+'/health');assert.equal(r.status,502);
});
