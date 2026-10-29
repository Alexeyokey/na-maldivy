import {counted,earliestSearchDate,formatDate,isTrainTrip,isoDay,tripAssumptions} from './trip-presentation.js';

const fields=['adults','start_date','nights','budget_rub','children_ages','pets'];
const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));

/** Render the search form without mutating its current trip or traveller values. */
export function renderPreferenceForm(state,{now=new Date(),errors={}}={}){
 const brief=state.brief||{},rail=isTrainTrip(state),ready=!state.stale&&!state.sessionLost&&state.stage!=='collecting';
 const assumed=tripAssumptions(ready?state:{...state,options:[],assumptions:[]}),minimum=earliestSearchDate(now);
 const value=field=>brief[field]??assumed.find(row=>row.field===field)?.value??'';
 const group=state.options?.[0]?.travelers||{},ages=brief.children_ages??group.children_ages??[],pets=brief.pets??group.pets??0;
 const field=(name,label,attributes,help)=>`<label class="api-preferences-field api-preferences-${name}" for="api-preferences-${name}"><span>${label}</span><input id="api-preferences-${name}" name="${name}" ${attributes} aria-describedby="api-preferences-${name}-help api-preferences-${name}-error"${errors[name]?' aria-invalid="true"':''}><small id="api-preferences-${name}-help">${help}</small><small id="api-preferences-${name}-error" class="api-field-error">${esc(errors[name]||'')}</small></label>`;
 const adults=field('adults','Взрослые',`type="number" min="1" max="12" step="1" value="${esc(value('adults'))}" required`,'От 1 до 12. Возраст детей задаётся отдельно.');
 const date=field('start_date',rail?'Дата отправления':'Дата вылета',`type="date" min="${minimum}" value="${isoDay(value('start_date'))||''}"`,`Не ранее ${formatDate(minimum)}. Пустое поле сохраняет текущие условия по датам.`);
 const nights=rail?'':field('nights','Ночи',`type="number" min="1" max="60" step="1" value="${esc(value('nights'))}" required`,'От 1 до 60 ночей.');
 const budget=field('budget_rub',rail?'Бюджет на билеты за всех в одну сторону, ₽':'Бюджет на всю поездку за всех, ₽',`type="text" inputmode="numeric" value="${esc(brief.budget_rub??'')}" placeholder="Без ограничения" maxlength="24"`,'Пустое поле снимает прежний лимит. Неоценённые доплаты остаются вне расчёта.');
 const children=field('children_ages','Возраст каждого ребёнка на дату отправления',`type="text" inputmode="text" value="${esc(ages.join(', '))}" placeholder="Например: 0, 5, 5" maxlength="60"`,'0 — младше года, максимум 8 детей до 18 лет. Через запятую, включая повторяющийся возраст. Пустое поле — без детей.');
 const animals=field('pets','Количество питомцев',`type="number" min="0" max="3" step="1" value="${esc(pets)}" required`,'От 0 до 3, 0 — без питомцев. Перевозку и размещение нужно подтвердить отдельно.');
 const hasChildren=ages.length>0,hasPets=Number(pets)>0,expanded=hasChildren||hasPets||Boolean(errors.children_ages||errors.pets);
 const summary=[hasChildren?counted(ages.length,'ребёнок','ребёнка','детей'):'Без детей',hasPets?counted(Number(pets),'питомец','питомца','питомцев'):'без питомцев'].join(' · ');
 return `<p class="about-text api-preferences-intro">Измените условия — сервер заново подберёт варианты. Другие пожелания сохранятся, оформление не начнётся.</p>
<form id="api-preferences-form" class="api-preferences-form api-preferences-compact" novalidate data-transport="${rail?'train':'flight'}">
${adults}${date}${nights}${budget}
<details class="api-preferences-extra"${expanded?' open':''}><summary><span>Дети и питомцы</span><small>${esc(summary)}</small></summary><div class="api-preferences-extra-fields">${children}${animals}</div></details>
<p class="demo-data-note api-preferences-note">Все значения формы применятся, в том числе отсутствие детей, питомцев и лимита бюджета. Если раздел свёрнут, его значения сохраняются.</p>
<button class="primary-button" type="submit">Применить и пересчитать</button>
</form>`;
}

/** Reveal a hidden invalid field before focus; preserve all in-progress form values. */
export function revealPreferenceErrors(form,errors={}){
 for(const name of fields){
  const input=form.querySelector?.(`[name="${name}"]`),message=form.querySelector?.(`#api-preferences-${name}-error`);
  if(input){if(errors[name])input.setAttribute('aria-invalid','true');else input.removeAttribute('aria-invalid');}
  if(message)message.textContent=errors[name]||'';
  if(errors[name]&&input){const details=input.closest?.('details');if(details)details.open=true;}
 }
 const first=fields.find(name=>errors[name]&&form.querySelector?.(`[name="${name}"]`));
 if(!first)return null;
 form.querySelector(`[name="${first}"]`)?.focus?.();
 return String(errors[first]);
}
