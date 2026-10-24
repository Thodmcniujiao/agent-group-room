let approvalDialog;
async function openApprovalDocument(room,identifier,{api,toast}){
 const value=await api('/rooms/'+room.id+'/approval-documents/'+identifier);
 if(!approvalDialog){
  approvalDialog=document.createElement('dialog');approvalDialog.className='approval-dialog';approvalDialog.id='approval-document-dialog';approvalDialog.setAttribute('aria-labelledby','approval-reader-title');
  approvalDialog.innerHTML='<header class="approval-reader-header"><div><span>方案审批书</span><h2 id="approval-reader-title"></h2></div><button class="icon-button approval-close" aria-label="关闭审批文档">×</button></header><div class="approval-reader-toolbar"><span class="approval-reader-version"></span><nav aria-label="审批文档工具"><a class="outline approval-download">下载 HTML</a><a class="outline approval-markdown">下载 Markdown</a><button class="outline approval-print">打印 / 存为 PDF</button></nav></div><iframe title="完整方案审批文档" sandbox="allow-same-origin allow-modals"></iframe><footer class="approval-reader-footer"><p class="approval-reader-note"></p><button class="primary approval-confirm">批准此方案并继续</button></footer>';
  document.body.append(approvalDialog);approvalDialog.querySelector('.approval-close').onclick=()=>approvalDialog.close();
  approvalDialog.querySelector('.approval-print').onclick=()=>approvalDialog.querySelector('iframe').contentWindow.print();
 }
 const dialog=approvalDialog,confirm=dialog.querySelector('.approval-confirm');
 dialog.querySelector('h2').textContent=value.title;dialog.querySelector('.approval-reader-version').textContent=value.plan_ref+' · '+value.author+' · SHA-256 '+value.plan_sha256.slice(0,12);
 dialog.querySelector('iframe').src=value.url;
 for(const [selector,suffix] of [['.approval-download','?download=1'],['.approval-markdown','?format=md']]){const link=dialog.querySelector(selector);link.href=value.url+suffix;link.download='approval-'+value.plan_ref.replace('@','-v')+(selector.includes('markdown')?'.md':'.html')}
 dialog.querySelector('.approval-reader-note').textContent=value.can_approve?'请核对正文、范围、验收命令和成员意见。批准仅适用于当前文档版本。':value.status==='approved'?'此版本已批准。':'此文档已不是当前待审批版本，仅供查阅。';
 confirm.disabled=!value.can_approve;confirm.textContent=value.status==='approved'?'已批准':'批准 '+value.plan_ref+' 并继续';
 confirm.onclick=async()=>{confirm.disabled=true;try{const result=await api('/rooms/'+room.id+'/approve',{plan_ref:value.plan_ref,document_id:value.id,document_sha256:value.document_sha256});confirm.textContent='已批准';dialog.querySelector('.approval-reader-note').textContent=result.status==='queued'?'批准已记录。另一协作正在使用此项目，空闲后自动继续。':'批准已记录，协作正在继续。';toast(result.status==='queued'?'已批准，等待项目空闲后自动继续':'已批准，正在继续协作')}catch(error){toast(error.message);confirm.disabled=false}};
 if(!dialog.open)dialog.showModal();
}

export function renderWorkflowActions(node,message,{room,api,toast}){
 const actions=message.actions||[];if(!actions.length)return;
 const review=actions.find(a=>a.id==='review_plan');
 if(review){
  node.classList.add('approval-card-message');node.querySelector('.message-content').textContent='';
  const card=document.createElement('section');card.className='approval-document-card';card.setAttribute('aria-label','待审批方案文档');
  card.innerHTML='<div class="approval-file-icon" aria-hidden="true">▤</div><div class="approval-card-description"><span>方案审批书</span><strong></strong><small></small></div><button class="primary">打开审批文档</button>';
  card.querySelector('strong').textContent=review.title||review.plan_ref;card.querySelector('small').textContent=review.plan_ref+' · 宿主生成 · 完整文档';
  card.querySelector('button').onclick=()=>openApprovalDocument(room,review.document_id,{api,toast}).catch(e=>toast(e.message));node.append(card);return;
 }
 const host=document.createElement('div');host.className='message-actions';
 for(const action of actions){if(!['recheck','approve'].includes(action.id))continue;const button=document.createElement('button');button.className='outline';button.textContent=action.id==='approve'?'查看审批文档':action.label;button.dataset.action=action.id;button.onclick=async()=>{button.disabled=true;try{if(action.id==='approve'){const data=await api('/rooms/'+room.id);if(!data.approval_document)throw new Error('此方案确认已过期。');await openApprovalDocument(room,data.approval_document.id,{api,toast})}else{await api('/rooms/'+room.id+'/recheck',{});toast('正在复查环境；相同故障不会调用模型')}}catch(error){toast(error.message)}finally{button.disabled=false}};host.append(button)}
 node.append(host);
}

export function workflowSettings(grid){
 const fields=document.createElement('div');fields.className='workflow-settings-fields';
 fields.innerHTML='<label>监工上下文模式<select id="supervisor-context"><option value="state">账本 + 新增原文（默认）</option><option value="full">完整状态 + 新增原文</option></select><small>当前方案和相关争议保留原文；历史摘要由下方开关控制。</small></label><label>复查续接短语<input id="resume-words" placeholder="继续"><small>每行或用逗号分隔；精确匹配才直接续接原契约。</small></label><label>讨论容忍度 <output id="discussion-tolerance-label">3 · 默认</output><input id="discussion-tolerance" aria-label="讨论容忍度" type="range" min="1" max="5" step="1" value="3"><small>最多评审 / 表态轮数：1、2、3、5、8；达到上限保留分歧。</small></label><label>历史维护阈值<input id="context-maintain" type="number" min="0.3" max="0.9" step="0.05" value="0.6"><small>达到软预算的该比例后压缩或使用交接包。</small></label><label>交接包内近期原文条数<input id="context-handoff" type="number" min="0" max="50" step="1" value="8"><small>其他原文仍归档，可按消息 ID 读取。</small></label>';
 grid.append(fields);const get=id=>document.getElementById(id);
 for(const [id,section,note] of [
  ['context-output','context-output','API 讨论和编码执行均按此值发送，包含思考与正文；CLI 输出仍由运行器控制。'],
  ['context-tools','context-tools','默认 16 轮，可设 1–100；这是一次发言的模型续轮数，与监工协作轮数分别计算。']]){
  const label=get(id).closest('label');label.querySelector('small').textContent=note;
  const link=document.createElement('a');link.href='/assets/optimization-guide.html#'+section;link.target='_blank';link.rel='noopener noreferrer';link.className='optimization-help-link';link.textContent='功能与原理';label.append(link);
 }
 get('discussion-tolerance').oninput=()=>{get('discussion-tolerance-label').textContent=['','1 · 紧','2','3 · 默认','4','5 · 宽'][Number(get('discussion-tolerance').value)]};
 return {fill(context,settings){get('supervisor-context').value=settings.supervisor_context||'state';get('resume-words').value=(settings.resume_words||['继续']).join(', ');get('discussion-tolerance').value=settings.discussion_tolerance||3;get('discussion-tolerance').oninput();get('context-maintain').value=context.maintain_at??0.6;get('context-handoff').value=context.handoff_recent??8},value(){return {supervisor_context:get('supervisor-context').value,resume_words:get('resume-words').value.split(/[,，\n]/).map(s=>s.trim()).filter(Boolean),discussion_tolerance:Number(get('discussion-tolerance').value),context:{maintain_at:Number(get('context-maintain').value),handoff_recent:Number(get('context-handoff').value)}}}};
}

export function renderControl(node,control){
 if(!node)return;let details=node.querySelector('.control-record');
 if(!details){details=document.createElement('details');details.className='control-record';const summary=document.createElement('summary');summary.textContent='宿主控制记录（不进入成员上下文）';details.append(summary,document.createElement('pre'));node.append(details)}
 details.querySelector('pre').textContent=JSON.stringify(control,null,2);
}
