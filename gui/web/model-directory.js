// All model pickers use the same per-connection directory and formatting.
export function modelLabel(model){
  if(!model.label||model.label===model.id)return model.id;
  return model.label.includes(model.id)?model.label:model.label+' · '+model.id;
}

// A real select shows the complete directory, independent of the current text.
export class ModelSelect {
  constructor(select,input,manual){
    this.select=select;this.input=input;this.manual=manual;this.models=[];
    select.addEventListener('change',()=>{this.manualState();if(select.value==='__custom')input.focus()});
    select.addEventListener('blur',()=>{if(this.pending){const list=this.pending;this.pending=null;this.update(list)}});
  }
  get value(){return this.select.value==='__custom'?this.input.value.trim():this.select.value}
  setValue(value){this.current=value||'';this.input.value='';this.fill(false)}
  update(models){
    if(document.activeElement===this.select){this.pending=models;return}
    this.current=this.value||this.current||'';const manual=this.select.value==='__custom';this.models=models;this.fill(manual);
  }
  fill(manual){
    const value=this.current;this.select.replaceChildren();
    const add=(id,label)=>{const option=document.createElement('option');option.value=id;option.textContent=label;this.select.append(option)};
    add('','请选择模型');for(const model of this.models)add(model.id,modelLabel(model));
    if(value&&!this.models.some(m=>m.id===value))add(value,value+' · 当前保存，目录未列出');
    add('__custom','＋ 自定义模型 ID');this.select.value=manual?'__custom':value;
    this.manualState();
  }
  manualState(){const manual=this.select.value==='__custom';this.manual.hidden=!manual;this.input.required=manual}
  reportValidity(){return this.select.reportValidity()&&(this.manual.hidden||this.input.reportValidity())}
  set disabled(value){this.select.disabled=value;this.input.disabled=value}
}

export function catalogText(entry){
  if(!entry)return '正在读取模型目录…';
  const count=entry.models?.length||0;
  if(entry.state==='manual')return '此服务手动填写模型 ID；自动查询仅支持 OpenAI、Claude、Gemini、DeepSeek。';
  if(entry.state==='unconfigured')return entry.error;
  if(entry.state==='loading')return '正在更新模型目录…'+(count?' 暂用上次 '+count+' 个模型。':'');
  if(entry.state==='error')return entry.error+(count?'（缓存 '+count+' 个）':'');
  if(entry.state==='ready')return entry.source+' · '+count+' 个模型'+(entry.updated?' · '+new Date(entry.updated).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}):'');
  return count?'上次目录 · '+count+' 个模型；等待自动更新。':'保存连接与密钥后自动读取；也可手动填写模型 ID。';
}
