import http from 'node:http';
import https from 'node:https';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const here=path.dirname(fileURLToPath(import.meta.url));
const types={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.jpg':'image/jpeg','.svg':'image/svg+xml','.png':'image/png'};
const routes=[
 [/^\/api\/chat$/,['POST']],
 [/^\/health$/,['GET']],
 [/^\/api\/session\/[A-Za-z0-9_-]{1,64}$/,['GET','DELETE']],
 [/^\/api\/checkout\/carts$/,['POST']],
 [/^\/api\/checkout\/carts\/[A-Za-z0-9_-]{1,128}$/,['GET']],
 [/^\/api\/checkout\/carts\/[A-Za-z0-9_-]{1,128}\/travelers$/,['PUT']],
 [/^\/api\/checkout\/carts\/[A-Za-z0-9_-]{1,128}\/pay$/,['POST']],
 [/^\/api\/checkout\/orders\/[A-Za-z0-9_-]{1,128}$/,['GET']],
 [/^\/api\/checkout\/orders\/[A-Za-z0-9_-]{1,128}\/(?:voucher|calendar\.ics)$/,['GET']],
];
const MAX_BODY_BYTES=64*1024;
const fail=(res,status,message)=>res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}).end(JSON.stringify({message}));
function readBody(req){
 return new Promise((resolve,reject)=>{
  let size=0,tooLarge=false;const chunks=[];
  req.on('data',chunk=>{size+=chunk.length;if(size>MAX_BODY_BYTES){tooLarge=true;chunks.length=0;}else if(!tooLarge)chunks.push(chunk);});
  req.on('end',()=>tooLarge?reject(Object.assign(new Error('Body limit'),{status:413})):resolve(Buffer.concat(chunks)));
  req.on('aborted',()=>reject(Object.assign(new Error('Request aborted'),{status:400})));
  req.on('error',()=>reject(Object.assign(new Error('Invalid body'),{status:400})));
 });
}
export function createFrontendServer({root=path.join(here,'dist'),agentUrl=process.env.AGENT_URL||'http://127.0.0.1:8000',mode=process.env.CHAT_MODE||'api',localOnly=process.env.LOCAL_ONLY==='1',privateSiteMode=process.env.PRIVATE_SITE_MODE==='1',agentToken=process.env.CODEX_LOCAL_TOKEN||''}={}){
 const origin=new URL(agentUrl);
 if(!['http:','https:'].includes(origin.protocol)||origin.username||origin.password)throw new Error('AGENT_URL must use HTTP or HTTPS without credentials');
 const loopbackAgent=['127.0.0.1','localhost','[::1]'].includes(origin.hostname);
 // This mode is exclusively for the owner, behind HTTPS edge authentication.
 // The agent is reachable only within the private container network.
 if(privateSiteMode){
  if(localOnly||agentToken.length<32)throw new Error('Private owner mode requires a server-only token of at least 32 characters');
  if(!loopbackAgent&&origin.href!=='http://agent:8000/')throw new Error('Private owner mode requires the internal agent');
 }else if(agentToken&&(!localOnly||!loopbackAgent))throw new Error('Personal Codex token requires a loopback-only proxy');
 return http.createServer(async(req,res)=>{
  if(privateSiteMode){
   // Never trust forwarded headers to override these browser-origin boundaries.
   if(req.headers.host!=='201.8.90.64'||(req.headers.origin!==undefined&&req.headers.origin!=='https://201.8.90.64')||req.headers['sec-fetch-site']==='cross-site'){
    fail(res,403,'Доступ к личному интерфейсу запрещён.');return;
   }
  }
  if(localOnly){
   const allowed=new Set([`127.0.0.1:${req.socket.localPort}`,`localhost:${req.socket.localPort}`]);
   const host=req.headers.host,origin=req.headers.origin;
   if(!['127.0.0.1','::1','::ffff:127.0.0.1'].includes(req.socket.remoteAddress)||!allowed.has(host)||(origin&&origin!==`http://${host}`)||req.headers['sec-fetch-site']==='cross-site'){
    fail(res,403,'Личный Codex доступен только с этого компьютера.');return;
   }
  }
  // Match API routes before URL normalization. Encoded slashes, dot segments and query
  // strings must never expand the upstream allowlist or carry private credentials.
  const rawUrl=req.url||'';
  if(!rawUrl.startsWith('/')||rawUrl.startsWith('//')||rawUrl.includes('\\')){fail(res,400,'Некорректный адрес запроса.');return;}
  let url,pathname;try{url=new URL(rawUrl,'http://localhost');pathname=decodeURIComponent(url.pathname);}catch{fail(res,400,'Некорректный адрес запроса.');return;}
  const apiRequest=rawUrl==='/api'||rawUrl.startsWith('/api/')||pathname==='/api'||pathname.startsWith('/api/')||rawUrl==='/health'||rawUrl.startsWith('/health?')||pathname==='/health';
  const route=routes.find(([pattern])=>pattern.test(rawUrl));
  if(apiRequest||route){
   if(!route){fail(res,404,'Маршрут не найден.');return;}
   if(!route[1].includes(req.method)){res.setHeader('Allow',route[1].join(', '));fail(res,405,'Метод не поддерживается.');return;}
   if(Number(req.headers['content-length']||0)>MAX_BODY_BYTES){req.resume();fail(res,413,'Слишком большой запрос.');return;}
   let body;try{body=await readBody(req);}catch(error){if(!res.destroyed)fail(res,error.status||400,error.status===413?'Слишком большой запрос.':'Не удалось прочитать запрос.');return;}
   if(!['POST','PUT'].includes(req.method)&&body.length){fail(res,400,'Этот запрос не должен содержать данные.');return;}
   const headers={accept:rawUrl==='/api/chat'?'text/event-stream':'application/json'};
   if(agentToken)headers['x-local-codex-token']=agentToken;
   for(const name of ['accept','content-type','x-session-id'])if(typeof req.headers[name]==='string')headers[name]=req.headers[name];
   const transport=origin.protocol==='https:'?https:http;
   const upstream=transport.request(new URL(rawUrl,origin),{method:req.method,headers},incoming=>{
    const outgoing={'Content-Type':incoming.headers['content-type']||'application/json','Cache-Control':incoming.headers['cache-control']||'no-store','X-Accel-Buffering':'no','X-Content-Type-Options':'nosniff'};
    if(typeof incoming.headers['content-disposition']==='string')outgoing['Content-Disposition']=incoming.headers['content-disposition'];
    // Redirect locations are deliberately not forwarded: the session header is private.
    res.writeHead(incoming.statusCode||502,outgoing);res.flushHeaders();
    incoming.pipe(res);incoming.on('error',()=>res.destroy());
   });
   upstream.on('error',()=>{if(!res.headersSent)fail(res,502,'Сервис агента недоступен. Повторите попытку.');else res.destroy();});
   upstream.setTimeout(90000,()=>upstream.destroy(new Error('Agent timeout')));
   res.on('close',()=>{if(!res.writableEnded)upstream.destroy();});
   req.on('aborted',()=>upstream.destroy());
   upstream.end(body);return;
  }
  if(pathname==='/config.js'){
   if(!['GET','HEAD'].includes(req.method)){fail(res,405,'Метод не поддерживается.');return;}
   res.writeHead(200,{'Content-Type':types['.js'],'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});
   res.end(req.method==='HEAD'?undefined:'window.NA_MALDIVY_CONFIG = Object.freeze('+JSON.stringify({mode:mode==='demo'?'demo':'api',apiEnabled:true,chatEndpoint:'/api/chat',healthEndpoint:'/health'})+');');return;
  }
  if(req.method!=='GET'&&req.method!=='HEAD'){res.writeHead(405).end();return;}
  const file=path.resolve(root,'.'+(pathname==='/'?'/index.html':pathname));
  if(!file.startsWith(path.resolve(root)+path.sep)){res.writeHead(403).end();return;}
  try{const data=await fs.readFile(file);res.writeHead(200,{'Content-Type':types[path.extname(file)]||'application/octet-stream','Cache-Control':'no-cache','X-Content-Type-Options':'nosniff'});res.end(req.method==='HEAD'?undefined:data);}
  catch{res.writeHead(404,{'Content-Type':'text/plain; charset=utf-8'}).end('Страница не найдена');}
 });
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const server=createFrontendServer();const port=Number(process.env.PORT||5173);const host=process.env.HOST||'127.0.0.1';
 server.on('error',error=>{console.error('Frontend server:',error.message);process.exitCode=1;});
 server.listen(port,host,()=>console.log(`Local: http://localhost:${port}\nMode: ${process.env.CHAT_MODE||'api'} · API proxy ready`));
 const stop=()=>server.close(()=>process.exit(0));process.on('SIGINT',stop);process.on('SIGTERM',stop);
}
