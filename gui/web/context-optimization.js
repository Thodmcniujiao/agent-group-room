const defaults={enabled:false,summary:{enabled:true,min_chars:400,max_chars:160,budget_ratio:.15,max_entries:30,uses:{supervisor_history:true,not_loaded:true,handoff:true,catch_up:true}},output_preview:{enabled:true,max_chars:6000},project_tools:true,brevity:false,experiments:{dual:false,headroom:false,repomap:false,toon:false,ccusage:false,comem:false,context_mode:false,claude_mem:false,llmlingua:false}};
const research=[['dual','双投递'],['headroom','Headroom'],['repomap','Aider RepoMap'],['toon','TOON'],['ccusage','ccusage'],['comem','CoMem'],['context_mode','Context Mode'],['claude_mem','Claude-Mem'],['llmlingua','LLMLingua']];
const copy=value=>JSON.parse(JSON.stringify(value));
const guideUrl='/assets/optimization-guide.html';
let guideDocument,guideDialog;

function helpLink(section,name,text='功能与原理'){
 const link=document.createElement('a');link.className='optimization-help-link';link.href=guideUrl+'#'+section;link.target='_blank';link.rel='noopener noreferrer';link.textContent=text;link.setAttribute('aria-label',name+'：'+text);return link;
}

async function openHelp(section){
 if(!guideDocument)guideDocument=fetch(guideUrl).then(response=>{if(!response.ok)throw new Error('暂时无法加载选项说明，请刷新后重试。');return response.text()}).then(html=>new DOMParser().parseFromString(html,'text/html')).catch(error=>{guideDocument=null;throw error});
 const source=(await guideDocument).getElementById(section);if(!source)throw new Error('未找到此选项的说明。');
 if(!guideDialog){
  guideDialog=document.createElement('dialog');guideDialog.id='optimization-help-dialog';guideDialog.className='optimization-help-dialog';guideDialog.setAttribute('aria-labelledby','optimization-help-title');
  guideDialog.innerHTML='<div class="dialog-heading"><h2 id="optimization-help-title"></h2><button type="button" class="close" aria-label="关闭选项说明">×</button></div><div class="optimization-guide-content" tabindex="0" role="region" aria-labelledby="optimization-help-title"></div><footer><a target="_blank" rel="noopener noreferrer">在新窗口阅读完整说明</a><button type="button" class="outline">返回设置</button></footer>';
  document.body.append(guideDialog);for(const button of guideDialog.querySelectorAll('button'))button.onclick=()=>guideDialog.close();
  guideDialog.querySelector('.optimization-guide-content').addEventListener('click',event=>{const link=event.target.closest('a');if(link?.getAttribute('href')?.startsWith('#')&&!event.ctrlKey&&!event.metaKey&&!event.shiftKey&&!event.altKey){event.preventDefault();openHelp(link.hash.slice(1)).catch(error=>{guideDialog.querySelector('.optimization-guide-content').textContent=error.message})}});
 }
 const content=source.cloneNode(true);content.removeAttribute('id');const heading=content.querySelector('h2');guideDialog.querySelector('h2').textContent=heading.textContent;heading.remove();
 guideDialog.querySelector('.optimization-guide-content').replaceChildren(content);guideDialog.querySelector('footer a').href=guideUrl+'#'+section;
 if(!guideDialog.open)guideDialog.showModal();guideDialog.scrollTop=0;guideDialog.querySelector('.optimization-guide-content').scrollTop=0;
}

export function optimizationSettings(grid,{getRoom,api,toast}){
 const fieldset=document.createElement('fieldset');fieldset.className='optimization-settings';fieldset.innerHTML='<legend>历史摘要与工具输出</legend><label class="optimization-toggle"><input id="optimization-enabled" type="checkbox">启用此房间的上下文优化</label><p class="optimization-note">切换设置后使用新上下文分支。完整原文继续保存；节省效果须由真实任务对照验证。</p><div class="optimization-options"><label class="optimization-toggle"><input id="summary-enabled" type="checkbox">作者自述摘要</label><label class="optimization-toggle"><input id="output-preview-enabled" type="checkbox">工具输出归档与有界预览</label><label class="optimization-toggle"><input id="project-tools-enabled" type="checkbox">只读项目读取与搜索</label><label class="optimization-toggle"><input id="brevity-enabled" type="checkbox">简洁发言指令</label></div><div class="optimization-uses"></div><details><summary>摘要与预览预算</summary><div class="optimization-parameters"><label>长回复阈值（字符）<input id="summary-min" type="number" min="0" max="5000"></label><label>摘要上限（字符）<input id="summary-max" type="number" min="40" max="400"></label><label>目录最多条数<input id="summary-entries" type="number" min="1" max="100"></label><label>目录预算比例<input id="summary-ratio" type="number" min="0.05" max="0.5" step="0.05"></label><label>输出预览上限（字符）<input id="output-preview-max" type="number" min="1000" max="50000"></label></div></details><details class="optimization-research"><summary>保留的实验路线 · 待日后测试</summary><p class="optimization-note">各路线独立保留开关，完成接入与验收后才能启用。当前版本均关闭。</p><div class="optimization-experiments"></div></details><button type="button" class="outline optimization-report-open">查看本房间用量与回取</button>';
 grid.append(fieldset);const get=id=>fieldset.querySelector('#'+id);let saved=copy(defaults);
 for(const [key,label] of [['supervisor_history','监工较早发言'],['not_loaded','未加载目录'],['handoff','分支交接'],['catch_up','新成员补看']]){
  const node=document.createElement('label');node.className='optimization-toggle';const input=document.createElement('input');input.type='checkbox';input.id='summary-use-'+key;node.append(input,document.createTextNode(label));fieldset.querySelector('.optimization-uses').append(node);
 }
 for(const [key,name] of research){const label=document.createElement('label');label.className='optimization-experiment';const input=document.createElement('input');input.type='checkbox';input.disabled=true;input.id='experiment-'+key;const span=document.createElement('span');span.textContent=name+' · 待测试';const small=document.createElement('small');small.dataset.experiment=key;label.append(input,span,small);fieldset.querySelector('.optimization-experiments').append(label)}
 for(const input of fieldset.querySelectorAll('input')){
  const label=input.closest('label');label.htmlFor=input.id;const row=document.createElement('div');row.className='optimization-option';label.before(row);row.append(label,helpLink(input.id,label.textContent.trim()));
 }
 fieldset.querySelector('.optimization-note').append(' ',helpLink('overview','优化说明手册','查看完整说明'));
 fieldset.append(helpLink('usage-report','用量与回取','用量口径与回取说明'));
 fieldset.addEventListener('click',event=>{const link=event.target.closest('.optimization-help-link');if(!link||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;event.preventDefault();openHelp(link.hash.slice(1)).catch(error=>toast(error.message))});
 function state(){const enabled=get('optimization-enabled').checked;fieldset.classList.toggle('optimization-off',!enabled);for(const input of fieldset.querySelectorAll('.optimization-options input,.optimization-uses input,.optimization-parameters input'))input.disabled=!enabled}
 get('optimization-enabled').onchange=state;
 fieldset.querySelector('.optimization-report-open').onclick=()=>openReport(getRoom(),api).catch(error=>toast(error.message));
 return {fill(settings={}){
  const value=settings.optimization||{};saved={...copy(defaults),...value,summary:{...copy(defaults.summary),...value.summary,uses:{...defaults.summary.uses,...value.summary?.uses}},output_preview:{...defaults.output_preview,...value.output_preview}};
  get('optimization-enabled').checked=saved.enabled;get('summary-enabled').checked=saved.summary.enabled;get('output-preview-enabled').checked=saved.output_preview.enabled;get('project-tools-enabled').checked=saved.project_tools;get('brevity-enabled').checked=saved.brevity;
  for(const key of Object.keys(defaults.summary.uses))get('summary-use-'+key).checked=saved.summary.uses[key];
  for(const [id,value] of [['summary-min',saved.summary.min_chars],['summary-max',saved.summary.max_chars],['summary-entries',saved.summary.max_entries],['summary-ratio',saved.summary.budget_ratio],['output-preview-max',saved.output_preview.max_chars]])get(id).value=value;
  for(const item of settings.optimization_experiments||[]){const small=fieldset.querySelector('[data-experiment="'+item.id+'"]');if(small)small.textContent=item.reason}
  state();
 },value(){return {optimization:{...saved,enabled:get('optimization-enabled').checked,project_tools:get('project-tools-enabled').checked,brevity:get('brevity-enabled').checked,
  summary:{...saved.summary,enabled:get('summary-enabled').checked,min_chars:Number(get('summary-min').value),max_chars:Number(get('summary-max').value),max_entries:Number(get('summary-entries').value),budget_ratio:Number(get('summary-ratio').value),uses:Object.fromEntries(Object.keys(defaults.summary.uses).map(key=>[key,get('summary-use-'+key).checked]))},output_preview:{enabled:get('output-preview-enabled').checked,max_chars:Number(get('output-preview-max').value)},experiments:copy(defaults.experiments)}}}};
}

export function renderAuthorSummary(node,message){
 if(!message.author_summary)return;const footer=document.createElement('div');footer.className='author-summary';footer.textContent='作者自述摘要 · '+message.author_summary.text;node.querySelector('.message-main')?.append(footer);
 if(message.summary_vote_mismatch){const warning=document.createElement('p');warning.className='summary-mismatch';warning.textContent='摘要立场与格式表态不一致；表态以原文格式行为准。';footer.append(warning)}
}

export function renderDelivery(section,entry){
 if(entry.feed_counts){const p=document.createElement('p');p.className='optimization-note';p.textContent='本次表示：全文 '+entry.feed_counts.full+' · 摘要 '+entry.feed_counts.menu+' · 仅编号 '+entry.feed_counts.id+'。这是投递记录，无法证明模型实际阅读或理解。';section.append(p)}
 if(entry.menu_entries?.length){const details=document.createElement('details');const title=document.createElement('summary');title.textContent='本次摘要与编号目录';const list=document.createElement('div');for(const item of entry.menu_entries){const p=document.createElement('p');p.className='directory-entry';p.textContent='#'+item.seq+' · '+item.provider+' · '+(item.feed==='menu'?'作者摘要：'+item.summary:'仅编号')+' · '+item.id;list.append(p)}details.append(title,list);section.append(details)}
 if(entry.message_retrievals?.length||entry.output_retrievals?.length){const p=document.createElement('p');p.className='optimization-note';p.textContent='本分支累计回取：消息片段 '+(entry.message_retrievals?.length||0)+' 次，输出片段 '+(entry.output_retrievals?.length||0)+' 次；消息所有分段齐全后才记为全文已投递。';section.append(p)}
}

let reportDialog;
async function openReport(room,api){
 if(!room)throw new Error('请先选择协作室。');
 if(!reportDialog){reportDialog=document.createElement('dialog');reportDialog.id='optimization-report-dialog';reportDialog.className='optimization-report-dialog';reportDialog.innerHTML='<div class="dialog-heading"><div><h2>用量与回取</h2><p class="optimization-report-room"></p></div><button type="button" class="close">×</button></div><label>分组<select class="optimization-report-by"><option value="member">成员</option><option value="role">角色</option><option value="session">会话分支</option><option value="day">日期</option><option value="adapter">适配器</option><option value="kind">发言类型</option></select></label><div class="optimization-report-body"></div><p class="optimization-note">输入总量已包含缓存读写，输出总量已包含推理。未知字段保持未知。尚未验证真实任务的净费用节省。</p><details><summary>已归档工具输出（最近 50 条）</summary><div class="optimization-output-list"></div><pre class="optimization-output-text"></pre><button type="button" class="outline optimization-output-next" hidden>读取下一段</button></details>';
  document.body.append(reportDialog);reportDialog.querySelector('.close').onclick=()=>reportDialog.close();
 }
 const dialog=reportDialog;dialog.querySelector('.optimization-report-room').textContent=room.title;const by=dialog.querySelector('.optimization-report-by');
 async function load(){
  const data=await api('/rooms/'+room.id+'/optimization-report?by='+by.value);const host=dialog.querySelector('.optimization-report-body');host.replaceChildren();
  if(!data.groups.length){const p=document.createElement('p');p.textContent='此房间尚无服务商用量记录。';host.append(p)}
  else{const table=document.createElement('table');const head=document.createElement('tr');for(const label of ['分组','请求','输入总量','缓存读取','输出总量','缓存命中率']){const th=document.createElement('th');th.textContent=label;head.append(th)}table.append(head);
   const format=counter=>counter.known_sum===null?'未知':counter.known_sum.toLocaleString()+(counter.unknown_requests?'（另 '+counter.unknown_requests+' 次未知）':'');
   for(const group of data.groups){const row=document.createElement('tr');for(const value of [group[data.by],group.requests,format(group.tokens.input),format(group.tokens.cached_read),format(group.tokens.output),group.cache_hit_rate===null?'未知':(group.cache_hit_rate*100).toFixed(1)+'%']){const cell=document.createElement('td');cell.textContent=value;row.append(cell)}table.append(row)}host.append(table)}
  const outputs=dialog.querySelector('.optimization-output-list');outputs.replaceChildren();dialog.querySelector('.optimization-output-text').textContent='';dialog.querySelector('.optimization-output-next').hidden=true;
  for(const output of data.outputs){const button=document.createElement('button');button.type='button';button.className='context-message';button.textContent=output.command+' · 退出码 '+output.exit_code+' · '+output.characters.toLocaleString()+' 字符 · 回取 '+output.retrievals+' 次'+(output.complete?'':' · 捕获不完整');button.onclick=()=>showOutput(output.id,0);outputs.append(button)}
  if(!data.outputs.length)outputs.textContent='尚无归档输出。';
 }
 async function showOutput(id,offset){const result=await api('/rooms/'+room.id+'/outputs/'+id+'?offset='+offset);dialog.querySelector('.optimization-output-text').textContent=result.text;const next=dialog.querySelector('.optimization-output-next');next.hidden=result.next_offset===null;next.onclick=()=>showOutput(id,result.next_offset)}
 by.onchange=load;await load();if(!dialog.open)dialog.showModal();
}
