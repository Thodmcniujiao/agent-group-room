// Sidebar: collaboration groups (each with its own default folder), rooms, archive and their menus.
import { openMenu, attachMenu } from './context-menu.js';

const $=id=>document.getElementById(id);
const folderName=path=>String(path||'').split(/[\\/]/).filter(Boolean).pop()||String(path||'');
const store={get(key,fallback){try{return JSON.parse(localStorage.getItem(key))??fallback}catch{return fallback}},
  set(key,value){try{localStorage.setItem(key,JSON.stringify(value))}catch{}}};
const ACTIVE=['running','paused','budget_paused','queued'];

export function confirmAction({title,text,ok='删除',danger=true}){
  const dialog=$('confirm-dialog');$('confirm-title').textContent=title;$('confirm-text').textContent=text;
  const button=$('confirm-ok');button.textContent=ok;button.className=danger?'stop':'primary';
  dialog.returnValue='';dialog.showModal();
  return new Promise(resolve=>dialog.addEventListener('close',()=>resolve(dialog.returnValue==='ok'),{once:true}));
}

export function createSidebar(ctx){
  const {app,api,toast,escape,statuses}=ctx;
  const host=$('rooms');
  let collapsed=new Set(store.get('agr-collapsed-groups',[]));
  let showArchived=store.get('agr-show-archived',false);
  let dragging=null;

  const groups=()=>app.groups||[];
  const groupOf=room=>groups().find(g=>g.id===room.group_id)||null;
  const busy=room=>ACTIVE.includes(room.status);
  const saveCollapsed=()=>store.set('agr-collapsed-groups',[...collapsed]);

  function replaceRoom(value){
    const index=app.rooms.findIndex(r=>r.id===value.id);if(index>=0)app.rooms[index]={...app.rooms[index],...value};
    if(app.room?.id===value.id){Object.assign(app.room,value);ctx.roomChanged(app.room)}
    render();
  }
  async function patchRoom(room,body,message){
    try{replaceRoom(await api('/rooms/'+room.id+'/meta',body));if(message)toast(message)}catch(e){toast(e.message)}
  }

  // ----- dialogs -----
  let groupEdit=null;
  function groupDialog(group=null){
    groupEdit=group;
    $('group-dialog-title').textContent=group?'编辑分组':'新建分组';
    $('group-save').textContent=group?'保存分组':'创建分组';
    $('group-name').value=group?.name||'';
    $('group-path').value=group?.path||app.room?.project||'';
    const inside=group?app.rooms.filter(r=>r.group_id===group.id).length:0;
    $('group-apply-wrap').hidden=!inside;$('group-apply').checked=false;
    $('group-apply-text').textContent='同时把组内 '+inside+' 个协作改到新文件夹（运行中的会跳过）';
    $('group-dialog').showModal();$('group-name').focus();
  }
  $('group-form').addEventListener('submit',async event=>{
    event.preventDefault();
    const name=$('group-name').value.trim(),path=$('group-path').value.trim();
    try{
      if(groupEdit){
        const body={name};if(path!==groupEdit.path){body.path=path;body.apply_to_rooms=$('group-apply').checked}
        const result=await api('/groups/'+groupEdit.id,body);
        app.groups=groups().map(g=>g.id===result.group.id?result.group:g);
        for(const room of result.rooms)replaceRoom(room);
        if(result.skipped.length)toast(result.skipped.length+' 个协作正在运行，未更换文件夹');
      }else{
        const group=await api('/groups',{name,path});app.groups=[...groups(),group];collapsed.delete(group.id);
        if(pendingMove){const room=pendingMove;pendingMove=null;await patchRoom(room,{group_id:group.id},'已移到「'+group.name+'」')}
      }
      $('group-dialog').close();render();
    }catch(e){toast(e.message)}
  });
  $('group-dialog').addEventListener('close',()=>{pendingMove=null});
  for(const [button,input] of [['group-browse','group-path'],['room-meta-browse','room-meta-path']]){
    $(button).addEventListener('click',async()=>{const b=$(button);b.disabled=true;const label=b.textContent;b.textContent='等待选择…';
      try{const data=await api('/folder',{});if(data.path)$(input).value=data.path}catch(e){toast(e.message)}finally{b.disabled=false;b.textContent=label}});
  }

  let metaRoom=null,metaMode='name';
  function roomMetaDialog(room,mode){
    metaRoom=room;metaMode=mode;
    $('room-meta-title').textContent=mode==='name'?'重命名协作':'更换项目文件夹';
    $('room-meta-note').textContent=mode==='name'?'只修改侧栏和群聊中显示的名称。':'当前：'+room.project;
    $('room-meta-name-wrap').hidden=mode!=='name';$('room-meta-path-wrap').hidden=mode==='name';
    $('room-meta-name').required=mode==='name';$('room-meta-path').required=mode!=='name';
    $('room-meta-name').value=room.title;$('room-meta-path').value=room.project;
    $('room-meta-dialog').showModal();(mode==='name'?$('room-meta-name'):$('room-meta-path')).select();
  }
  $('room-meta-form').addEventListener('submit',async event=>{
    event.preventDefault();if(!metaRoom)return;
    const body=metaMode==='name'?{title:$('room-meta-name').value.trim()}:{project:$('room-meta-path').value.trim()};
    try{replaceRoom(await api('/rooms/'+metaRoom.id+'/meta',body));$('room-meta-dialog').close();
      toast(metaMode==='name'?'已重命名':'已更换项目文件夹；成员下次发言使用新会话')}catch(e){toast(e.message)}
  });

  // ----- actions -----
  let pendingMove=null;
  async function deleteRoom(room){
    if(busy(room)){toast('协作运行中，停止后才能删除。');return}
    const ok=await confirmAction({title:'删除「'+room.title+'」？',text:'将永久删除此协作的群聊、方案、审批记录、用量与日志。项目文件夹中的文件不会被改动。此操作无法撤销；只想收起可以改用“归档”。'});
    if(!ok)return;
    try{await api('/rooms/'+room.id+'/delete',{});app.rooms=app.rooms.filter(r=>r.id!==room.id);
      if(app.room?.id===room.id){ctx.roomRemoved(room);return}render();toast('已删除协作')}catch(e){toast(e.message)}
  }
  async function deleteGroup(group){
    const count=app.rooms.filter(r=>r.group_id===group.id).length;
    const ok=await confirmAction({title:'删除分组「'+group.name+'」？',text:count?'组内 '+count+' 个协作会移到“未分组”，协作本身和项目文件都不会删除。':'这个分组是空的。'});
    if(!ok)return;
    try{await api('/groups/'+group.id+'/delete',{});app.groups=groups().filter(g=>g.id!==group.id);
      for(const room of app.rooms)if(room.group_id===group.id)room.group_id=null;render();toast('已删除分组')}catch(e){toast(e.message)}
  }
  async function moveGroup(group,step){
    const list=groups().map(g=>g.id);const i=list.indexOf(group.id),j=i+step;if(j<0||j>=list.length)return;
    [list[i],list[j]]=[list[j],list[i]];
    try{app.groups=(await api('/groups/order',{ids:list})).groups;render()}catch(e){toast(e.message)}
  }

  function moveTargets(room){
    return [{label:'未分组',checked:!room.group_id,disabled:!room.group_id,action:()=>patchRoom(room,{group_id:null},'已移到“未分组”')},
      ...(groups().length?[{separator:true}]:[]),
      ...groups().map(g=>({label:g.name,checked:room.group_id===g.id,disabled:room.group_id===g.id,action:()=>patchRoom(room,{group_id:g.id},'已移到「'+g.name+'」')})),
      {separator:true},{label:'新建分组…',action:()=>{pendingMove=room;groupDialog()}}];
  }

  function roomMenu(room,{inSidebar=true}={}){
    const active=app.room?.id===room.id;
    const settings=tab=>async()=>{if(!active)await ctx.openRoom(room.id);ctx.openRoomSettings(tab)};
    return [
      inSidebar&&!active?{label:'打开',action:()=>ctx.openRoom(room.id)}:null,
      {label:'协作室设置',submenu:[{label:'成员与执行者',action:settings('team')},{label:'合作规则',action:settings('policy')},{label:'上下文',action:settings('context')}]},
      {separator:true},
      {label:'重命名…',action:()=>roomMetaDialog(room,'name')},
      {label:'移动到分组',submenu:moveTargets(room)},
      {label:'更换项目文件夹…',disabled:busy(room),title:busy(room)?'运行中不能更换':'',action:()=>roomMetaDialog(room,'path')},
      {label:'导出群聊 (Markdown)',action:()=>ctx.exportRoom(room)},
      {separator:true},
      room.archived?{label:'取消归档',action:()=>patchRoom(room,{archived:false},'已取消归档')}
        :{label:'归档',disabled:busy(room),action:()=>patchRoom(room,{archived:true},'已归档；可在侧栏底部“已归档”中找回')},
      {label:'删除协作…',danger:true,disabled:busy(room),action:()=>deleteRoom(room)}];
  }
  function groupMenu(group){
    const list=groups();const index=list.indexOf(group);
    return [{label:'在此分组新建协作',action:()=>ctx.newRoom(group)},
      {separator:true},
      {label:'编辑名称与文件夹…',action:()=>groupDialog(group)},
      {label:'上移',disabled:index<=0,action:()=>moveGroup(group,-1)},
      {label:'下移',disabled:index<0||index>=list.length-1,action:()=>moveGroup(group,1)},
      {separator:true},
      {label:'删除分组…',danger:true,action:()=>deleteGroup(group)}];
  }

  // ----- rendering -----
  function roomRow(room){
    const row=document.createElement('div');row.className='room-row';
    const link=document.createElement('button');link.type='button';
    link.className='room-link'+(app.room?.id===room.id?' active':'')+(busy(room)?' busy':'');
    link.title=room.title+'\n'+room.project;link.draggable=true;
    const group=groupOf(room);
    const place=!group||group.path!==room.project?' · '+escape(folderName(room.project)):'';
    link.innerHTML='<strong>'+escape(room.title)+'</strong><span><i class="room-dot '+escape(room.status)+'"></i>'+escape(statuses[room.status]||room.status)+place+'</span>';
    link.onclick=()=>ctx.openRoom(room.id).catch(e=>toast(e.message));
    link.addEventListener('dragstart',event=>{dragging=room;event.dataTransfer.effectAllowed='move';event.dataTransfer.setData('text/plain',room.title);row.classList.add('dragging')});
    link.addEventListener('dragend',()=>{dragging=null;row.classList.remove('dragging');host.querySelectorAll('.drop-target').forEach(n=>n.classList.remove('drop-target'))});
    attachMenu(link,()=>roomMenu(room));
    const more=document.createElement('button');more.type='button';more.className='row-more';more.textContent='⋯';
    more.setAttribute('aria-label',room.title+' 的更多操作');more.tabIndex=-1;
    more.onclick=event=>{event.stopPropagation();openMenu(roomMenu(room),{anchor:more})};
    row.append(link,more);return row;
  }
  function dropZone(node,groupId){
    node.addEventListener('dragover',event=>{if(!dragging||(dragging.group_id||null)===groupId)return;event.preventDefault();node.classList.add('drop-target')});
    node.addEventListener('dragleave',event=>{if(!node.contains(event.relatedTarget))node.classList.remove('drop-target')});
    node.addEventListener('drop',event=>{event.preventDefault();node.classList.remove('drop-target');const room=dragging;if(!room)return;
      const g=groups().find(x=>x.id===groupId);patchRoom(room,{group_id:groupId},'已移到'+(g?'「'+g.name+'」':'“未分组”'))});
  }
  function section({id,name,path,rooms,group}){
    const key=id||'__ungrouped';const open=!collapsed.has(key);
    const node=document.createElement('section');node.className='room-group'+(group?'':' ungrouped');
    const head=document.createElement('div');head.className='group-head';
    const toggle=document.createElement('button');toggle.type='button';toggle.className='group-toggle';toggle.setAttribute('aria-expanded',String(open));
    toggle.title=path?name+'\n'+path:name;
    toggle.innerHTML='<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 4l4 4-4 4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg><span class="group-name">'+escape(name)+'</span><span class="group-count">'+rooms.length+'</span>';
    toggle.onclick=()=>{open?collapsed.add(key):collapsed.delete(key);saveCollapsed();render()};
    head.append(toggle);
    if(group){
      const add=document.createElement('button');add.type='button';add.className='group-action';add.textContent='＋';add.title='在「'+name+'」新建协作';add.tabIndex=-1;
      add.onclick=()=>ctx.newRoom(group);
      const more=document.createElement('button');more.type='button';more.className='group-action';more.textContent='⋯';more.title='分组操作';more.tabIndex=-1;
      more.onclick=()=>openMenu(groupMenu(group),{anchor:more});
      head.append(add,more);attachMenu(toggle,()=>groupMenu(group));
    }
    node.append(head);dropZone(node,id||null);
    if(open){
      const body=document.createElement('div');body.className='group-body';
      if(rooms.length)for(const room of rooms)body.append(roomRow(room));
      else if(group){const empty=document.createElement('button');empty.type='button';empty.className='group-empty';empty.textContent='＋ 在这里新建协作';empty.onclick=()=>ctx.newRoom(group);body.append(empty)}
      node.append(body);
    }
    return node;
  }

  function render(){
    host.innerHTML='';
    const live=app.rooms.filter(r=>!r.archived),archived=app.rooms.filter(r=>r.archived);
    for(const group of groups())host.append(section({id:group.id,name:group.name,path:group.path,group,rooms:live.filter(r=>r.group_id===group.id)}));
    const loose=live.filter(r=>!groupOf(r));
    if(loose.length||!groups().length){
      if(groups().length)host.append(section({id:null,name:'未分组',rooms:loose}));
      else{const body=document.createElement('div');body.className='group-body flat';for(const room of loose)body.append(roomRow(room));dropZone(body,null);host.append(body)}
    }
    if(archived.length){
      const wrap=document.createElement('section');wrap.className='room-group archived';
      const toggle=document.createElement('button');toggle.type='button';toggle.className='archive-toggle';toggle.setAttribute('aria-expanded',String(showArchived));
      toggle.innerHTML='<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 4.5h11v2h-11zM3.5 6.5v6h9v-6M6.5 9h3" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg><span>已归档</span><span class="group-count">'+archived.length+'</span>';
      toggle.onclick=()=>{showArchived=!showArchived;store.set('agr-show-archived',showArchived);render()};
      wrap.append(toggle);
      if(showArchived){const body=document.createElement('div');body.className='group-body';for(const room of archived)body.append(roomRow(room));wrap.append(body)}
      host.append(wrap);
    }
    if(!app.rooms.length){const empty=document.createElement('p');empty.className='side-empty';empty.textContent='还没有协作。点击上方“新建协作”开始。';host.append(empty)}
  }

  $('group-add').addEventListener('click',()=>groupDialog());
  return {render,roomMenu,groupDialog,deleteRoom,roomMetaDialog};
}
