import http from 'node:http';
import test from 'node:test';
import assert from 'node:assert/strict';
import {once} from 'node:events';
import {createFrontendServer} from '../server.mjs';

const listen=async server=>{server.listen(0,'127.0.0.1');await once(server,'listening');return 'http://127.0.0.1:'+server.address().port;};
const close=server=>new Promise(resolve=>{server.closeAllConnections();server.close(resolve);});

test('Personal proxy keeps its token on the server and blocks foreign sites and DNS rebinding',async t=>{
 const token='TEST-ONLY-PRIVATE-CODEX-TOKEN';let calls=0,received;
 const agent=http.createServer((req,res)=>{calls++;received=req.headers['x-local-codex-token'];res.setHeader('Content-Type','application/json');res.end(JSON.stringify({status:'ok',llm_provider:'codex_local'}));});
 const upstream=await listen(agent),web=createFrontendServer({agentUrl:upstream,localOnly:true,agentToken:token});
 const base=await listen(web);t.after(async()=>{await close(web);await close(agent);});
 const response=await fetch(base+'/health',{headers:{'X-Local-Codex-Token':'browser-cannot-replace-server-token'}});
 assert.equal(response.status,200);assert.equal((await response.json()).llm_provider,'codex_local');assert.equal(received,token);
 const config=await (await fetch(base+'/config.js')).text();assert.ok(!config.includes(token));assert.ok(!config.includes('agentToken'));
 const before=calls;
 for(const headers of [{Origin:'https://foreign.example'},{Host:'foreign.example'},{'Sec-Fetch-Site':'cross-site'}]){
  const r=await new Promise((resolve,reject)=>{const req=http.request(base+'/api/chat',{method:'POST',headers:{...headers,'Content-Type':'application/json'}},res=>{let body='';res.on('data',chunk=>body+=chunk);res.on('end',()=>resolve({status:res.statusCode,body}));});req.on('error',reject);req.end('{"message":"must not consume account"}');});
  assert.equal(r.status,403,JSON.stringify(headers));assert.ok(!r.body.includes(token));
 }
 assert.equal(calls,before);
 const own=await fetch(base+'/health',{headers:{Origin:base}});assert.equal(own.status,200);await own.arrayBuffer();
});

test('Private owner mode cannot start without its nonce or with a foreign upstream',()=>{
 const privateOptions={privateSiteMode:true,agentUrl:'http://agent:8000'};
 for(const agentToken of ['', 'short'])assert.throws(()=>createFrontendServer({...privateOptions,agentToken}),/server-only token/);
 const agentToken='synthetic-server-only-token-1234567890-test';
 for(const agentUrl of ['https://foreign.example','http://agent:8001','http://agent:8000/other']){
  assert.throws(()=>createFrontendServer({...privateOptions,agentUrl,agentToken}),/internal agent/);
 }
 assert.throws(()=>createFrontendServer({...privateOptions,agentToken,localOnly:true}),/server-only token/);
 assert.doesNotThrow(()=>createFrontendServer({...privateOptions,agentToken}));
 assert.throws(()=>createFrontendServer({agentUrl:'http://agent:8000',agentToken,privateSiteMode:false,localOnly:false}),/loopback-only/);
});

test('Private owner proxy validates host and origin before forwarding its server-only nonce',async t=>{
 const token='synthetic-server-only-token-1234567890-test';let calls=0,received;
 const agent=http.createServer((req,res)=>{calls++;received=req.headers;res.setHeader('Content-Type','application/json');res.end(JSON.stringify({status:'ok',llm_provider:'codex_private'}));});
 const upstream=await listen(agent),web=createFrontendServer({agentUrl:upstream,privateSiteMode:true,agentToken:token});
 const base=await listen(web);t.after(async()=>{await close(web);await close(agent);});
 const request=(path,headers={})=>new Promise((resolve,reject)=>{
  const req=http.request(base+path,{headers:{Host:'201.8.90.64',...headers}},res=>{
   let body='';res.on('data',chunk=>body+=chunk);res.on('end',()=>resolve({status:res.statusCode,body}));
  });req.on('error',reject);req.end();
 });
 const response=await request('/health',{'X-Local-Codex-Token':'browser-token','Authorization':'Basic browser-credential',Origin:'https://201.8.90.64','Sec-Fetch-Site':'same-origin'});
 assert.equal(response.status,200);assert.equal(JSON.parse(response.body).llm_provider,'codex_private');
 assert.equal(received['x-local-codex-token'],token);assert.equal(received.authorization,undefined);assert.equal(received.origin,undefined);
 const config=await request('/config.js');assert.equal(config.status,200);assert.ok(!config.body.includes(token));assert.ok(!config.body.includes('agentToken'));
 const before=calls;
 for(const headers of [
  {Host:'foreign.example','X-Forwarded-Host':'201.8.90.64'},
  {Host:'201.8.90.64:5173'},
  {Origin:'http://201.8.90.64'},
  {Origin:'https://foreign.example','X-Forwarded-Host':'201.8.90.64'},
  {Origin:'null'},
  {Origin:''},
  {'Sec-Fetch-Site':'cross-site'},
 ]){
  for(const path of ['/health','/config.js','/']){
   const denied=await request(path,headers);assert.equal(denied.status,403,JSON.stringify({path,headers}));assert.ok(!denied.body.includes(token));
  }
 }
 assert.equal(calls,before);
 const absentOrigin=await request('/health');assert.equal(absentOrigin.status,200);
});
