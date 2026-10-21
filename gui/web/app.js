import { AvatarManager } from './avatars.js';
import { effortModel, nextEffort, renderReasoningEffort } from './reasoning-effort.js';
import { AccountUsageView } from './account-usage.js';
import { ConnectionsPanel, groupAgents } from './connections.js';
import { AgentDebugConsole } from './debug-console.js';
import { catalogText, modelLabel, ModelSelect } from './model-directory.js';
const $=id=>document.getElementById(id);
const names={chatgpt:'ChatGPT',claude:'Claude',deepseek:'DeepSeek',supervisor:'监工',you:'你',system:'系统'};
const usageViews=new Map();
let contextEntries=[];
let memberEffort=null,roomMemberEffort=null;
const letters={chatgpt:'G',claude:'C',deepseek:'D',supervisor:'M',you:'你'};
const statuses={idle:'就绪',running:'运行中',paused:'等待继续',budget_paused:'等待上下文预算处理',complete:'已完成',partial:'部分完成',blocked:'需要处理',stopped:'已停止',interrupted:'上次运行已中断'};
const app={token:'',prefs:null,roomSettings:null,roomEpoch:0,removing:false,editRoomMember:null,modelCatalog:{agents:{},refreshing:false},connections:{},logins:{},rooms:[],room:null,policy:null,events:null,messages:new Map(),phaseHistory:[],customProvider:null,editMember:null,modelPoll:null,modelsBusy:false,catalogEpoch:0,evidenceMap:{},lastActivity:0,pendingSaves:0,syncBusy:false,configEpoch:0,usageData:{agents:{},refreshing:false},usagePoll:null,usageBusy:false,usageEpoch:0};
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function toast(text){$('toast').textContent=text;$('toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').hidden=true,4500)}
async function api(path,body){const response=await fetch('/api'+path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json','X-Tiered-Token':app.token},body:body===undefined?undefined:JSON.stringify(body)});let value;try{value=await response.json()}catch{throw new Error('本机服务未返回有效响应。')}if(!response.ok)throw new Error(value.error||'操作没有完成。');return value}
function bind(id,handler){$(id).addEventListener('click',async event=>{try{await handler(event)}catch(error){toast(error.message)}})}
function textHtml(text){const value=String(text);const pattern=/```[^\n]*\n([\s\S]*?)(?:```|$)/g;let html='',offset=0;const prose=part=>escape(part).replace(/\*\*(.*?)\*\*/g,'<strong>$1</strong>').replace(/`([^`\n]+)`/g,'<code>$1</code>').replace(/^#{1,4} (.+)$/gm,'<h4>$1</h4>');for(const match of value.matchAll(pattern)){html+=prose(value.slice(offset,match.index))+'<pre>'+escape(match[1])+'</pre>';offset=match.index+match[0].length}return html+prose(value.slice(offset))}

const backendLabels={chatgpt:'ChatGPT CLI',claude:'Claude CLI',deepseek:'DeepSeek API','openai-compatible':'兼容 API'};
const roomPrefs=()=>app.roomSettings||{...app.prefs,members:[],team:[]};
const getMember=id=>roomPrefs().members.find(m=>m.id===id);
const getAgent=id=>app.prefs.agents.find(m=>m.id===id);
const isCli=m=>['chatgpt','claude'].includes(m.backend);
const getCatalog=m=>app.modelCatalog.agents[m.id]||(isCli(m)?app.modelCatalog.agents[app.prefs.agents.find(a=>a.backend===m.backend)?.id]:undefined);
const modelList=m=>getCatalog(m)?.models||[];
const avatarClass=id=>getAgent(id)?.backend==='openai-compatible'?'custom':getAgent(id)?.backend||id;
const letter=id=>letters[id]||(names[id]||id).slice(0,1).toUpperCase();
function avatarMarkup(id,extra=''){return '<span class="avatar '+avatarClass(id)+' '+extra+'" data-avatar="'+escape(id)+'" data-letter="'+escape(letter(id))+'">'+escape(letter(id))+'</span>'}
const avatarManager=new AvatarManager($('avatars-dialog'),{api,notice:toast,people:()=>[{id:'you',name:'你',letter:'你',detail:'你的群聊头像'},{id:'supervisor',name:'监工',letter:'M',detail:'独立监工身份'},...(app.prefs?.agents||[]).map(a=>({...a,letter:letter(a.id),avatarClass:avatarClass(a.id),detail:a.group||a.backend}))],changed:()=>avatarManager.apply()});
function renderConnections(){let count=0;for(const m of app.prefs.agents){const item=app.connections[m.id]||{};if(item.ready)count++;const dot=$('state-'+m.id);if(dot){dot.className='provider-state'+(item.ready?' ready':'');dot.title=item.detail||'尚未检查'}}$('connection-count').textContent=count+'/'+app.prefs.agents.length;$('connection-count').title='连接已配置 '+count+' 位 / 共保存 '+app.prefs.agents.length+' 位 Agent';connectionPanel.render(app.prefs.agents,app.connections,app.modelCatalog.agents,app.usageData.agents,app.logins);avatarManager.apply()}
const connectionPanel=new ConnectionsPanel($('connection-members'),{
 onSaveKey:async(id,key)=>{await queueMutation('/agents/'+id+'/key',{key})},
 onLogin:async provider=>{const data=await api('/login/'+provider,{});app.logins[provider]=data;renderConnections();return data.message},
 onEdit:id=>memberDialog(id),
 onDebug:id=>debugConsole.open(getAgent(id)),
 onUsageRefresh:id=>refreshUsage(id),
 onAvatar:id=>avatarManager.open(id)
});
const memberModel=new ModelSelect($('member-model'),$('member-custom-model'),$('member-custom-model-wrap'));
const roomMemberModel=new ModelSelect($('room-member-model'),$('room-member-custom-model'),$('room-member-custom-wrap'));
const debugConsole=new AgentDebugConsole($('debug-dialog'),{api,models:modelList,catalog:getCatalog});
function renderRooms(){const host=$('rooms');host.innerHTML='';for(const room of app.rooms){const button=document.createElement('button');button.className='room-link'+(app.room?.id===room.id?' active':'');button.title=room.title;button.innerHTML='<strong>'+escape(room.title)+'</strong><span>'+escape(statuses[room.status]||room.status)+' · '+escape(room.project.split(/[\\/]/).pop())+'</span>';button.onclick=()=>openRoom(room.id).catch(e=>toast(e.message));host.append(button)}}
function renderProviders(){$('roster').hidden=!app.room;$('roster').parentElement.classList.toggle('no-room',!app.room);$('team-top').hidden=!app.room;$('context-settings-open').hidden=!app.room;$('policy-top').hidden=!app.room;Object.assign(names,app.prefs.member_names||{});for(const m of app.prefs.agents)names[m.id]=m.name;const target=$('target');const selected=target.value;target.innerHTML='<option value="all">全部讨论成员依次回复</option><option value="supervisor">监工（补充合作要求）</option>'+roomPrefs().members.filter(m=>m.id!==roomPrefs().executor).map(m=>'<option value="'+m.id+'">'+escape(m.name)+'</option>').join('');target.value=[...target.options].some(o=>o.value===selected)?selected:'all';const host=$('providers');usageViews.clear();host.innerHTML=roomPrefs().members.length?'':'<p class="empty-team">此协作室暂无成员。点击 ＋ 邀请已保存的 Agent。</p>';for(const m of roomPrefs().members){const provider=m.id;const node=document.createElement('section');node.className='provider';node.innerHTML='<div class="provider-top">'+avatarMarkup(provider)+'<div><strong>'+escape(m.name)+'</strong><span class="sub">'+escape(backendLabels[m.backend])+'</span></div><span id="state-'+provider+'" class="provider-state"></span><button class="member-edit" aria-label="编辑 '+escape(m.name)+'">⋯</button></div><select id="model-'+provider+'" aria-label="'+escape(m.name)+' 模型"></select><p id="model-note-'+provider+'" class="model-catalog-note"></p><section class="provider-usage compact" aria-label="账户用量与余额"></section><button class="context-badge" data-context="'+provider+'"></button>'+'<label class="effort-row"><span>推理强度</span><select id="effort-'+provider+'" aria-label="'+escape(m.name)+' 推理强度"></select></label>';host.append(node);const usageView=new AccountUsageView(node.querySelector('.provider-usage'),()=>refreshUsage(provider));usageViews.set(provider,usageView);usageView.render(app.usageData.agents[provider]);node.querySelector('.member-edit').onclick=()=>roomMemberDialog(provider);renderModel(provider);$('model-'+provider).onchange=async()=>{try{const value=$('model-'+provider).value;if(value==='__custom'){customDialog(provider);return}m.model=value;m.effort=nextEffort(effortModel(modelList(m),value),m.effort);renderEffort(provider);await saveMemberFields(provider,{model:m.model,effort:m.effort})}catch(e){toast(e.message)}};$('effort-'+provider).onchange=async()=>{try{m.effort=$('effort-'+provider).value||null;await saveMemberFields(provider,{effort:m.effort})}catch(e){toast(e.message)}}}
const welcomeAvatars=document.querySelector('#welcome .welcome-avatars');if(welcomeAvatars)welcomeAvatars.innerHTML=roomPrefs().members.slice(0,6).map(m=>avatarMarkup(m.id)).join('')+avatarMarkup('supervisor');$('pause-before').checked=roomPrefs().pause_before_execution;renderSupervisor();renderPolicy();renderConnections();renderContexts()}
function customDialog(provider){app.customProvider=provider;$('model-dialog-title').textContent=(names[provider]||provider)+' 自定义模型';$('custom-model').value='';$('model-dialog').showModal()}
function renderSupervisor(){const config=roomPrefs().supervisor;const source=getAgent(config.provider)||app.prefs.agents[0];const list=[...modelList(source)];if(!list.some(m=>m.id===config.model))list.push({id:config.model,label:config.model+'（当前保存，目录未列出）'});$('supervisor-provider').innerHTML=app.prefs.agents.map(m=>'<option value="'+m.id+'">'+escape(m.name)+' · '+escape(backendLabels[m.backend])+'</option>').join('');$('supervisor-provider').value=source.id;$('supervisor-model').innerHTML=list.map(m=>'<option value="'+escape(m.id)+'">'+escape(modelLabel(m))+'</option>').join('')+'<option value="__custom">＋ 自定义模型 ID</option>';$('supervisor-model').value=config.model;renderReasoningEffort($('supervisor-effort'),effortModel(modelList(source),config.model),config.effort);$('supervisor-catalog-note').textContent=catalogText(getCatalog(source))}
function renderTeam(){
 const host=$('team-list');host.innerHTML='';const members=roomPrefs().members;
 $('team-room-title').textContent=app.room?.title||'协作室信息';
 $('team-summary').textContent=members.length+' 位成员 · 只管理此协作室';
 const executor=$('room-executor');executor.innerHTML='<option value="">不指定专门执行者</option>'+members.map(m=>'<option value="'+escape(m.id)+'">'+escape(m.name)+(m.backend==='deepseek'?' · 默认推荐':'')+'</option>').join('');executor.value=roomPrefs().executor||'';
 $('team-top').textContent='协作室信息 · '+members.length;
 for(const m of members){
  const tile=document.createElement('button');tile.type='button';tile.className='group-member'+(app.removing?' removing':'');tile.dataset.member=m.id;
  tile.setAttribute('aria-label',(app.removing?'移出成员 ':'成员设置 ')+m.name);
  tile.innerHTML=avatarMarkup(m.id)+'<strong>'+escape(m.name)+'</strong><small>'+escape(m.model)+'</small>'+(app.removing?'<b class="member-minus">−</b>':'');
  tile.onclick=()=>app.removing?setMembership(m.id,false).catch(e=>toast(e.message)):roomMemberDialog(m.id);
  host.append(tile);
 }
 const add=document.createElement('button');add.type='button';add.className='group-member group-action';add.id='member-add';add.innerHTML='<span>＋</span><strong>邀请成员</strong>';add.onclick=()=>{app.removing=false;renderTeam();renderPicker();avatarManager.apply();$('team-picker-dialog').showModal()};host.append(add);
 const remove=document.createElement('button');remove.type='button';remove.className='group-member group-action';remove.innerHTML='<span>−</span><strong>'+(app.removing?'完成移出':'移出成员')+'</strong>';remove.disabled=!members.length;remove.onclick=()=>{app.removing=!app.removing;renderTeam()};host.append(remove);avatarManager.apply(host);
}
async function setMembership(id,enabled){
 if(!app.room)throw new Error('请先进入一个协作室。');
 await queueMutation('/rooms/'+app.room.id+'/members/'+id+'/membership',{enabled});
 toast(enabled?'已邀请加入此协作室':'已移出此协作室，Agent 连接和其他协作室不受影响');
}
function renderPicker(){
 const host=$('team-picker-list');host.innerHTML='';const active=new Set(roomPrefs().members.map(m=>m.id));
 for(const [name,agents] of groupAgents(app.prefs.agents)){
  const group=document.createElement('section');group.className='agent-picker-group';
  const title=document.createElement('h3');title.textContent=name;group.append(title);
  for(const a of agents){
   const ref=roomPrefs().team.find(r=>r.agent_id===a.id);const row=document.createElement('div');row.className='team-row';row.dataset.agent=a.id;
   row.innerHTML=avatarMarkup(a.id)+'<div><strong>'+escape(a.name)+'</strong><span>'+escape(ref?.model||a.model)+'</span><small>'+escape(app.connections[a.id]?.detail||'尚未检查')+'</small></div><button class="outline">'+(active.has(a.id)?'已加入':'邀请加入')+'</button>';
   const button=row.querySelector('button');button.classList.toggle('in-team',active.has(a.id));button.disabled=active.has(a.id);
   button.onclick=async()=>{button.disabled=true;try{await setMembership(a.id,true)}catch(e){toast(e.message);button.disabled=false}};
   group.append(row);
  }
  host.append(group);
 }
}
function teamDialog(){avatarManager.apply();if(!app.room){projectDialog();return}app.removing=false;renderTeam();$('team-dialog').showModal()}
function roomMemberDialog(id){
 const m=getMember(id);if(!m)return;app.editRoomMember=id;
 $('room-member-title').textContent=m.name+' · 此协作室的设置';roomMemberModel.setValue(m.model);roomMemberModel.update(modelList(m));
 roomMemberEffort=m.effort;refreshRoomMemberEffort();$('room-member-dialog').showModal();
}
function memberDialog(id=null){
 if(id&&!getAgent(id)){toast('此 Agent 不存在，请刷新 Agent 管理');return}
 app.editMember=id;const m=id?getAgent(id):{name:'',backend:'openai-compatible',model:'',effort:null,base_url:'',group:'',no_auth:false};
 $('member-dialog-title').textContent=id?'编辑已保存的 Agent':'添加 Agent';
 $('member-name').value=m.name;$('member-backend').value=m.backend;$('member-group').value=m.group||'';
 memberModel.setValue(m.model);memberEffort=m.effort;$('member-endpoint').value=m.base_url;
 $('member-key').value='';$('member-no-auth').checked=m.no_auth;memberFields();$('member-dialog').showModal();
}
function editorSource(){
 const backend=$('member-backend').value;const saved=app.editMember?getAgent(app.editMember):null;
 const endpoint=$('member-endpoint').value.trim().replace(/\/+$/,'');
 return saved&&saved.backend===backend&&(backend!=='openai-compatible'||saved.base_url===endpoint)?saved:{id:'',backend,base_url:endpoint};
}
function refreshMemberModels(){
 const source=editorSource();memberModel.update(modelList(source));
 $('member-model-note').textContent=catalogText(getCatalog(source));refreshMemberEffort();
}
function memberFields(){
 const memberName=$('member-name').value.trim()||'此 Agent';$('member-key-label').textContent=memberName+' API 密钥';
 $('member-key').placeholder='粘贴 '+memberName+' 的密钥；留空保留现有密钥';
 const backend=$('member-backend').value;const cli=['chatgpt','claude'].includes(backend);
 $('member-endpoint-wrap').hidden=backend!=='openai-compatible';$('member-endpoint').required=backend==='openai-compatible';
 $('member-key-wrap').hidden=cli;$('member-login-note').hidden=!cli;
 $('member-no-auth').closest('label').hidden=backend!=='openai-compatible';refreshMemberModels();
}
function renderCatalogViews(){
 if(!app.prefs)return;const active=document.activeElement;
 for(const m of roomPrefs().members){
  if(active!==$('model-'+m.id)&&active!==$('effort-'+m.id))renderModel(m.id);
  $('model-note-'+m.id).textContent=catalogText(getCatalog(m));
 }
 if(!['supervisor-model','supervisor-provider','supervisor-effort'].includes(active?.id))renderSupervisor();
 if($('member-dialog').open)refreshMemberModels();
 if($('room-member-dialog').open&&app.editRoomMember){roomMemberModel.update(modelList(getAgent(app.editRoomMember)));refreshRoomMemberEffort()}debugConsole.refreshModels();renderConnections();
 const entries=Object.values(app.modelCatalog.agents);const failed=entries.filter(e=>e.state==='error').length;
 $('model-catalog-status').textContent=app.modelCatalog.refreshing?'正在自动更新四家模型目录…':failed?'模型目录已更新，'+failed+' 个连接暂用缓存或当前选择。':'模型目录在打开或刷新页面时自动更新。';
}
function scheduleModelPoll(){
 clearTimeout(app.modelPoll);if(app.modelCatalog.refreshing)app.modelPoll=setTimeout(pollModels,600);
}
function applyCatalog(data){
 if(!data)return;const changed=JSON.stringify(app.modelCatalog)!==JSON.stringify(data);app.modelCatalog=data;
 if(changed)renderCatalogViews();scheduleModelPoll();
}
async function pollModels(){
 if(app.modelsBusy||app.pendingSaves){scheduleModelPoll();return}
 const epoch=app.configEpoch;const catalogEpoch=app.catalogEpoch;app.modelsBusy=true;
 try{const data=await api('/models');if(epoch===app.configEpoch&&catalogEpoch===app.catalogEpoch)applyCatalog(data)}
 catch{ $('model-catalog-status').textContent='目录同步暂时失败，将自动重试。' }
 finally{app.modelsBusy=false;scheduleModelPoll()}
}
async function refreshCatalog(){
 await saveChain;const epoch=app.configEpoch;const catalogEpoch=++app.catalogEpoch;const data=await api('/models',{});if(epoch===app.configEpoch&&catalogEpoch===app.catalogEpoch)applyCatalog(data);
}

function applyUsage(data){
 if(!data)return;app.usageData=data;
 for(const [id,view] of usageViews)view.render(data.agents[id]);renderConnections();scheduleUsagePoll();
}
function scheduleUsagePoll(){
 clearTimeout(app.usagePoll);app.usagePoll=setTimeout(pollUsage,app.usageData.refreshing?800:60000);
}
async function pollUsage(){
 if(app.usageBusy){scheduleUsagePoll();return}app.usageBusy=true;const epoch=app.configEpoch,usageEpoch=app.usageEpoch;
 try{const data=await api('/usage');if(epoch===app.configEpoch&&usageEpoch===app.usageEpoch)applyUsage(data)}
 catch{}finally{app.usageBusy=false;scheduleUsagePoll()}
}
async function refreshUsage(id=null){
 await saveChain;const epoch=app.configEpoch,usageEpoch=++app.usageEpoch;
 const data=await api(id?'/agents/'+id+'/usage':'/usage',{});
 if(epoch===app.configEpoch&&usageEpoch===app.usageEpoch)applyUsage(data);
}

function renderPolicy(){$('policy-summary').textContent=(app.policy?.text||app.prefs.cooperation_policy);$('policy-summary').title=$('policy-summary').textContent}
function policyDialog(){$('policy-text').value=app.policy?.text||app.prefs.cooperation_policy;$('max-steps').value=roomPrefs().supervisor.max_steps;$('policy-default').checked=!app.room;$('policy-dialog').showModal()}
function renderChecks(event){const labels={met:'已符合',pending:'待核对',violated:'需纠偏',not_applicable:'不适用'};const checks=new Map(event.checks.map(c=>[c.rule_id,c]));$('rule-checks').innerHTML='<div class="checks-title">第 '+event.turn+' 轮 · 合作规则检查</div>'+event.rules.map(rule=>{const c=checks.get(rule.id)||{status:'pending',evidence:'等待核对',evidence_ids:[]};return '<details class="rule-check '+escape(c.status)+'"><summary><span>'+escape(labels[c.status])+'</span>'+escape(rule.text)+'</summary><p>'+escape(c.evidence||'等待证据')+'</p><p class="muted">核对方法：'+escape(rule.criterion)+'</p><small>'+escape((c.evidence_ids||[]).join(' · '))+'</small></details>'}).join('')}
function renderModel(provider){const m=getMember(provider);const select=$('model-'+provider);const list=[...modelList(m)];if(!list.some(x=>x.id===m.model))list.push({id:m.model,label:m.model+'（当前保存，目录未列出）'});select.innerHTML=list.map(x=>'<option value="'+escape(x.id)+'">'+escape(modelLabel(x))+'</option>').join('')+'<option value="__custom">＋ 自定义模型 ID</option>';select.value=m.model;$('model-note-'+provider).textContent=catalogText(getCatalog(m));renderEffort(provider)}
function renderEffort(provider){const m=getMember(provider);renderReasoningEffort($('effort-'+provider),effortModel(modelList(m),m.model),m.effort)}
function refreshMemberEffort(){renderReasoningEffort($('member-effort'),effortModel(modelList(editorSource()),memberModel.value),memberEffort)}
function refreshRoomMemberEffort(){const m=getAgent(app.editRoomMember);if(m)renderReasoningEffort($('room-member-effort'),effortModel(modelList(m),roomMemberModel.value),roomMemberEffort)}
let saveChain=Promise.resolve();
function editingConfiguration(){return ['member-dialog','room-member-dialog','model-dialog','policy-dialog','context-settings-dialog','avatars-dialog'].some(id=>$(id).open)||document.activeElement?.tagName==='SELECT'}
function applyState(data,force=false){
 if(data.connections)app.connections=data.connections;if(data.logins)app.logins=data.logins;let changed=false;
 if(data.preferences&&(force||!editingConfiguration())){changed=JSON.stringify(app.prefs)!==JSON.stringify(data.preferences);app.prefs=data.preferences}
 if(data.room_settings&&data.room_settings.room_id===app.room?.id&&(force||!editingConfiguration())){changed=changed||JSON.stringify(app.roomSettings)!==JSON.stringify(data.room_settings);app.roomSettings=data.room_settings}
 if(data.avatars)avatarManager.update(data.avatars);
 if(changed){renderProviders();if(app.room)renderTeam();if($('team-picker-dialog').open)renderPicker()}
 if(data.model_catalog)applyCatalog(data.model_catalog);if(data.account_usage)applyUsage(data.account_usage);renderConnections();
}
async function queueMutation(path,body){app.pendingSaves++;app.configEpoch++;const pending=saveChain.catch(()=>{}).then(()=>api(path,body));saveChain=pending;try{const data=await pending;applyState(data,true);return data}finally{app.pendingSaves--;app.configEpoch++}}
async function savePrefs(){
 if(!app.room)throw new Error('请先进入协作室。');
 const payload=JSON.parse(JSON.stringify({pause_before_execution:roomPrefs().pause_before_execution,supervisor:roomPrefs().supervisor}));
 await queueMutation('/rooms/'+app.room.id+'/configuration',payload);
 if(app.room?.status==='running')toast('已保存此协作室设置，下次开始协作时生效');
}
async function saveMemberFields(id,fields){if(!app.room)throw new Error('请先进入协作室。');return queueMutation('/rooms/'+app.room.id+'/members/'+id,fields)}
async function syncConnections(force=false){if(!app.prefs||app.syncBusy||app.pendingSaves||editingConfiguration())return;const epoch=app.configEpoch;app.syncBusy=true;try{const data=await api('/connection-state?'+new URLSearchParams({...(app.room?{room_id:app.room.id}:{}),...(force?{refresh:'1'}:{})}));if(app.pendingSaves||epoch!==app.configEpoch)return;applyState(data);$('settings-note').textContent='已同步 '+app.prefs.agents.length+' 位已保存 Agent；连接状态每 5 秒自动更新。'}catch(e){if($('settings-dialog').open)$('settings-note').textContent='同步失败：'+e.message+' 将自动重试。';if(force)toast(e.message)}finally{app.syncBusy=false}}
setInterval(()=>{if(document.visibilityState==='visible')syncConnections()},5000);
window.addEventListener('focus',()=>syncConnections(true));
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')syncConnections(true)});

function readable(text){try{const value=JSON.parse(text.replace(/^```(?:json)?\s*|\s*```$/g,''));if(value.message&&value.action){const a=value.action;const types={ask:'安排发言',execute:'安排执行',finish:'完成核对',wait:'等待处理'};return value.message+'\n\n'+(types[a.type]||a.type)+(a.agents?.length?' · '+a.agents.map(p=>names[p]||p).join('、'):'')+(a.instruction?'\n'+a.instruction:'')+(a.parallel?'\n并行发言':'')}if(value.plan&&Array.isArray(value.tasks))return value.plan+'\n\n## 执行任务\n'+value.tasks.map((t,i)=>(t.id||('T-'+String(i+1).padStart(2,'0')))+' · '+t.goal+'\n允许修改：'+(t.allowed_paths||[]).join('、')+'\n验收：'+(t.acceptance_commands||[]).join('；')).join('\n\n');if(value.verdict)return (value.verdict==='PASS'?'评审通过':'存在阻断问题')+'\n'+(value.summary||'')+'\n'+(value.findings||[]).map(f=>'• '+f).join('\n')}catch{}return text}
function renderMessage(message){app.messages.set(message.id,message);let node=$('m-'+message.id);if(!node){node=document.createElement('article');node.id='m-'+message.id;$('timeline').append(node)}node.className='message '+message.provider+' '+message.status;const time=new Date(message.created).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'});if(message.provider==='system'){node.innerHTML='<div class="message-content">'+textHtml(readable(message.text))+'</div>';return}node.innerHTML=''+avatarMarkup(message.provider)+'<div class="message-main"><div class="message-meta"><span class="message-name">'+escape(names[message.provider]||message.provider)+'</span>'+(message.model?'<span class="message-model">'+escape(message.model)+'</span>':'')+(message.phase?'<span class="message-phase">'+escape(message.phase)+'</span>':'')+'<time>'+time+'</time></div><div class="message-content">'+(message.text?textHtml(readable(message.text)):'<span class="muted">正在生成答复…</span>')+'</div></div>';avatarManager.apply(node)}
function composerState(){const running=app.room&&['running','paused','budget_paused'].includes(app.room.status);const steering=$('mode').value==='discuss'&&$('target').value==='supervisor';$('send').disabled=Boolean(running&&!steering);$('prompt').disabled=Boolean(running&&!steering);const workflow=$('mode').value==='workflow';$('target-wrap').hidden=workflow;$('mode-note').textContent=workflow?'按合作规则动态安排各方':steering?'补充合作规则，下个步骤生效':'只读讨论，不修改项目文件';$('send').firstChild.textContent=workflow?'开始协作 ':steering?'交给监工 ':'发送 ';$('prompt').placeholder=steering?'告诉监工你希望怎样合作，例如：下一轮让两方分别提出反例……':'输入要完成的任务，监工会按合作规则安排各方……';$('force-budget').hidden=!app.room;$('force-budget').disabled=Boolean(running&&app.room.status!=='budget_paused');$('force-budget').classList.toggle('waiting',app.room?.status==='budget_paused')}
function setStatus(value){if(!app.room)return;app.room.status=value;const room=app.rooms.find(r=>r.id===app.room.id);if(room)room.status=value;$('run-status').textContent=statuses[value]||value;$('run-status').className='status '+value;const running=['running','paused','budget_paused'].includes(value);$('stop').hidden=!running;$('resume').hidden=value!=='paused';$('activity').hidden=!running;if(!running)$('activity-text').textContent='';composerState();renderRooms()}
function onEvent(event){const timeline=$('timeline');const follow=timeline.scrollHeight-timeline.scrollTop-timeline.clientHeight<160;switch(event.type){case'budget_wait':app.budgetWait=event.pending;composerState();break;case'policy_updated':if(!app.policy||event.policy.revision>=app.policy.revision){app.policy=event.policy;renderPolicy()}break;case'supervision':if(event.turn===1)app.evidenceMap={};renderChecks(event);break;case'evidence':{const r=event.record;if(r.message_id){app.evidenceMap[r.id]=r.message_id;const node=$('m-'+r.message_id);if(node){const tag=document.createElement('span');tag.className='evidence-badge';tag.dataset.evidence=r.id;tag.textContent=r.id+' · '+(r.status==='complete'?'已返回':'未通过')+(r.kind==='execute'?(r.tests_run?' · 已运行验收':' · 未测试'):'');node.querySelector('.message-meta')?.append(tag)}}break;}case'message':$('welcome')?.remove();renderMessage(event.message);break;case'room_status':setStatus(event.status);break;case'context':if(!contextEntries.some(e=>e.id===event.delivery_id))contextEntries.unshift({...event.context,identity:event.provider,id:event.delivery_id,created:event.time});contextEntries=contextEntries.slice(0,100);renderContexts();break;case'activity':$('activity').hidden=false;$('activity-text').textContent=(names[event.provider]||'系统')+'：'+event.text;app.lastActivity=Date.now();break;case'phase':if(app.phaseHistory.at(-1)!==event.phase)app.phaseHistory.push(event.phase);$('phases').innerHTML=app.phaseHistory.map((p,i)=>'<div class="phase '+(i===app.phaseHistory.length-1?'current':'')+'">'+escape(p)+'</div>').join('');$('activity').hidden=false;$('activity-text').textContent=(names[event.provider]||'系统')+' · '+event.phase;break;case'plan':$('task-list').innerHTML=event.tasks.map(t=>'<div class="task-item"><strong>'+escape(t.id)+'</strong> '+escape(t.goal)+'</div>').join('');break;case'test':{const detail=document.createElement('details');detail.className='tool-output'+(event.exit_code?' fail':'');detail.innerHTML='<summary>'+escape(event.command)+' · 退出码 '+event.exit_code+'</summary><pre>'+escape(event.output||'（无输出）')+'</pre>';timeline.append(detail);break}}if(follow)timeline.scrollTop=timeline.scrollHeight}
async function openRoom(rid){const epoch=++app.roomEpoch;app.events?.close();await saveChain.catch(()=>{});const data=await api('/rooms/'+rid);if(epoch!==app.roomEpoch)return;for(const id of ['team-dialog','team-picker-dialog','room-member-dialog','model-dialog','policy-dialog','context-dialog','context-settings-dialog'])$(id).close();app.room=data.room;app.roomSettings=data.room_settings;contextEntries=data.context||[];app.budgetWait=data.budget_wait||[];renderProviders();renderTeam();app.policy=data.policy;renderPolicy();app.messages.clear();app.evidenceMap={};app.phaseHistory=[];$('timeline').innerHTML='';$('phases').innerHTML='<p class="muted">等待监工安排。</p>';$('task-list').innerHTML='';$('rule-checks').innerHTML='';$('room-title').textContent=data.room.title;$('project-chip').textContent=data.room.project;$('project-chip').title=data.room.project;for(const message of data.messages)renderMessage(message);if(!data.messages.length){const note=document.createElement('div');note.className='welcome';note.innerHTML='<div class="welcome-avatars">'+roomPrefs().members.slice(0,6).map(m=>avatarMarkup(m.id)).join('')+''+avatarMarkup('supervisor')+'</div><h2>团队与监工已经就位。</h2><p>先编辑合作方式，再输入这次要完成的任务。</p>';$('timeline').append(note);note.id='welcome'}setStatus(data.room.status);renderRooms();app.events=new EventSource('/api/rooms/'+rid+'/events');app.events.onmessage=event=>{if(epoch===app.roomEpoch)onEvent(JSON.parse(event.data))};app.events.onopen=()=>setStatus(app.room.status);app.events.onerror=()=>{$('run-status').textContent='正在重新连接…'};$('timeline').scrollTop=$('timeline').scrollHeight;localStorage.setItem('tiered-last-room',rid)}
function projectDialog(){$('project-title').value='';$('project-path').value=app.room?.project||app.rooms[0]?.project||'';$('project-dialog').showModal()}

bind('new-room',projectDialog);bind('welcome-create',projectDialog);bind('project-chip',()=>app.room?toast(app.room.project):projectDialog());
for(const button of document.querySelectorAll('[data-close]'))button.onclick=()=>button.closest('dialog').close();
for(const dialog of document.querySelectorAll('dialog'))dialog.addEventListener('click',event=>{if(event.target===dialog){const rect=dialog.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)dialog.close()}});
bind('browse',async()=>{const button=$('browse');button.disabled=true;button.textContent='等待选择…';try{const data=await api('/folder',{});if(data.path)$('project-path').value=data.path}finally{button.disabled=false;button.textContent='浏览文件夹'}});
$('project-form').onsubmit=async event=>{event.preventDefault();try{const path=$('project-path').value.trim();const title=$('project-title').value.trim()||path.split(/[\\/]/).filter(Boolean).pop();const room=await api('/rooms',{project:path,title});app.rooms.unshift(room);$('project-dialog').close();await openRoom(room.id);teamDialog()}catch(error){toast(error.message)}};
bind('team-open',teamDialog);bind('team-top',teamDialog);
bind('agent-add',()=>memberDialog());
bind('picker-agent-add',()=>{$('team-picker-dialog').close();$('team-dialog').close();$('settings-open').click()});
$('member-backend').onchange=memberFields;
$('member-endpoint').addEventListener('input',refreshMemberModels);
$('member-name').addEventListener('input',memberFields);
$('member-form').onsubmit=async event=>{
 event.preventDefault();const button=$('member-save');button.disabled=true;
 try{
  const id=app.editMember||('m-'+crypto.randomUUID().replaceAll('-','').slice(0,16));
  const m={name:$('member-name').value.trim(),group:$('member-group').value.trim(),backend:$('member-backend').value,model:memberModel.value,effort:$('member-effort').value||null,base_url:$('member-endpoint').value.trim(),no_auth:$('member-no-auth').checked&&$('member-backend').value==='openai-compatible',create:!app.editMember};
  await queueMutation('/agents/'+id,m);app.editMember=id;
  if($('member-key').value.trim()){await queueMutation('/agents/'+id+'/key',{key:$('member-key').value.trim()});$('member-key').value=''}
  renderTeam();if($('team-picker-dialog').open)renderPicker();$('member-dialog').close();toast('Agent 已保存，可在各协作室中邀请加入');
 }catch(e){toast(e.message)}finally{button.disabled=false}
};
$('room-member-form').onsubmit=async event=>{event.preventDefault();if(!roomMemberModel.reportValidity())return;try{await saveMemberFields(app.editRoomMember,{model:roomMemberModel.value,effort:$('room-member-effort').value||null});$('room-member-dialog').close();toast('已保存此协作室的成员设置')}catch(error){toast(error.message)}};
bind('room-member-remove',async()=>{await setMembership(app.editRoomMember,false);$('room-member-dialog').close()});
bind('settings-open',async()=>{$('settings-dialog').showModal();renderConnections();$('settings-note').textContent='正在同步已保存的 Agent 与连接状态…';await syncConnections(true)});
bind('check-connections',async()=>{const button=$('check-connections');button.disabled=true;try{await Promise.all([syncConnections(true),refreshCatalog(),refreshUsage()])}finally{button.disabled=false}});
bind('refresh-models',async()=>{const button=$('refresh-models');button.disabled=true;try{await refreshCatalog();toast('正在更新四家模型目录，已保存的模型选择保持不变')}finally{button.disabled=false}});
$('model-form').onsubmit=async event=>{event.preventDefault();try{const provider=app.customProvider;const model=$('custom-model').value.trim();if(provider==='supervisor'){roomPrefs().supervisor.model=model;roomPrefs().supervisor.effort=null;await savePrefs();renderSupervisor()}else{await saveMemberFields(provider,{model,effort:null})} $('model-dialog').close();toast('模型已更新')}catch(error){toast(error.message)}};
$('model-dialog').addEventListener('close',()=>{if(app.customProvider==='supervisor')renderSupervisor();else if(app.customProvider&&getMember(app.customProvider))renderModel(app.customProvider)});
$('pause-before').onchange=async()=>{roomPrefs().pause_before_execution=$('pause-before').checked;try{await savePrefs()}catch(e){toast(e.message)}};
$('supervisor-provider').onchange=async()=>{try{const provider=$('supervisor-provider').value;roomPrefs().supervisor.provider=provider;roomPrefs().supervisor.model=getAgent(provider).model;roomPrefs().supervisor.effort=nextEffort(effortModel(modelList(getAgent(provider)),getAgent(provider).model),getAgent(provider).effort);renderSupervisor();await savePrefs()}catch(e){toast(e.message)}};
$('supervisor-model').onchange=async()=>{try{const value=$('supervisor-model').value;if(value==='__custom'){customDialog('supervisor');return}roomPrefs().supervisor.model=value;roomPrefs().supervisor.effort=nextEffort(effortModel(modelList(getAgent(roomPrefs().supervisor.provider)),value),roomPrefs().supervisor.effort);renderSupervisor();await savePrefs()}catch(e){toast(e.message)}};
$('supervisor-effort').onchange=async()=>{try{roomPrefs().supervisor.effort=$('supervisor-effort').value||null;await savePrefs()}catch(e){toast(e.message)}};
bind('policy-open',policyDialog);bind('policy-top',policyDialog);
$('policy-form').onsubmit=async event=>{event.preventDefault();try{const text=$('policy-text').value.trim();if(!text)throw new Error('请输入合作方式。');roomPrefs().supervisor.max_steps=Number($('max-steps').value);if($('policy-default').checked)await queueMutation('/settings',{cooperation_policy:text});await savePrefs();if(app.room)app.policy=await api('/rooms/'+app.room.id+'/policy',{text});renderPolicy();$('policy-dialog').close();toast(app.room?.status==='running'?'合作方式已更新，当前步骤结束后生效':'合作方式已交给监工')}catch(e){toast(e.message)}};
$('mode').onchange=composerState;$('target').onchange=composerState;
$('composer').onsubmit=async event=>{event.preventDefault();if(!app.room){projectDialog();return}const text=$('prompt').value.trim();if(!text)return;try{await saveChain;const response=await api('/rooms/'+app.room.id+'/send',{text,mode:$('mode').value,target:$('target').value});$('prompt').value='';if(response.status==='started'){$('activity-text').textContent='正在安排本次协作…'}else toast('已补充合作要求，当前步骤结束后生效')}catch(error){toast(error.message)}};
$('prompt').onkeydown=event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();$('composer').requestSubmit()}};
bind('stop',async()=>{if(!app.room)return;$('stop').disabled=true;try{await api('/rooms/'+app.room.id+'/stop',{})}finally{$('stop').disabled=false}});bind('resume',async()=>{await api('/rooms/'+app.room.id+'/resume',{});$('resume').hidden=true});
bind('export',()=>{if(!app.room){toast('先创建一个协作室');return}const link=document.createElement('a');link.href='/api/rooms/'+app.room.id+'/export';link.download='tiered-chat.md';link.click()});
bind('help',()=>$('help-dialog').showModal());
bind('recovery-open',async()=>{if(!app.room){toast('先选择协作项目');return}const data=await api('/rooms/'+app.room.id+'/recovery',{});$('recovery-list').innerHTML=data.pending.length?data.pending.map(r=>'<div class="recovery-record"><strong>'+escape(r.path)+'</strong><br>状态：'+escape(r.status)+'<br>记录：'+escape(r.transaction_id)+'</div>').join(''):'<p class="muted">没有待确认的文件写入记录。</p>';$('recovery-confirm').disabled=!data.pending.length;$('recovery-dialog').showModal()});
bind('recovery-confirm',async()=>{const data=await api('/rooms/'+app.room.id+'/recovery',{acknowledge:true});$('recovery-list').innerHTML='<p class="muted">已核对并确认 '+data.acknowledged+' 条记录。</p>';$('recovery-confirm').disabled=true});

async function init(){try{const data=await api('/bootstrap');app.token=data.token;app.prefs=data.preferences;app.modelCatalog=data.model_catalog;app.usageData=data.account_usage||{agents:{},refreshing:false};app.connections=data.connections;app.logins=data.logins||{};app.rooms=data.rooms;avatarManager.update(data.avatars);renderProviders();renderRooms();composerState();applyCatalog(data.model_catalog);applyUsage(data.account_usage);renderCatalogViews();const last=localStorage.getItem('tiered-last-room');if(last&&app.rooms.some(r=>r.id===last))await openRoom(last)}catch(error){toast('连接本机服务失败：'+error.message);$('run-status').textContent='连接失败'}}
document.addEventListener('focusout',event=>{if(event.target.tagName==='SELECT')setTimeout(renderCatalogViews,0)});
init();

let artifactData={};
bind('artifacts-open',async()=>{if(!app.room){toast('先选择一个项目');return}artifactData=await api('/rooms/'+app.room.id+'/artifacts');$('artifact-text').textContent=artifactData.diff||'没有待查看的变更。';$('artifacts-dialog').showModal()});
for(const button of document.querySelectorAll('[data-artifact]'))button.onclick=()=>{$('artifact-text').textContent=artifactData[button.dataset.artifact]||'没有内容。'};

function renderContexts(){
 for(const button of document.querySelectorAll('[data-context]')){
  const id=button.dataset.context;const executor=roomPrefs().executor===id;const role=executor?'executor':id==='supervisor'?'supervisor':'discussion';const entry=contextEntries.find(e=>e.identity===id&&e.role===role);
  button.textContent=(executor?'执行者 · 仅监工契约':id==='supervisor'?'监工 · 群原文共享':'讨论成员 · 群原文共享')+(entry?' · '+({prepared:'准备中',delivered:'已投递',failed:'失败',interrupted:'已中断'}[entry.status]||entry.status)+(executor&&entry.contract?' 契约 #'+entry.contract.revision:' '+entry.messages.length+' 条原文'):'');
  button.onclick=()=>contextDialog(id);
 }
}
function contextDialog(id){
 $('context-title').textContent=(names[id]||id)+' · 上下文投递';const host=$('context-detail');host.innerHTML='';
 const entries=contextEntries.filter(e=>e.identity===id).slice(0,12);
 if(!entries.length)host.textContent=roomPrefs().executor===id?'此执行者只接收监工执行契约，尚无投递。':'尚无投递记录。下次发言会直接同步群原文。';
 for(const e of entries){
  const section=document.createElement('section');section.className='context-entry';
  section.innerHTML='<strong>'+escape(e.mode)+' · '+escape(e.phase)+'</strong><p>'+escape(({prepared:'准备调用',delivered:'已完成投递',failed:'调用失败',interrupted:'调用中断'}[e.status]||e.status))+' · 本次输入估算 '+Number(e.estimated_input_tokens).toLocaleString()+' tokens</p><p>本地活动预算 '+Number(e.budget.soft).toLocaleString()+' · '+escape(e.budget.source)+'</p>'+(e.rotation?'<p>'+escape(e.rotation)+'</p>':'')+'<small>'+(e.role==='executor'?'执行者不会加载群原文；本次执行要求见下方。':'本次 '+e.messages.length+' 条原文；'+e.not_loaded.length+' 条未自动加载，可按消息 ID 检索。')+'</small>';
  if(e.usage&&Object.keys(e.usage).length){const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent='运行器返回的用量';const pre=document.createElement('pre');pre.textContent=JSON.stringify(e.usage,null,2);details.append(summary,pre);section.append(details)}
  if(e.contract){const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent='本次执行要求 · 第 '+e.contract.revision+' 版';const pre=document.createElement('pre');pre.textContent=e.contract.text;details.append(summary,pre);section.append(details)}
  const list=document.createElement('div');
  for(const m of e.messages){const b=document.createElement('button');b.className='context-message';b.textContent=(names[m.provider]||m.provider)+' · #'+m.seq+' · '+m.id.slice(0,12);b.title='SHA-256 '+m.hash;b.onclick=()=>{let pre=section.querySelector('pre');if(!pre){pre=document.createElement('pre');section.append(pre)}pre.textContent=app.messages.get(m.id)?.text||'请重新进入此群加载原文。'};list.append(b)}
  if(e.not_loaded.length){const omitted=document.createElement('details');const summary=document.createElement('summary');summary.textContent='未自动加载的消息 ID';omitted.append(summary);const p=document.createElement('p');p.textContent=e.not_loaded.map(m=>m.id).join(' · ');omitted.append(p);list.append(omitted)}
  section.append(list);host.append(section);
 }
 $('context-dialog').showModal();
}
$('room-executor').onchange=async()=>{try{await queueMutation('/rooms/'+app.room.id+'/configuration',{executor:$('room-executor').value||null});toast('已保存此协作室执行者；下次任务使用独立上下文')}catch(e){toast(e.message);renderTeam()}};

$('member-effort').onchange=()=>{memberEffort=$('member-effort').value||null};
$('room-member-effort').onchange=()=>{roomMemberEffort=$('room-member-effort').value||null};
for(const id of ['member-model','member-custom-model'])$(id).addEventListener(id.endsWith('model')&&id.includes('custom')?'input':'change',()=>{memberEffort=nextEffort(effortModel(modelList(editorSource()),memberModel.value),memberEffort);refreshMemberEffort()});
for(const id of ['room-member-model','room-member-custom-model'])$(id).addEventListener(id.includes('custom')?'input':'change',()=>{roomMemberEffort=nextEffort(effortModel(modelList(getAgent(app.editRoomMember)),roomMemberModel.value),roomMemberEffort);refreshRoomMemberEffort()});

const contextDefaults={soft_tokens:128000,output_tokens:32768,margin_tokens:6000,tool_rounds:16,overflow:'rotate'};
function fillContextSettings(value){for(const [field,id] of Object.entries({soft_tokens:'context-soft',output_tokens:'context-output',margin_tokens:'context-margin',tool_rounds:'context-tools',overflow:'context-overflow'}))$(id).value=value[field];$('context-steps').value=roomPrefs().supervisor.max_steps}
bind('context-settings-open',()=>{if(!app.room)return;fillContextSettings(roomPrefs().context||contextDefaults);$('context-settings-room').textContent=app.room.title+' · 设置仅应用于此协作室';$('context-settings-dialog').showModal()});
bind('context-reset',()=>{fillContextSettings(contextDefaults);$('context-steps').value=12});
$('context-settings-form').onsubmit=async event=>{event.preventDefault();try{const controls={soft_tokens:Number($('context-soft').value),output_tokens:Number($('context-output').value),margin_tokens:Number($('context-margin').value),tool_rounds:Number($('context-tools').value),overflow:$('context-overflow').value};await queueMutation('/rooms/'+app.room.id+'/configuration',{context:controls,supervisor:{max_steps:Number($('context-steps').value)}});$('context-settings-dialog').close();toast('已保存此协作室的上下文设置；满足预算的等待请求会继续')}catch(e){toast(e.message)}};
bind('force-budget',async()=>{if(!app.room)return;const button=$('force-budget');button.disabled=true;try{await saveChain;const response=await api('/rooms/'+app.room.id+'/force-budget',{text:$('prompt').value.trim(),mode:$('mode').value,target:$('target').value});if(response.status==='started')$('prompt').value='';toast(response.status==='resumed'?'原请求已继续；本次协作跳过本地软上限':'已开始本次超预算协作')}finally{composerState()}});
bind('avatars-open',()=>avatarManager.open());
