/** Small, dependency-free chat formatter. Input HTML and links remain text.
 * Supported output: p/br/strong/em/code/ol/ul/li, with numeric list attributes.
 * This deliberately does not implement headings, images, links or nested lists.
 */
const entities={'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'};
const escapeHtml=value=>value.replace(/[&<>"']/g,char=>entities[char]);
const blank=line=>/^[ \t]*$/.test(line);

function inline(text){
 const frames=[{marker:'',tag:'',parts:[]}];
 const append=html=>frames.at(-1).parts.push(html);
 let index=0,start=0;
 const flush=end=>{if(end>start)append(escapeHtml(text.slice(start,end)).replace(/\n/g,'<br>'));};
 while(index<text.length){
  const char=text[index];
  if(char==='\\'&&['\\','*','`'].includes(text[index+1])){
   flush(index);append(escapeHtml(text[index+1]));index+=2;start=index;continue;
  }
  if(char==='`'){
   const close=text.indexOf('`',index+1);
   if(close>index+1){
    flush(index);append('<code>'+escapeHtml(text.slice(index+1,close)).replace(/\n/g,' ')+'</code>');
    index=close+1;start=index;continue;
   }
   // Empty or unfinished code delimiters remain literal during streaming.
   if(close===index+1){index+=2;continue;}
   index++;continue;
  }
  if(char!=='*'){index++;continue;}
  let end=index+1;while(text[end]==='*')end++;
  const marker=text.slice(index,end),top=frames.at(-1);
  if(marker.length>2){index=end;continue;}
  const canClose=top.marker===marker&&index>0&&!/\s/.test(text[index-1]);
  const canOpen=end<text.length&&!/\s/.test(text[end])&&!frames.some(frame=>frame.marker===marker);
  if(canClose){
   flush(index);const frame=frames.pop();
   append('<'+frame.tag+'>'+frame.parts.join('')+'</'+frame.tag+'>');
   index=end;start=index;
  }else if(canOpen){
   flush(index);frames.push({marker,tag:marker.length===2?'strong':'em',parts:[]});
   index=end;start=index;
  }else index=end;
 }
 flush(text.length);
 // At most one strong and one em frame can be open. Unfinished markers stay visible.
 while(frames.length>1){const frame=frames.pop();append(frame.marker+frame.parts.join(''));}
 return frames[0].parts.join('');
}

function listItem(line){
 // Canonical numbers fit HTML's signed-integer counters. Longer/zero-padded
 // labels stay literal instead of being rounded, overflowed or renumbered.
 const match=/^ {0,3}(?:(0|[1-9][0-9]{0,8})[.)]|([-+*•]))[ \t]+(.*)$/.exec(line);
 if(!match)return null;
 return {kind:match[1]===undefined?'ul':'ol',number:match[1],text:match[3]};
}

export function formatAssistantText(input){
 const text=String(input??'').replace(/\r\n?/g,'\n');
 if(!text.trim())return '';
 const lines=text.split('\n'),blocks=[];let index=0;
 while(index<lines.length){
  if(blank(lines[index])){index++;continue;}
  const first=listItem(lines[index]);
  if(!first){
   const paragraph=[];
   while(index<lines.length&&!blank(lines[index])&&!listItem(lines[index]))paragraph.push(lines[index++]);
   blocks.push('<p>'+inline(paragraph.join('\n'))+'</p>');continue;
  }
  const items=[];
  while(index<lines.length){
   const item=listItem(lines[index]);if(!item||item.kind!==first.kind)break;
   const content=[item.text];index++;
   while(index<lines.length&&!blank(lines[index])&&!listItem(lines[index]))content.push(lines[index++].replace(/^ {1,3}/,''));
   items.push('<li'+(item.kind==='ol'?' value="'+item.number+'"':'')+'>'+inline(content.join('\n'))+'</li>');
   // Blank lines between items do not split the user's numbered list.
   let next=index;while(next<lines.length&&blank(lines[next]))next++;
   if(listItem(lines[next]??'')?.kind!==first.kind)break;
   index=next;
  }
  blocks.push('<'+first.kind+(first.kind==='ol'?' start="'+first.number+'"':'')+'>'+items.join('')+'</'+first.kind+'>');
 }
 return blocks.join('');
}
