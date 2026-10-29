import http from 'node:http';
import test from 'node:test';
import assert from 'node:assert/strict';
import {once} from 'node:events';
import {createFrontendServer} from '../server.mjs';
import {streamChat} from '../dist/api.js';
const listen=async server=>{server.listen(0,'127.0.0.1');await once(server,'listening');return 'http://127.0.0.1:'+server.address().port;};
const close=server=>new Promise(resolve=>{server.closeAllConnections();server.close(resolve);});

test('Browser message modules and stylesheet are static assets, never upstream routes',async t=>{
 let calls=0;const agent=http.createServer((req,res)=>{calls++;res.writeHead(404).end('{}');});
 const upstream=await listen(agent),web=createFrontendServer({agentUrl:upstream});const base=await listen(web);
 t.after(async()=>{await close(web);await close(agent);});
 for(const route of ['/api-app.js','/api-model.js','/api.js','/message-format.js','/api.css']){
  const response=await fetch(base+route);assert.equal(response.status,200,route);
  assert.match(response.headers.get('content-type'),route.endsWith('.css')?/text\/css/:/text\/javascript/);
  assert.ok((await response.text()).length>100);
 }
 assert.equal(calls,0,'Browser modules must not be proxied to the backend');
});

test('Same-origin configuration, assets, health and streaming proxy match the repository',async t=>{
 let received=null,agentDone=false;
 const agent=http.createServer(async(req,res)=>{
  if(req.url==='/health'){res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify({status:'ok',llm_provider:'fake'}));return;}
  if(req.url==='/api/session/test-session'){if(req.method==='DELETE'){res.writeHead(204).end();return;}res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify({session_id:'test-session',version:2,brief:{budget_rub:200000},sources:{budget_rub:'explicit'}}));return;}
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
 assert.equal((await fetch(base+'/api/session/test-session',{method:'DELETE'})).status,204);
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

test('Checkout proxy forwards private ownership and document headers, but no cookies or authorization',async t=>{
 const calls=[];
 const agent=http.createServer(async(req,res)=>{
  let body='';for await(const chunk of req)body+=chunk;calls.push({url:req.url,method:req.method,headers:req.headers,body});
  if(req.url.endsWith('/voucher')){res.writeHead(200,{'Content-Type':'text/html; charset=utf-8','Content-Disposition':'attachment; filename="demo-voucher.html"','Cache-Control':'no-store'}).end('<html>DEMO</html>');return;}
  if(req.method==='DELETE'){res.writeHead(204,{'Cache-Control':'no-store'}).end();return;}
  res.writeHead(200,{'Content-Type':'application/json','Cache-Control':'no-store'}).end(JSON.stringify({status:'ok'}));
 });
 const upstream=await listen(agent),web=createFrontendServer({agentUrl:upstream});const base=await listen(web);
 t.after(async()=>{await close(web);await close(agent);});
 const paths=[
  ['/api/checkout/carts','POST'],['/api/checkout/carts/CART-safe','GET'],['/api/checkout/carts/CART-safe/travelers','PUT'],['/api/checkout/carts/CART-safe/pay','POST'],['/api/checkout/orders/DEMO-safe','GET'],['/api/checkout/orders/DEMO-safe/calendar.ics','GET'],['/api/session/private-session','GET'],['/api/session/private-session','DELETE'],
 ];
 for(const [route,method] of paths){
  const response=await fetch(base+route,{method,headers:{'Accept':'application/json','Content-Type':'application/json','X-Session-ID':'private-session','Authorization':'secret-canary','Cookie':'token=secret-canary','X-Api-Key':'secret-canary'},...(['POST','PUT'].includes(method)?{body:JSON.stringify({session_id:'private-session'})}:{})});
  assert.ok(response.ok,route);await response.arrayBuffer();
 }
 const voucher=await fetch(base+'/api/checkout/orders/DEMO-safe/voucher',{headers:{'X-Session-ID':'private-session',Accept:'text/html'}});
 assert.equal(voucher.headers.get('content-disposition'),'attachment; filename="demo-voucher.html"');assert.equal(voucher.headers.get('cache-control'),'no-store');assert.match(await voucher.text(),/DEMO/);
 for(const call of calls){assert.equal(call.headers.authorization,undefined);assert.equal(call.headers.cookie,undefined);assert.equal(call.headers['x-api-key'],undefined);assert.equal(call.headers['x-session-id'],'private-session');assert.ok(!call.url.includes('?'));}
 assert.equal(calls[0].body,JSON.stringify({session_id:'private-session'}));
});

test('The proxy allowlist rejects unknown, encoded, query and wrong-method routes without upstream access',async t=>{
 let calls=0;
 const agent=http.createServer((req,res)=>{calls++;res.end('{}');});const upstream=await listen(agent),web=createFrontendServer({agentUrl:upstream});const base=await listen(web);
 t.after(async()=>{await close(web);await close(agent);});
 for(const route of ['/api/anything','/api/checkout/orders/id/secret','/api/checkout/carts/id?session_id=private','/api/chat?token=private','/health?private','/api/checkout/carts/%2Fprivate']){
  const r=await fetch(base+route);assert.equal(r.status,404,route);assert.ok(!(await r.text()).includes('private'));
 }
 for(const [route,method] of [['/api/checkout/carts','GET'],['/api/checkout/carts/id','DELETE'],['/api/checkout/orders/id/voucher','POST'],['/api/session/id','POST'],['/health','DELETE']]){
  const r=await fetch(base+route,{method});assert.equal(r.status,405,route);await r.arrayBuffer();
 }
 const rawStatus=await new Promise((resolve,reject)=>{const request=http.request(base,{path:'/api/checkout/carts/id/../pay'},response=>{response.resume();response.on('end',()=>resolve(response.statusCode));});request.on('error',reject);request.end();});
 assert.equal(rawStatus,404);assert.equal(calls,0);
});

test('The proxy enforces a 64 KiB body limit before contacting the backend',async t=>{
 let calls=0;const agent=http.createServer((req,res)=>{calls++;res.end('{}');});const upstream=await listen(agent),web=createFrontendServer({agentUrl:upstream});const base=await listen(web);
 t.after(async()=>{await close(web);await close(agent);});
 const r=await fetch(base+'/api/checkout/carts',{method:'POST',headers:{'Content-Type':'application/json'},body:'x'.repeat(65537)});
 assert.equal(r.status,413);await r.arrayBuffer();assert.equal(calls,0);
 const chunked=await new Promise((resolve,reject)=>{
  const request=http.request(base+'/api/chat',{method:'POST',headers:{'Transfer-Encoding':'chunked'}},response=>{let body='';response.on('data',chunk=>body+=chunk);response.on('end',()=>resolve({status:response.statusCode,body}));});
  request.on('error',reject);request.write('x'.repeat(32768));request.write('x'.repeat(32769));request.end();
 });
 assert.equal(chunked.status,413);assert.equal(calls,0);
});

test('Browser configuration never exposes environment credentials or upstream addresses',async t=>{
 const key='FAKE-CANARY-API-KEY';const previous=process.env.AVIASALES_API_TOKEN;process.env.AVIASALES_API_TOKEN=key;
 const web=createFrontendServer({agentUrl:'http://127.0.0.1:1',mode:'api'});const base=await listen(web);
 t.after(async()=>{await close(web);if(previous===undefined)delete process.env.AVIASALES_API_TOKEN;else process.env.AVIASALES_API_TOKEN=previous;});
 const config=await (await fetch(base+'/config.js')).text();assert.ok(!config.includes(key));assert.ok(!config.includes('127.0.0.1'));assert.ok(!config.includes('AVIASALES'));assert.match(config,/"chatEndpoint":"\/api\/chat"/);
 for(const route of ['/api/checkout/orders/DEMO-safe','/api/checkout/orders/DEMO-safe/voucher','/api/session/private-session']){
  const r=await fetch(base+route,{headers:{'X-Session-ID':'private-session'}});assert.equal(r.status,502);const body=await r.text();assert.ok(!body.includes(key));assert.ok(!body.includes('private-session'));assert.ok(!body.includes('DEMO-safe'));
 }
});
