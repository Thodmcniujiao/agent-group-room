// Exact model metadata is the only source of selectable effort values.
export function effortModel(models,id){return models.find(model=>model.id===id)}
export function effortLevels(model){return model?.effort_source?model.efforts||[]:[]}
export function nextEffort(model,previous){
  const levels=effortLevels(model);
  if(levels.includes(previous))return previous;
  return levels.includes(model?.default_effort)?model.default_effort:null;
}

export function renderReasoningEffort(select,model,value,{running=false}={}){
  const levels=effortLevels(model);select.replaceChildren();
  const add=(value,label,title='')=>{
    const option=document.createElement('option');option.value=value;option.textContent=label;option.title=title;select.append(option);
  };
  add('',levels.length?'未指定（不发送强度参数）':'官方未返回推理强度数据');
  for(const level of levels)add(level,level,model.effort_descriptions?.[level]||'');
  select.value=levels.includes(value)?value:'';
  select.disabled=running||!levels.length;
  let note=select.parentElement.querySelector('[data-reasoning-note]');
  if(!note){note=document.createElement('small');note.dataset.reasoningNote='';note.className='field-note reasoning-note';select.parentElement.append(note)}
  note.textContent=!levels.length?'不指定推理强度；不向服务发送此参数。':
    value&&!levels.includes(value)?'已保存的 '+value+' 未在此模型的官方列表中返回，请重新选择。':
    model.default_effort?'官方默认值: '+model.default_effort:'';
  select.title=model?.effort_source||'官方模型目录未返回可选强度';
  return select.value||null;
}
