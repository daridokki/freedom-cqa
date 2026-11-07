const S = { state: null, checklist: null, activeEvaluation: null, page: 'dashboard', jobId: null, analytics: null };
let polling = null;
const $ = q => document.querySelector(q);
const esc = x => String(x ?? '').replace(/[&<>"']/g, v => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[v]));
const formatAnswer = v => v === 'yes' ? 'ДА' : v === 'no' ? 'НЕТ' : v === 'na' ? 'N/A' : String(v);
const num = n => n === null || n === undefined ? 'N/A' : Number(n).toFixed(2);

async function api(path, opts={}) {
  const resp = await fetch(path, {headers:{'Content-Type':'application/json'},...opts});
  let body; try {body = await resp.json();} catch {body = {detail:'Invalid server response'};}
  if(!resp.ok) throw new Error(typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body));
  return body;
}
function report(msg,error=false){const n=$('#notice');n.textContent=msg;n.className=error?'error':'';window.scrollTo({top:0,behavior:'smooth'});}
function clearNotice(){$('#notice').textContent='';$('#notice').className='';}
function busy(btn, on){if(btn){btn.disabled=on;btn.classList.toggle('loader',on);}}

async function loadState(){
  S.state=await api('/api/state');
  if(!S.checklist || !S.state.checklists.some(x=>x.id===S.checklist.id)) S.checklist=S.state.current_checklist;
  $('#llmStatus').textContent=S.state.llm_configured?'● LLM подключена':'○ LLM не настроена';
  $('#llmStatus').className='tag '+(S.state.llm_configured?'success':'warn');
  renderStats();renderDialogs();renderEvalList();renderChecklistVersions();renderJobs();
  await Promise.all([renderDashboard(), renderReviewQueue()]);
}
function showPage(page){
  S.page=page;
  for(const p of document.querySelectorAll('.page')) p.classList.toggle('hidden',p.id!==page);
  for(const b of document.querySelectorAll('.nav')) b.classList.toggle('active',b.dataset.page===page);
  const headers={dashboard:['Обзор','Аналитика качества клиентских диалогов'],
    dialogs:['Диалоги','Оценка общения с клиентами по редактируемому чек-листу'],
    results:['Результаты','Аргументация, фрагменты транскрипта и апелляции'],
    checklist:['Конструктор чек-листа','Настраиваемые критерии, шкалы, веса и N/A'],
    reviews:['Апелляции','Очередь возражений и решения проверяющего']};
  $('#pageTitle').textContent=headers[page][0];$('#pageSubtitle').textContent=headers[page][1];
  if(page==='checklist') renderChecklistEditor();
  if(page==='dashboard')renderDashboard();
  if(page==='reviews')renderReviewQueue();
  clearNotice();
}
function renderStats(){
  const d=S.state.conversations, e=S.state.evaluations;
  const cards=[['Всего диалогов',d.length],['Русский / RU',d.filter(x=>x.language==='RU').length],
    ['Казахский / KZ',d.filter(x=>x.language==='KZ').length],['Английский / EN',d.filter(x=>x.language==='EN').length]];
  $('#stats').innerHTML=cards.map(([title,value])=>`<div class="stat"><span>${title}</span><strong>${value}</strong></div>`).join('');
  const current=$('#datasetFilter').value;
  const datasets=[...new Set(d.map(x=>x.dataset_group_id||'manual'))].sort();
  $('#datasetFilter').innerHTML='<option value="">Все наборы</option>'+datasets.map(x=>`<option value="${esc(x)}" ${x===current?'selected':''}>${esc(x)}</option>`).join('');
}
function renderDialogs(){
  if(!S.state)return;
  const q=$('#search').value.toLowerCase(),lang=$('#languageFilter').value,grp=$('#datasetFilter').value,chan=$('#channelFilter').value;
  const dialogs=S.state.conversations.filter(c=>(!lang||lang===c.language)&&(!grp||grp===c.dataset_group_id)&&(!chan||chan===c.channel)&&c.conversation_id.toLowerCase().includes(q));
  $('#dialogList').innerHTML=dialogs.length?dialogs.map(c=>`<div class="list-row"><div><div class="name mono">${esc(c.conversation_id)}</div>
    <div class="meta">${esc(c.language)} · ${esc(c.channel)} · ${esc(c.dataset_group_id||'manual')} · ${c.evaluations} оценок</div></div>
    <div class="row-actions"><span class="tag">${esc(c.language)}</span><button class="ghost small" data-dialog="${esc(c.conversation_id)}" data-act="open-dialog">Просмотр</button>
      <button class="primary small" data-dialog="${esc(c.conversation_id)}" data-act="evaluate">Оценить</button></div></div>`).join(''):'<div class="empty">Диалогов пока нет. Импортируйте данные или откройте учебный пример.</div>';
}
function renderEvalList(){
  if(!S.state)return;
  const list=S.state.evaluations;
  $('#evaluationList').innerHTML=list.length?list.map(e=>`<div class="list-row ${S.activeEvaluation===e.id?'selected':''}" data-eid="${esc(e.id)}">
    <div class="name mono">${esc(e.conversation_id)}</div><div class="meta">Чек-лист v${e.checklist_version} · ${esc(e.mode)} · ${esc(new Date(e.created_at).toLocaleString('ru-RU'))}</div>
    ${e.pending_appeals?`<span class="tag warn">Апелляции: ${e.pending_appeals}</span>`:''}</div>`).join(''):'<div class="empty">Пока нет оценок. Оцените диалог или откройте учебный пример.</div>';
}
async function openEval(id){
  S.activeEvaluation=id;showPage('results');
  $('#evaluationDetail').innerHTML='<div class="empty">Загрузка…</div>';
  try{const data=await api('/api/evaluations/'+encodeURIComponent(id));renderEvaluation(data);renderEvalList();}
  catch(e){report(e.message,true);}
}
function validAudio(u){try{const url=new URL(u);return ['https:','http:'].includes(url.protocol)}catch{return false}}
function answerOptions(c, current){
  let vals=c.type==='scale'?c.levels.map(String):['yes','no'];
  if(c.allow_na)vals.push('na');
  return vals.map(v=>`<option value="${esc(v)}" ${String(current)===v?'selected':''}>${formatAnswer(v)}</option>`).join('');
}
function scoreBlock(s,label){return `<div class="score-block"><div class="fine">${label}</div><div class="score">${num(s)} ${s===null?'<small>не рассчитывается</small>':'<small>/ 100</small>'}</div></div>`}
function renderEvaluation(d){
  const cfg=Object.fromEntries(d.checklist.criteria.map(c=>[c.id,c]));
  const changed=d.appeals.some(a=>a.status==='resolved'&&a.decision==='change');
  const rows=d.final.criteria.map(r=>{
    const c=cfg[r.criterion_id],ap=d.appeals.filter(a=>a.criterion_id===r.criterion_id);
    return `<div class="criteria-result"><div class="criterion-head"><strong>${esc(r.criterion_id)} · ${esc(c.name)}</strong>
      <span class="tag ${c.type==='flag'?(r.answer==='yes'?'danger':''):r.answer==='no'||r.answer===0?'warn':''}">${esc(formatAnswer(r.answer))}${r.effective_points===null?'':` · ${num(r.effective_points)} / ${num(r.effective_weight)}`}</span></div>
      <div class="criterion-body"><p>${esc(r.reason)}</p><div class="fine">Вес: ${esc(c.weight)} · ${r.answer==='na'?'Исключён из расчёта (N/A)':r.effective_weight===null?'Без баллов':'С учётом перераспределения: '+num(r.effective_weight)}</div>
      <div>${r.evidence_indices.length?r.evidence_indices.map(i=>`<button class="evidence" data-ev-index="${i}">↗ Реплика #${i+1}</button>`).join(''):'<span class="fine">В ответе нет ссылки на конкретную реплику</span>'}</div>
      <details><summary>Подать апелляцию (${ap.length})</summary><form class="appeal-form" data-criterion="${esc(c.id)}">
      <label>Причина апелляции</label><textarea name="comment" required minlength="2" placeholder="Почему оценка может быть ошибочной?"></textarea>
      <button class="ghost small" type="submit">Сохранить апелляцию</button></form></details></div></div>`;
  }).join('');
  const turns=d.conversation.transcript.map((t,i)=>`<div class="turn ${esc(t.speaker)}" id="turn-${i}"><div class="turn-title">#${i+1} · ${esc(t.timestamp)} · ${esc(t.speaker)}</div><p>${esc(t.text)}</p></div>`).join('');
  const appeals=d.appeals.map(a=>{
    const c=cfg[a.criterion_id],active=d.final.criteria.find(r=>r.criterion_id===a.criterion_id);
    return `<div class="appeal"><div class="action-row"><b>${esc(c?.name||a.criterion_id)}</b><span class="tag ${a.status==='pending'?'warn':''}">${a.status==='pending'?'Ожидает решения':'Решено'}</span></div>
      <p>${esc(a.comment)}</p>${a.status==='pending'?`<form class="appeal-form" data-appeal="${esc(a.id)}">
      <label>Решение проверяющего</label><select name="decision"><option value="confirm">Подтвердить оценку</option><option value="change">Изменить оценку</option></select>
      <label>Новая оценка (если выбрано изменение)</label><select name="answer">${answerOptions(c,active.answer)}</select>
      <label>Реплики-доказательства (необязательно; номера через запятую, начиная с 1)</label><input name="evidence" type="text" placeholder="Например, 2, 4" />
      <label>Причина решения</label><textarea name="reviewer_note" required minlength="2"></textarea>
      <button class="primary small" type="submit">Сохранить решение</button></form>`:
      `<div class="fine">${a.decision==='confirm'?'Оценка подтверждена':'Новая оценка: '+formatAnswer(JSON.parse(a.decided_answer))} · ${esc(a.reviewer_note)}</div>`}</div>`;
  }).join('');
  $('#evaluationDetail').innerHTML=`<div class="eval-head"><div><h2 class="mono">${esc(d.conversation.conversation_id)}</h2><div class="fine">${esc(d.conversation.language)} · ${esc(d.conversation.channel)} · v${d.checklist.version} · ${esc(d.mode)}</div></div>
    <div>${scoreBlock(d.final.total_score,'Итоговый балл')}</div></div>
    ${d.mode==='curated-demo'?'<p class="fine" style="margin-top:8px">Учебный пример — оценки внесены вручную, не созданы нейросетью.</p>':''}
    ${d.mode==='manual'?'<p class="fine">Оценка внесена человеком, без ИИ.</p>':''}
    ${d.original.model_info?`<p class="fine model-info">Модель: ${esc(d.original.model_info.model)} · ${esc(d.original.model_info.prompt_tokens)} входных / ${esc(d.original.model_info.completion_tokens)} выходных токенов ${d.original.model_info.approx_cost_usd!==null?'· ориентировочно $'+Number(d.original.model_info.approx_cost_usd).toFixed(5):''}</p>`:''}
    <a class="export-link" href="/api/evaluations/${encodeURIComponent(d.id)}/export.json" target="_blank" rel="noopener">↗ JSON-отчёт</a>
    <div class="section-results">${Object.entries(d.final.sections).map(([title,s])=>`<div class="section-chip">${esc(title)}: <b>${num(s.earned)} / ${num(s.maximum)}</b></div>`).join('')}</div>
    ${d.final.gross_error?'<span class="tag danger">⚠ Грубое нарушение</span>':'<span class="tag">Грубых нарушений не выявлено</span>'}
    ${changed?`<div class="divider"></div><div class="fine">Первоначальный балл</div><b>${num(d.original.total_score)}</b> → <b>${num(d.final.total_score)}</b>`:''}
    <h2 style="margin:19px 0 9px">Оценка по критериям</h2>${rows}
    <div class="divider"></div><h2>Транскрипт</h2><p class="muted fine">Нажатие на доказательство перемещает к нужной реплике.</p>
    ${d.conversation.audio_url&&validAudio(d.conversation.audio_url)?`<div class="audio-player"><b>Запись звонка</b><audio controls preload="none" src="${esc(d.conversation.audio_url)}"></audio><a class="audio-link" href="${esc(d.conversation.audio_url)}" target="_blank" rel="noopener noreferrer">Открыть запись в отдельной вкладке</a></div>`:''}
    <div class="transcript" id="transcript">${turns}</div>
    <div class="divider"></div><h2>Апелляции и пересмотр</h2><p class="fine">Проверяющий подтверждает или меняет оценку. Исходная оценка сохраняется.</p>
    ${appeals||'<div class="empty" style="padding:20px">Нет поданных апелляций</div>'}`;
}
function renderChecklistVersions(){
  const el=$('#checklistVersion');const id=S.checklist?.id;
  el.innerHTML=S.state.checklists.map(c=>`<option value="${esc(c.id)}" ${c.id===id?'selected':''}>Версия ${c.version} · ${esc(c.name)}</option>`).join('');
}
function renderChecklistEditor(){
  if(!S.checklist)return;
  $('#checklistName').value=S.checklist.name;
  $('#criteriaEditor').innerHTML=S.checklist.criteria.map((c,i)=>`<div class="criterion-edit" data-index="${i}">
    <div class="edit-grid"><label>ID<input name="id" value="${esc(c.id)}"></label>
    <label>Название<input name="name" value="${esc(c.name)}"></label>
    <label>Тип<select name="type"><option value="boolean" ${c.type==='boolean'?'selected':''}>Да / Нет</option>
      <option value="scale" ${c.type==='scale'?'selected':''}>Шкала</option><option value="flag" ${c.type==='flag'?'selected':''}>Флаг</option></select></label>
    <label>Вес<input name="weight" type="number" min="0" max="100" step="0.5" value="${esc(c.weight)}"></label>
    <label>N/A<select name="allow_na"><option value="false" ${!c.allow_na?'selected':''}>Нет</option><option value="true" ${c.allow_na?'selected':''}>Да</option></select></label></div>
    <div class="edit-grid" style="grid-template-columns:1fr 1fr auto;margin-top:10px">
    <label>Секция<input name="section" value="${esc(c.section)}"></label>
    <label>Разрешённые ступени шкалы (через запятую)<input name="levels" value="${esc(c.levels.join(','))}" placeholder="0,4,7,10"></label>
    <button class="danger-button" type="button" data-remove="${i}">Удалить</button></div>
    <label class="rule">Правила оценки<textarea name="rule">${esc(c.rule)}</textarea></label></div>`).join('');
  const total=S.checklist.criteria.reduce((s,c)=>s+Number(c.weight||0),0);
  $('#weightsInfo').textContent='Сумма базовых весов: '+total+' (итог нормализуется до 100). Критерии-флаги имеют вес 0.';
}
function collectChecklist(){
  const criteria=[...document.querySelectorAll('.criterion-edit')].map(card=>{
    const val=name=>card.querySelector(`[name="${name}"]`).value;
    const type=val('type');
    let levels=val('levels').split(',').map(s=>s.trim()).filter(Boolean).map(Number);
    if(type==='scale' && levels.some(x=>!Number.isFinite(x))) throw new Error('Шкала содержит нечисловое значение');
    if(type!=='scale') levels=[];
    return {id:val('id').trim(),name:val('name').trim(),section:val('section').trim()||'Другое',
      type,weight:Number(val('weight')),allow_na:val('allow_na')==='true',levels,
      rule:val('rule').trim()};
  });
  return {name:$('#checklistName').value.trim(), criteria};
}
async function reloadEval(){if(S.activeEvaluation)await openEval(S.activeEvaluation);await loadState();}

for(const b of document.querySelectorAll('.nav')) b.addEventListener('click',()=>showPage(b.dataset.page));
$('#search').addEventListener('input',renderDialogs);
$('#languageFilter').addEventListener('change',renderDialogs);
$('#datasetFilter').addEventListener('change',renderDialogs);
$('#channelFilter').addEventListener('change',renderDialogs);
document.body.addEventListener('click',e=>{const go=e.target.closest('[data-goto]');if(go)showPage(go.dataset.goto)});
$('#demoBtn').addEventListener('click',async e=>{busy(e.currentTarget,true);try{const o=await api('/api/demo',{method:'POST'});await loadState();await openEval(o.evaluation_id);report('Добавлен учебный пример (не оценка нейросети).');}catch(err){report(err.message,true)}finally{busy(e.currentTarget,false)}});
$('#importUrl').addEventListener('click',async e=>{const url=$('#apiUrl').value.trim();if(!url)return report('Вставьте URL организаторов',true);busy(e.currentTarget,true);try{const r=await api('/api/import/url',{method:'POST',body:JSON.stringify({url})});$('#apiUrl').value='';await loadState();report(`Импортировано ${r.imported} диалогов (${r.dataset_group_id}).`);}catch(err){report(err.message,true)}finally{busy(e.currentTarget,false)}});
$('#batchBtn').addEventListener('click',async e=>{
  if(!S.state.llm_configured)return report('Укажите CQA_LLM_API_KEY и CQA_LLM_MODEL в .env, затем перезапустите сервер',true);
  const version=S.state.current_checklist.version;
  const evaluated=new Set(S.state.evaluations.filter(x=>x.checklist_version===version).map(x=>x.conversation_id));
  const pending=S.state.conversations.filter(x=>!evaluated.has(x.conversation_id));
  if(!pending.length)return report('Все текущие диалоги уже оценены по версии v'+version);
  if(!confirm(`Запустить AI-анализ ${pending.length} диалогов по версии v${version}? Тексты будут отправлены внешнему поставщику API; возможны расходы.`))return;
  busy(e.currentTarget,true);
  try{
    const job=await api('/api/jobs/batch',{method:'POST',body:JSON.stringify({checklist_id:S.state.current_checklist.id})});
    if(job.job_id){S.jobId=job.job_id;await loadState();startPolling();report('Сервер запустил пакетную оценку '+job.total+' диалогов.');}
    else report('Нет новых диалогов для оценки.');
  }catch(err){report(err.message,true)}finally{busy(e.currentTarget,false)}
});
$('#cancelBatch').addEventListener('click',async()=>{if(!S.jobId)return;try{await api('/api/jobs/'+encodeURIComponent(S.jobId)+'/cancel',{method:'POST'});await loadState();report('Отмена запрошена. Уже начатые диалоги будут завершены.');}catch(e){report(e.message,true)}});
$('#jsonFile').addEventListener('change',async e=>{const file=e.target.files[0];if(!file)return;try{const text=await file.text();const payload=JSON.parse(text);const r=await api('/api/import/json',{method:'POST',body:JSON.stringify({payload})});await loadState();report(`Импортировано ${r.imported} диалогов.`);}catch(err){report(err.message,true)}e.target.value='';});
$('#dialogList').addEventListener('click',async e=>{
  const btn=e.target.closest('button[data-act]');if(!btn)return;
  const id=btn.dataset.dialog;
  if(btn.dataset.act==='open-dialog'){
    try{const c=await api('/api/conversations/'+encodeURIComponent(id));
      const newest=S.state.evaluations.find(v=>v.conversation_id===id);
      if(newest){await openEval(newest.id);return;}
      showPage('results');$('#evaluationDetail').innerHTML=`<h2>${esc(id)}</h2><p class="muted">Оценок пока нет. Вернитесь в «Диалоги» и запустите оценку.</p><div class="transcript">${c.transcript.map((t,i)=>`<div class="turn ${esc(t.speaker)}"><div class="turn-title">#${i+1} · ${esc(t.timestamp)} · ${esc(t.speaker)}</div><p>${esc(t.text)}</p></div>`).join('')}</div>`;
    }catch(err){report(err.message,true)}return;
  }
  busy(btn,true);try{const c=await api('/api/evaluations',{method:'POST',body:JSON.stringify({conversation_id:id,checklist_id:S.state.current_checklist.id})});await loadState();await openEval(c.evaluation_id);report('Оценка выполнена с текущим чек-листом.');}
  catch(err){report(err.message,true)}finally{busy(btn,false)}
});
$('#evaluationList').addEventListener('click',e=>{const row=e.target.closest('[data-eid]');if(row)openEval(row.dataset.eid)});
$('#evaluationDetail').addEventListener('click',e=>{const el=e.target.closest('[data-ev-index]');if(!el)return;
  const i=Number(el.dataset.evIndex),target=$('#turn-'+i);if(!target)return;
  target.scrollIntoView({behavior:'smooth',block:'center'});target.classList.add('hot');
  document.querySelectorAll('.turn').forEach(t=>{if(t!==target)t.classList.remove('hot')});
});
$('#evaluationDetail').addEventListener('submit',async e=>{
  const form=e.target; if(!form.matches('form.appeal-form'))return;
  e.preventDefault();const fd=new FormData(form);
  const payload=form.dataset.appeal?{decision:fd.get('decision'),reviewer_note:String(fd.get('reviewer_note')).trim(),answer:fd.get('answer')}:
    {evaluation_id:S.activeEvaluation,criterion_id:form.dataset.criterion,comment:String(fd.get('comment')).trim()};
  const target=form.dataset.appeal?'/api/appeals/'+encodeURIComponent(form.dataset.appeal)+'/decision':'/api/appeals';
  if(form.dataset.appeal && payload.decision==='change'){const raw=String(fd.get('evidence')||'').trim();if(raw){
    const indexes=raw.split(',').map(x=>Number(x.trim())-1);
    if(indexes.some(x=>!Number.isInteger(x)||x<0))return report('Укажите корректные номера реплик (начиная с 1)',true);
    payload.evidence_indices=indexes;
  }}
  try{await api(target,{method:'POST',body:JSON.stringify(payload)});await loadState();await openEval(S.activeEvaluation);report(form.dataset.appeal?'Решение сохранено. Итог пересчитан.':'Апелляция отправлена.');}
  catch(err){report(err.message,true)}
});
$('#checklistVersion').addEventListener('change',async e=>{try{S.checklist=await api('/api/checklists/'+encodeURIComponent(e.target.value));renderChecklistEditor()}catch(err){report(err.message,true)}});
$('#addCriterion').addEventListener('click',()=>{try{S.checklist={...S.checklist,...collectChecklist()};const ids=new Set(S.checklist.criteria.map(c=>c.id));let n=9;while(ids.has(String(n).padStart(2,'0')))n++;
  S.checklist.criteria.push({id:String(n).padStart(2,'0'),name:'Новый критерий',section:'Дополнительные критерии',type:'boolean',weight:5,allow_na:false,levels:[],rule:'Опишите условия Да / Нет'});renderChecklistEditor();}
  catch(err){report(err.message,true)}});
$('#criteriaEditor').addEventListener('click',e=>{const b=e.target.closest('[data-remove]');if(!b)return;
  try{S.checklist={...S.checklist,...collectChecklist()};S.checklist.criteria.splice(Number(b.dataset.remove),1);renderChecklistEditor();}catch(err){report(err.message,true)}});
$('#saveChecklist').addEventListener('click',async e=>{busy(e.currentTarget,true);try{
  const c=collectChecklist();const o=await api('/api/checklists',{method:'POST',body:JSON.stringify(c)});
  S.checklist={...c,id:o.id,version:o.version};await loadState();renderChecklistEditor();report(`Сохранена версия ${o.version}. Новые оценки будут использовать обновлённые правила.`);
}catch(err){report(err.message,true)}finally{busy(e.currentTarget,false)}});
loadState().then(()=>{if(S.jobId)startPolling();}).catch(err=>report('Ошибка загрузки: '+err.message,true));


function barRow(label,value,count){
  const n=Math.max(0,Math.min(100,Number(value)||0));
  return `<div class="bar-row"><div class="bar-label"><span>${esc(label)}</span><b>${num(n)}%</b></div><div class="bar-track"><div class="bar-fill" style="width:${n}%"></div></div><div class="fine">${count} оценок</div></div>`;
}
async function renderDashboard(){
  if(!S.state)return;
  try{
    const a=await api('/api/analytics');S.analytics=a;
    const stats=[['Диалогов',S.state.conversations.length],['Оценок',a.evaluations],['Средний результат',a.average_score===null?'—':num(a.average_score)],['Апелляций на проверке',a.pending_appeals]];
    $('#dashStats').innerHTML=stats.map(([k,v])=>`<div class="stat"><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`).join('');
    $('#languageBreakdown').innerHTML=Object.keys(a.by_language).length?Object.entries(a.by_language).sort().map(([l,r])=>barRow(l,r.average,r.count)).join(''):'<div class="empty">После первой оценки здесь появится аналитика</div>';
    $('#criteriaBreakdown').innerHTML=Object.keys(a.by_criterion).length?Object.entries(a.by_criterion).sort(([a],[b])=>a.localeCompare(b)).map(([id,r])=>barRow(id+' · '+r.name,r.average_percent,r.count)).join(''):'<div class="empty">Критерии появятся после оценки диалогов</div>';
    $('#reviewBubble').textContent=a.pending_appeals||'';
  }catch(e){$('#dashStats').textContent='Аналитика недоступна: '+e.message;}
}
async function renderReviewQueue(){
  if(!S.state)return;
  try{
    const queue=await api('/api/review-queue');
    $('#reviewQueue').innerHTML=queue.length?queue.map(a=>`<div class="list-row"><div><div class="name mono">${esc(a.conversation_id)} <span class="tag warn">Критерий ${esc(a.criterion_id)}</span></div><div class="meta">${esc(a.language)} · v${esc(a.checklist_version)} · ${esc(a.created_at.slice(0,16).replace('T',' '))}</div><p class="review-comment">${esc(a.comment)}</p></div><button class="primary small" data-review-eid="${esc(a.evaluation_id)}">Открыть оценку</button></div>`).join(''):'<div class="empty">Нет апелляций, ожидающих рассмотрения</div>';
  }catch(e){$('#reviewQueue').textContent='Ошибка: '+e.message;}
}
$('#reviewQueue').addEventListener('click',e=>{const b=e.target.closest('[data-review-eid]');if(b)openEval(b.dataset.reviewEid)});
function renderJobs(){
  if(!S.state)return;
  const jobs=S.state.jobs||[];
  const job=jobs.find(j=>j.id===S.jobId)||jobs[0];
  const el=$('#jobProgress');
  if(!job){el.innerHTML='<span class="fine">Пакетная обработка ещё не запускалась.</span>';$('#cancelBatch').classList.add('hidden');return;}
  S.jobId=job.id;
  const share=job.total?Math.round(job.completed/job.total*100):0;
  const labels={pending:'Ожидает',running:'Обработка',cancelling:'Завершение текущих запросов',cancelled:'Отменено',completed:'Завершено',failed:'Ошибка',interrupted:'Прервано перезапуском сервера'};
  el.innerHTML=`<div class="job-head"><b>${esc(labels[job.status]||job.status)} · ${job.completed}/${job.total}</b><span class="fine">Успешно: ${job.succeeded} · Ошибок: ${job.failed}</span></div><div class="bar-track"><div class="bar-fill" style="width:${share}%"></div></div><p class="fine">Версия: ${esc(job.checklist_id)}. При сбое можно запустить новую обработку — уже оценённые диалоги будут пропущены.</p>`;
  $('#cancelBatch').classList.toggle('hidden',!['pending','running'].includes(job.status));
}
function startPolling(){
  if(polling)clearInterval(polling);
  polling=setInterval(async()=>{
    if(!S.jobId)return;
    try{
      const job=await api('/api/jobs/'+encodeURIComponent(S.jobId));
      if(S.state){const i=(S.state.jobs||[]).findIndex(j=>j.id===job.id);if(i>=0)S.state.jobs[i]=job;else S.state.jobs.unshift(job);renderJobs();}
      if(!['running','pending','cancelling'].includes(job.status)){
        clearInterval(polling);polling=null;
        await loadState();
        if(job.failed){report(`Пакетная обработка: ${job.succeeded} успешно, ${job.failed} ошибок. Детали доступны в API /api/jobs/${job.id}`,true)}
        else report(`Пакетная обработка завершена: ${job.succeeded} диалогов оценено.`);
      }
    }catch(e){clearInterval(polling);polling=null;report('Не удалось получить статус: '+e.message,true)}
  },1300);
}
