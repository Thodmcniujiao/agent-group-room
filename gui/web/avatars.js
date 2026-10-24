export class AvatarManager {
  constructor(dialog,{api,people,changed,notice}){
    Object.assign(this,{dialog,api,people,changed,notice});this.images={};this.size=32;
    const controls=document.createElement('section');controls.className='avatar-size-settings';controls.innerHTML='<label for="chat-avatar-size">聊天框头像大小 <output for="chat-avatar-size">32 px</output></label><input id="chat-avatar-size" type="range" min="24" max="96" step="1" value="32"><div class="avatar-size-preview"><span class="avatar you chat-avatar-preview" data-avatar="you" data-letter="你">你</span><p>拖动即时预览，松开后自动保存。应用于全部协作室的聊天头像。</p><button type="button" class="outline">恢复 32 px</button></div>';
    dialog.querySelector('[data-avatar-list]').before(controls);this.sizeControls=controls;
    const slider=controls.querySelector('input');slider.oninput=()=>this.previewSize(Number(slider.value));slider.onchange=()=>this.saveSize(Number(slider.value));controls.querySelector('button').onclick=()=>this.saveSize(32);
  }
  previewSize(size){document.documentElement.style.setProperty('--chat-avatar-size',size+'px');this.sizeControls.querySelector('input').value=size;this.sizeControls.querySelector('output').textContent=size+' px'}
  updateDisplay(value){this.size=value?.chat_avatar_size??32;this.previewSize(this.size)}
  async saveSize(size){const slider=this.sizeControls.querySelector('input');slider.disabled=true;try{const data=await this.api('/display',{chat_avatar_size:size});this.updateDisplay(data.display);this.notice('聊天头像大小已保存')}catch(error){this.previewSize(this.size);this.notice(error.message)}finally{slider.disabled=false}}
  update(images){this.images=images||{};this.apply();if(this.dialog.open)this.render()}
  apply(root=document){
    for(const node of root.querySelectorAll('[data-avatar]')){
      const image=this.images[node.dataset.avatar]||this.images[node.dataset.avatarFallback];
      if(image){
        let img=node.querySelector('img');
        if(!img){img=document.createElement('img');img.alt='';node.replaceChildren(img)}
        if(img.getAttribute('src')!==image)img.src=image;
      }else node.textContent=node.dataset.letter||this.people().find(p=>p.id===node.dataset.avatar)?.name.slice(0,1)||'?';
    }
  }
  open(id){this.render();if(!this.dialog.open)this.dialog.showModal();if(id)this.dialog.querySelector('[data-person="'+id+'"]')?.scrollIntoView({block:'center'})}
  render(){
    const host=this.dialog.querySelector('[data-avatar-list]');host.replaceChildren();
    for(const person of this.people()){
      const row=document.createElement('section');row.className='avatar-person';row.dataset.person=person.id;
      const preview=document.createElement('span');preview.className='avatar '+(person.avatarClass||person.backend||person.id);preview.dataset.avatar=person.id;preview.dataset.letter=person.letter||person.name.slice(0,1);
      if(person.fallback)preview.dataset.avatarFallback=person.fallback;
      const identity=document.createElement('div');const name=document.createElement('strong');name.textContent=person.name;
      const detail=document.createElement('small');detail.textContent=person.detail||'已保存 Agent';identity.append(name,detail);
      const input=document.createElement('input');input.type='file';input.accept='image/png,image/jpeg,image/webp';input.hidden=true;input.setAttribute('aria-label','上传 '+person.name+' 的头像');
      const choose=document.createElement('button');choose.type='button';choose.className='outline';choose.textContent='上传图片';choose.setAttribute('aria-label','上传 '+person.name+' 的头像');choose.onclick=()=>input.click();
      const reset=document.createElement('button');reset.type='button';reset.textContent='恢复默认';reset.disabled=!this.images[person.id];reset.setAttribute('aria-label','恢复 '+person.name+' 的默认头像');
      reset.onclick=()=>this.save(person.id,null).catch(e=>this.notice(e.message));
      input.onchange=async()=>{try{if(input.files[0])await this.save(person.id,await prepareAvatar(input.files[0]))}catch(e){this.notice(e.message)}finally{input.value=''}};
      row.append(preview,identity,choose,reset,input);host.append(row);
    }
    this.apply(host);
  }
  async save(id,image){const person=this.people().find(p=>p.id===id);if(!person)throw new Error('该成员已不存在，请重新打开头像设置。');const data=await this.api(person.savePath||'/avatars/'+id,{image});this.update(data.avatars);this.changed();this.notice(image?'头像已更新':'已恢复默认头像')}
}

async function prepareAvatar(file){
  if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>15*1024*1024)throw new Error('请选择 15 MB 以内的 PNG、JPEG 或 WebP 图片。');
  const bitmap=await createImageBitmap(file);const canvas=document.createElement('canvas');canvas.width=canvas.height=256;
  const side=Math.min(bitmap.width,bitmap.height);canvas.getContext('2d').drawImage(bitmap,(bitmap.width-side)/2,(bitmap.height-side)/2,side,side,0,0,256,256);bitmap.close();
  let value=canvas.toDataURL('image/png');
  if(value.length>87000)value=canvas.toDataURL('image/webp',0.82);
  if(value.length>87000)throw new Error('图片压缩后仍然过大，请换一张较简单的图片。');
  return value;
}
