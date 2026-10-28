import http from 'node:http';
import https from 'node:https';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const here=path.dirname(fileURLToPath(import.meta.url));
const types={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.jpg':'image/jpeg','.svg':'image/svg+xml','.png':'image/png'};
export function createFrontendServer({root=path.join(here,'dist'),agentUrl=process.env.AGENT_URL||'http://127.0.0.1:8000',mode=process.env.CHAT_MODE||'api'}={}){
 const origin=new URL(agentUrl);
 if(!['http:','https:'].includes(origin.protocol))throw new Error('AGENT_URL must use HTTP or HTTPS');
 return http.createServer(async(req,res)=>{
  let pathname;try{pathname=decodeURIComponent(new URL(req.url,'http://localhost').pathname);}catch{res.writeHead(400).end('Bad request');return;}
  if(pathname==='/config.js'){
   res.writeHead(200,{'Content-Type':types['.js'],'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});
   res.end('window.NA_MALDIVY_CONFIG = Object.freeze('+JSON.stringify({mode:mode==='demo'?'demo':'api',apiEnabled:true,chatEndpoint:'/api/chat',healthEndpoint:'/health'})+');');return;
  }
  if(pathname==='/api/chat'||pathname==='/health'){
   if(req.method!==(pathname==='/health'?'GET':'POST')){res.writeHead(405).end('Method not allowed');return;}
   const headers={accept:pathname==='/api/chat'?'text/event-stream':'application/json'};
   if(req.headers['content-type'])headers['content-type']=req.headers['content-type'];
   const transport=origin.protocol==='https:'?https:http;
   const upstream=transport.request(new URL(pathname,origin),{method:req.method,headers},incoming=>{
    res.writeHead(incoming.statusCode||502,{'Content-Type':incoming.headers['content-type']||'application/json','Cache-Control':'no-cache, no-transform','X-Accel-Buffering':'no'});
    incoming.pipe(res);incoming.on('error',()=>res.destroy());
   });
   upstream.on('error',()=>{if(!res.headersSent)res.writeHead(502,{'Content-Type':'application/json'}).end(JSON.stringify({message:'Сервис agent недоступен. Запустите backend.'}));else res.destroy();});
   upstream.setTimeout(90000,()=>upstream.destroy(new Error('Agent timeout')));
   res.on('close',()=>{if(!res.writableEnded)upstream.destroy();});
   req.on('aborted',()=>upstream.destroy());
   req.pipe(upstream);return;
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
