/** Transport for the repository's current POST /api/chat SSE contract. */
export class ChatApiError extends Error {
  constructor(message, {code='network',partialText='',status}={}) {
    super(message);this.name=code==='aborted'?'AbortError':'ChatApiError';
    this.code=code;this.partialText=partialText;this.status=status;
  }
}
export async function streamChat({message,sessionId,endpoint='/api/chat',signal,onDelta=()=>{},onEvent=()=>{},fetchImpl=globalThis.fetch}) {
  if(typeof message!=='string'||!message.trim()||message.length>4000)throw new ChatApiError('Напишите сообщение длиной от 1 до 4000 символов.',{code:'validation'});
  if(sessionId!==undefined&&sessionId!==null&&(typeof sessionId!=='string'||!sessionId||sessionId.length>64))throw new ChatApiError('Не удалось восстановить сессию поездки.',{code:'validation'});
  let text='',reader,finished=false;
  try {
    const response=await fetchImpl(endpoint,{method:'POST',headers:{'Content-Type':'application/json','Accept':'text/event-stream'},body:JSON.stringify({message,...(sessionId?{session_id:sessionId}:{})}),signal});
    if(!response.ok)throw new ChatApiError(response.status===422?'Сервер не принял сообщение. Проверьте его длину.':'Агент сейчас недоступен. Попробуйте ещё раз.',{code:'http',status:response.status});
    if(!response.headers.get('content-type')?.includes('text/event-stream')||!response.body)throw new ChatApiError('Сервер вернул ответ в неподдерживаемом формате.',{code:'protocol'});
    reader=response.body.getReader();const decoder=new TextDecoder();let pending='',event='message',data=[];
    const dispatch=()=>{
      const type=event,payload=data.join('\n');event='message';data=[];
      if(!payload)return;
      let value;try{value=JSON.parse(payload);}catch{throw new ChatApiError('Не удалось прочитать ответ агента.',{code:'protocol'});}
      if(type==='delta'){
        if(typeof value.text!=='string')throw new ChatApiError('Агент вернул некорректный фрагмент ответа.',{code:'protocol'});
        text+=value.text;onDelta(value.text,text);
      }else if(type==='done'){
        try{onEvent(type,value);}catch(error){throw new ChatApiError(error.message||'Некорректная версия холста поездки.',{code:'protocol'});}
        finished=true;
      }
      else if(type==='error')throw new ChatApiError(typeof value.message==='string'?value.message:'Агент не смог закончить ответ.',{code:'agent'});
      else if(type==='brief'){
        try{onEvent(type,value);}catch(error){throw new ChatApiError(error.message||'Агент вернул некорректный холст поездки.',{code:'protocol'});}
      }
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
    return {text};
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
    if(value.status!=='ok'||!['fake','openai'].includes(value.llm_provider))throw new Error('Invalid health');
    return value;
  }catch(error){throw new ChatApiError(signal?.aborted?'Проверка остановлена.':'Сервер агента недоступен.',{code:signal?.aborted?'aborted':'network'});}
}
export async function fetchSessionCanvas({sessionId,endpoint='/api/session',signal,fetchImpl=globalThis.fetch}){
 if(typeof sessionId!=='string'||!sessionId||sessionId.length>64)throw new ChatApiError('Некорректная сессия поездки.',{code:'validation'});
 const response=await fetchImpl(endpoint+'/'+encodeURIComponent(sessionId),{signal,headers:{Accept:'application/json'}});
 if(!response.ok)throw new ChatApiError(response.status===404?'Сессия поездки больше не существует.':'Не удалось обновить холст поездки.',{code:response.status===404?'session_missing':'http',status:response.status});
 const value=await response.json();
 if(value?.session_id!==sessionId||!value.brief||typeof value.brief!=='object'||Array.isArray(value.brief))throw new ChatApiError('Сервер вернул другой холст поездки.',{code:'protocol'});
 return value;
}
