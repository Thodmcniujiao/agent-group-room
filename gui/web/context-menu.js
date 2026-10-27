// Shared menu for right-click, "⋯" buttons and the keyboard (Menu key / Shift+F10).
// Items: {label, action, danger, disabled, hint, checked, submenu:[...]} or {separator:true}.
let stack=[];let restoreFocus=null;

function closeFrom(level){while(stack.length>level)stack.pop().remove()}
export function closeMenu(){
  if(!stack.length)return;closeFrom(0);
  document.removeEventListener('mousedown',outside,true);window.removeEventListener('blur',closeMenu);
  window.removeEventListener('resize',closeMenu);document.removeEventListener('scroll',closeMenu,true);
  const target=restoreFocus;restoreFocus=null;if(target?.isConnected)target.focus({preventScroll:true});
}
function outside(event){if(!stack.some(menu=>menu.contains(event.target)))closeMenu()}

function place(menu,x,y,side){
  const rect=menu.getBoundingClientRect();const pad=6;
  let left=x,top=y;
  if(left+rect.width>innerWidth-pad)left=side?side.left-rect.width:innerWidth-rect.width-pad;
  if(top+rect.height>innerHeight-pad)top=Math.max(pad,innerHeight-rect.height-pad);
  menu.style.left=Math.max(pad,left)+'px';menu.style.top=Math.max(pad,top)+'px';
}

function items(menu){return [...menu.querySelectorAll(':scope>[role=menuitem]:not(:disabled)')]}

function build(list,level){
  const menu=document.createElement('div');menu.className='ctx-menu';menu.setAttribute('role','menu');
  for(const item of list){
    if(!item)continue;
    if(item.separator){const line=document.createElement('div');line.className='ctx-separator';line.setAttribute('role','separator');menu.append(line);continue}
    const button=document.createElement('button');button.type='button';button.setAttribute('role','menuitem');
    button.className='ctx-item'+(item.danger?' danger':'')+(item.checked?' checked':'');
    button.disabled=!!item.disabled;
    const label=document.createElement('span');label.textContent=item.label;button.append(label);
    if(item.hint||item.submenu){const hint=document.createElement('small');hint.textContent=item.submenu?'›':item.hint;button.append(hint)}
    if(item.title)button.title=item.title;
    if(item.submenu){
      button.setAttribute('aria-haspopup','menu');
      const open=focus=>{closeFrom(level+1);const r=button.getBoundingClientRect();const child=show(item.submenu,r.right-2,r.top-4,level+1,r);if(focus)items(child)[0]?.focus()};
      button.addEventListener('mouseenter',()=>open(false));
      button.addEventListener('click',()=>open(true));
      button.dataset.submenu='1';button._open=open;
    }else{
      button.addEventListener('mouseenter',()=>{closeFrom(level+1);button.focus({preventScroll:true})});
      button.addEventListener('click',()=>{closeMenu();Promise.resolve().then(()=>item.action?.())});
    }
    menu.append(button);
  }
  menu.addEventListener('keydown',event=>{
    const list=items(menu);const index=list.indexOf(document.activeElement);
    if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();const step=event.key==='ArrowDown'?1:-1;list[(index+step+list.length)%list.length]?.focus()}
    else if(event.key==='Home'){event.preventDefault();list[0]?.focus()}
    else if(event.key==='End'){event.preventDefault();list.at(-1)?.focus()}
    else if(event.key==='ArrowRight'&&document.activeElement?.dataset.submenu){event.preventDefault();document.activeElement._open(true)}
    else if(event.key==='ArrowLeft'&&level>0){event.preventDefault();closeFrom(level);items(stack[level-1]).find(b=>b.dataset.submenu)?.focus()}
    else if(event.key==='Escape'){event.preventDefault();event.stopPropagation();level>0?closeFrom(level):closeMenu()}
    else if(event.key==='Tab'){event.preventDefault();closeMenu()}
  });
  return menu;
}

function show(list,x,y,level,side){
  const menu=build(list,level);document.body.append(menu);stack.push(menu);place(menu,x,y,side);return menu;
}

/** Opens a menu at a point, or below/beside an anchor element. */
export function openMenu(list,{x,y,anchor,above=false,keyboard=false}={}){
  closeMenu();
  restoreFocus=document.activeElement;
  let r=null;if(anchor){r=anchor.getBoundingClientRect();x=r.left;y=r.bottom+4;if(x+220>innerWidth)x=r.right-220}
  const menu=show(list,x??0,y??0,0);
  if(r&&above)menu.style.top=Math.max(6,r.top-menu.getBoundingClientRect().height-4)+'px';
  document.addEventListener('mousedown',outside,true);window.addEventListener('blur',closeMenu);
  window.addEventListener('resize',closeMenu);document.addEventListener('scroll',closeMenu,true);
  (keyboard?items(menu)[0]:menu.querySelector('[role=menuitem]'))?.focus({preventScroll:true});
  if(!keyboard)menu.focus?.();
  return menu;
}

/** Wires right-click, the Menu key and Shift+F10 on an element to a menu builder. */
export function attachMenu(element,builder){
  element.addEventListener('contextmenu',event=>{event.preventDefault();event.stopPropagation();const list=builder();if(list)openMenu(list,{x:event.clientX,y:event.clientY})});
  element.addEventListener('keydown',event=>{
    if(event.key==='ContextMenu'||(event.shiftKey&&event.key==='F10')){event.preventDefault();const r=element.getBoundingClientRect();const list=builder();if(list)openMenu(list,{x:r.left+24,y:r.top+r.height/2,keyboard:true})}
  });
}
