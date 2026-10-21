import { catalogText, ModelSelect } from './model-directory.js';
import { effortModel, nextEffort, renderReasoningEffort } from './reasoning-effort.js';
const states={running:'运行中',complete:'完成',error:'失败',stopped:'已停止'};

export class AgentDebugConsole {
  constructor(dialog,{api,models,catalog}) {
    this.dialog=dialog;this.api=api;this.models=models;this.catalog=catalog;this.agent=null;
    this.jobs=new Map();this.nodes=new Map();this.drafts=new Map();this.generation=0;
    this.$=selector=>dialog.querySelector(selector);
    this.picker=new ModelSelect(this.$('[data-model]'),this.$('[data-custom-model]'),this.$('[data-custom-model-wrap]'));
    const modelChanged=()=>{this.effort=nextEffort(effortModel(this.models(this.agent),this.picker.value),this.effort);this.refreshEffort()};
    this.$('[data-model]').addEventListener('change',modelChanged);
    this.$('[data-custom-model]').addEventListener('input',modelChanged);
    this.$('[data-effort]').onchange=()=>{this.effort=this.$('[data-effort]').value||null};
    this.$('form').onsubmit=event=>{event.preventDefault();this.send()};
    this.$('[data-test]').onclick=()=>{this.$('textarea').value='这是连接测试。请只回复：连接成功';this.send()};
    this.$('[data-stop]').onclick=()=>this.stop();
    this.$('textarea').onkeydown=event=>{
      if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();this.$('form').requestSubmit()}
    };
    dialog.addEventListener('close',()=>{
      this.remember();this.stream?.close();clearInterval(this.timer);this.generation++;
    });
  }

  remember(){
    if(this.agent)this.drafts.set(this.agent.id,{prompt:this.$('textarea').value,model:this.picker.value,effort:this.$('[data-effort]').value});
  }

  async open(agent){
    this.remember();this.stream?.close();clearInterval(this.timer);
    const generation=++this.generation;this.agent=agent;this.activeJob=null;this.jobs.clear();this.nodes.clear();
    this.$('[data-transcript]').replaceChildren();this.$('[data-title]').textContent=agent.name+' · 调试控制台';
    this.$('[data-connection]').textContent=agent.base_url||agent.name+' 本机 CLI 登录';
    const draft=this.drafts.get(agent.id);
    this.picker.setValue(draft?.model||agent.model);
    this.effort=draft?draft.effort:agent.effort;
    this.$('textarea').value=draft?.prompt||'';
    this.refreshModels();
    this.notice('正在读取此 Agent 的调试记录…');this.controls(true);
    if(!this.dialog.open)this.dialog.showModal();
    try{
      const data=await this.api('/agents/'+agent.id+'/debug');
      if(generation!==this.generation||!this.dialog.open)return;
      for(const job of data.jobs)this.render(job);
      const running=data.jobs.find(job=>job.status==='running');
      this.notice(running?'测试继续运行中。':'每条测试指令独立发送，不携带项目或群聊上下文。');
      this.controls(Boolean(running));
      if(running)this.follow(running);
    }catch(error){if(generation===this.generation){this.notice(error.message,true);this.controls(false)}}
    if(generation===this.generation&&this.dialog.open)this.timer=setInterval(()=>this.duration(),200);
  }

  notice(text,error=false){this.$('[data-notice]').textContent=text;this.$('[data-notice]').classList.toggle('error',error)}

  refreshModels(){
    if(!this.agent)return;
    this.picker.update(this.models(this.agent));
    this.$('[data-model-note]').textContent=catalogText(this.catalog(this.agent));
    this.refreshEffort();
  }

  refreshEffort(){renderReasoningEffort(this.$('[data-effort]'),effortModel(this.models(this.agent),this.picker.value),this.effort,{running:this.running})}

  controls(running){
    this.$('[type=submit]').disabled=running;this.$('[data-test]').disabled=running;
    this.running=running;this.picker.disabled=running;this.refreshEffort();
    this.$('[data-stop]').hidden=!running||!this.activeJob;this.$('[data-stop]').disabled=false;
  }

  render(job){
    const host=this.$('[data-transcript]');const follow=host.scrollHeight-host.scrollTop-host.clientHeight<80;
    this.jobs.set(job.id,{...job,received:Date.now()});
    while(this.jobs.size>8){
      const old=[...this.jobs.keys()].find(id=>id!==job.id&&this.jobs.get(id).status!=='running');
      if(!old)break;this.nodes.get(old)?.remove();this.nodes.delete(old);this.jobs.delete(old);
    }
    let node=this.nodes.get(job.id);
    if(!node){
      node=document.createElement('article');node.className='debug-entry';node.dataset.job=job.id;
      node.innerHTML='<div class="debug-entry-meta"><strong></strong><span class="debug-job-state"></span><time></time></div><pre class="debug-command"></pre><pre class="debug-output"></pre><pre class="debug-error"></pre>';
      this.nodes.set(job.id,node);host.append(node);
    }
    node.querySelector('strong').textContent=job.model;
    node.querySelector('time').textContent=new Date(job.started).toLocaleTimeString('zh-CN');
    node.querySelector('.debug-command').textContent='> '+job.prompt;
    node.querySelector('.debug-output').textContent=job.output||(job.status==='running'?job.activity||'等待模型输出…':'（没有可见输出）');
    node.querySelector('.debug-error').textContent=job.error;node.dataset.status=job.status;
    this.duration();
    if(follow)host.scrollTop=host.scrollHeight;
  }

  duration(){
    for(const job of this.jobs.values()){
      const ms=job.duration_ms+(job.status==='running'?Date.now()-job.received:0);
      this.nodes.get(job.id).querySelector('.debug-job-state').textContent=(states[job.status]||job.status)+' · '+(ms/1000).toFixed(1)+' 秒';
    }
  }

  follow(job){
    this.stream?.close();this.activeJob=job.id;
    this.controls(true);
    const generation=this.generation;
    this.stream=new EventSource('/api/debug/'+job.id+'/events');
    this.stream.onmessage=event=>{
      if(generation!==this.generation)return;
      const update=JSON.parse(event.data);this.render(update);
      if(update.status!=='running'){
        this.stream.close();this.activeJob=null;this.controls(false);
        this.notice(update.status==='error'?update.error:(states[update.status]+' · '+(update.duration_ms/1000).toFixed(1)+' 秒'),update.status==='error');
      }
    };
    this.stream.onerror=()=>{if(generation===this.generation)this.notice('输出连接暂时中断，正在重新连接…')};
  }

  async send(){
    if(!this.agent||this.$('[type=submit]').disabled)return;
    if(!this.picker.reportValidity())return;
    const prompt=this.$('textarea').value.trim();if(!prompt){this.$('textarea').focus();return}
    const generation=this.generation;this.controls(true);this.notice('正在发起独立测试…');
    try{
      const job=await this.api('/agents/'+this.agent.id+'/debug',{prompt,model:this.picker.value,effort:this.$('[data-effort]').value||null});
      if(generation!==this.generation)return;
      this.$('textarea').value='';this.render(job);this.follow(job);this.notice('模型正在回复，可随时停止。');
    }catch(error){if(generation===this.generation){this.controls(false);this.notice(error.message,true)}}
  }

  async stop(){
    if(!this.activeJob)return;const generation=this.generation;this.$('[data-stop]').disabled=true;
    try{const job=await this.api('/debug/'+this.activeJob+'/cancel',{});if(generation!==this.generation)return;this.render(job);this.stream?.close();this.activeJob=null;this.controls(false);this.notice('已停止测试。')}
    catch(error){if(generation===this.generation){this.notice(error.message,true);this.$('[data-stop]').disabled=false}}
  }
}
