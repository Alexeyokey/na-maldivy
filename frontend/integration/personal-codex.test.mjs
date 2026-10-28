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
