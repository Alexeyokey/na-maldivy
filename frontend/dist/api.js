/** Transport for the repository's current POST /api/chat SSE contract. */
export class ChatApiError extends Error {
  constructor(message, {code='network',partialText='',status}={}) {
    super(message);this.name=code==='aborted'?'AbortError':'ChatApiError';
    this.code=code;this.partialText=partialText;this.status=status;
  }
}
export async function streamChat({message,sessionId,endpoint='/api/chat',signal,onDelta=()=>{},onEvent=()=>{},fetchImpl=globalThis.fetch}) {
  if(typeof message!=='string'||!message.trim()||message.length>4000)throw new ChatApiError('Напишите сообщение длиной от 1 до 4000 символов.',{code:'validation'});
  if(sessionId!==undefined&&sessionId!==null&&!isSessionId(sessionId))throw new ChatApiError('Не удалось продолжить диалог. Начните новый поиск.',{code:'validation'});
  let text='',reader,finished=false,done=null;
  const events=[];let resultSessionId=sessionId||null;
  try {
    const response=await fetchImpl(endpoint,{method:'POST',headers:{'Content-Type':'application/json','Accept':'text/event-stream'},body:JSON.stringify({message,...(sessionId?{session_id:sessionId}:{})}),signal});
    if(!response.ok)throw new ChatApiError(response.status===422?'Сервер не принял сообщение. Проверьте его длину.':'Агент сейчас недоступен. Попробуйте ещё раз.',{code:'http',status:response.status});
    if(!response.headers.get('content-type')?.includes('text/event-stream')||!response.body)throw new ChatApiError('Сервер вернул ответ в неподдерживаемом формате.',{code:'protocol'});
    reader=response.body.getReader();const decoder=new TextDecoder();let pending='',event='message',data=[];
    const dispatch=()=>{
      const type=event,payload=data.join('\n');event='message';data=[];
      if(!payload)return;
      let value;try{value=JSON.parse(payload);}catch{throw new ChatApiError('Не удалось прочитать ответ агента.',{code:'protocol'});}
      if(value===null||typeof value!=='object'||Array.isArray(value))throw new ChatApiError('Агент вернул некорректное событие.',{code:'protocol'});
      events.push({type,data:value});onEvent(type,value);
      if(type==='delta'){
        if(typeof value.text!=='string')throw new ChatApiError('Агент вернул некорректный фрагмент ответа.',{code:'protocol'});
        text+=value.text;onDelta(value.text,text);
      }else if(type==='done'){
        if(value.session_id!==undefined&&!isSessionId(value.session_id))throw new ChatApiError('Не удалось сохранить диалог.',{code:'protocol'});
        done=value;resultSessionId=value.session_id||resultSessionId;finished=true;
      }
      else if(type==='error')throw new ChatApiError(typeof value.message==='string'?value.message:'Агент не смог закончить ответ.',{code:'agent'});
    };
    const line=value=>{
      if(!value){dispatch();return;}
      if(value.startsWith(':'))return;
      const colon=value.indexOf(':');const field=colon<0?value:value.slice(0,colon);let body=colon<0?'':value.slice(colon+1);if(body.startsWith(' '))body=body.slice(1);
      if(field==='event')event=body;else if(field==='data')data.push(body);
    };
    while(!finished){
      if(signal?.aborted)throw new ChatApiError('Ответ остановлен.',{code:'aborted'});
      const chunk=await reader.read();pending+=decoder.decode(chunk.value,{stream:!chunk.done});
      let match;
      while((match=/\r\n|\r|\n/.exec(pending))){
        if(match[0]==='\r'&&match.index===pending.length-1&&!chunk.done)break;
        const value=pending.slice(0,match.index);pending=pending.slice(match.index+match[0].length);line(value);if(finished)break;
      }
      if(chunk.done){if(pending)line(pending);dispatch();break;}
    }
    if(!finished)throw new ChatApiError('Связь прервалась до завершения ответа. Полученный текст сохранён.',{code:'incomplete'});
    return {text,sessionId:resultSessionId,done,events};
  }catch(error){
    if(error instanceof ChatApiError){error.partialText=text;throw error;}
    throw new ChatApiError(signal?.aborted||error?.name==='AbortError'?'Ответ остановлен.':'Не удалось связаться с агентом. Проверьте, запущен ли сервер.',{code:signal?.aborted||error?.name==='AbortError'?'aborted':'network',partialText:text});
  }finally{if(reader){try{await reader.cancel();}catch{}reader.releaseLock();}}
}
export async function checkAgentHealth({endpoint='/health',signal,fetchImpl=globalThis.fetch}={}){
  try{
    const response=await fetchImpl(endpoint,{signal,headers:{Accept:'application/json'}});
    if(!response.ok)throw new Error('Unavailable');
    const value=await response.json();
    if(value.status!=='ok'||!['fake','openai','codex_local','codex_private'].includes(value.llm_provider))throw new Error('Invalid health');
    return value;
  }catch(error){throw new ChatApiError(signal?.aborted?'Проверка остановлена.':'Сервер агента недоступен.',{code:signal?.aborted?'aborted':'network'});}
}

const isSessionId=value=>typeof value==='string'&&/^[A-Za-z0-9_-]{1,64}$/.test(value);
const checkoutRoutes=[
  [/^\/api\/checkout\/carts$/,['POST']],
  [/^\/api\/checkout\/carts\/[A-Za-z0-9_-]{1,128}$/,['GET']],
  [/^\/api\/checkout\/carts\/[A-Za-z0-9_-]{1,128}\/travelers$/,['PUT']],
  [/^\/api\/checkout\/carts\/[A-Za-z0-9_-]{1,128}\/pay$/,['POST']],
  [/^\/api\/checkout\/orders\/[A-Za-z0-9_-]{1,128}$/,['GET']],
  [/^\/api\/checkout\/orders\/[A-Za-z0-9_-]{1,128}\/(?:voucher|calendar\.ics)$/,['GET']],
];
function checkoutRequest({path,method='GET',body,sessionId,signal}){
  const route=typeof path==='string'&&checkoutRoutes.find(([pattern])=>pattern.test(path));
  if(!route||!route[1].includes(method)||!isSessionId(sessionId))throw new ChatApiError('Не удалось выполнить оформление. Повторите поиск.',{code:'validation'});
  const headers={Accept:'application/json'};
  const init={method,headers,signal,credentials:'same-origin',cache:'no-store'};
  if(method==='GET'){
    if(body!==undefined)throw new ChatApiError('Некорректный запрос оформления.',{code:'validation'});
    headers['X-Session-ID']=sessionId;
  }else{
    if(body!==undefined&&(body===null||typeof body!=='object'||Array.isArray(body)))throw new ChatApiError('Некорректный запрос оформления.',{code:'validation'});
    headers['Content-Type']='application/json';init.body=JSON.stringify({...body,session_id:sessionId});
  }
  return init;
}
function checkoutError(error,signal){
  if(error instanceof ChatApiError)return error;
  return new ChatApiError(signal?.aborted||error?.name==='AbortError'?'Оформление остановлено.':'Не удалось связаться с сервисом оформления. Повторите попытку.',{code:signal?.aborted||error?.name==='AbortError'?'aborted':'network'});
}
async function checkoutFetch(path,init,fetchImpl){
  // Avoid redirects sending the private session header to a different origin.
  const response=await fetchImpl(path,{...init,redirect:'error'});
  if(!response.ok)throw new ChatApiError(response.status===409?'Условия поездки изменились. Повторите поиск и выберите вариант заново.':'Не удалось выполнить оформление. Проверьте данные и повторите попытку.',{code:'http',status:response.status});
  return response;
}
/** Session identifiers stay in the private header or JSON body, never in a query string. */
export async function requestCheckout({path,method='GET',body,sessionId,fetchImpl=globalThis.fetch,signal}){
  try{
    const init=checkoutRequest({path,method,body,sessionId,signal});
    const response=await checkoutFetch(path,init,fetchImpl);
    if(!response.headers.get('content-type')?.includes('application/json'))throw new ChatApiError('Сервис оформления вернул неподдерживаемый ответ.',{code:'protocol'});
    let value;try{value=await response.json();}catch{throw new ChatApiError('Не удалось прочитать результат оформления.',{code:'protocol'});}
    if(value===null||typeof value!=='object'||Array.isArray(value))throw new ChatApiError('Не удалось прочитать результат оформления.',{code:'protocol'});
    return value;
  }catch(error){throw checkoutError(error,signal);}
}
/** The displayed filename is fixed by document type, not by an order/session identifier. */
export async function downloadCheckout({path,sessionId,fetchImpl=globalThis.fetch,signal}){
  try{
    const init=checkoutRequest({path,sessionId,signal});
    const calendar=path.endsWith('/calendar.ics'),voucher=path.endsWith('/voucher');
    if(!calendar&&!voucher)throw new ChatApiError('Выберите документ для скачивания.',{code:'validation'});
    const contentType=calendar?'text/calendar':'text/html';init.headers.Accept=contentType;
    const response=await checkoutFetch(path,init,fetchImpl);
    if(!response.headers.get('content-type')?.toLowerCase().startsWith(contentType))throw new ChatApiError('Документ вернулся в неподдерживаемом формате.',{code:'protocol'});
    return {blob:await response.blob(),filename:calendar?'demo-trip.ics':'demo-voucher.html',contentType};
  }catch(error){throw checkoutError(error,signal);}
}
