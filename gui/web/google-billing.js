const $=id=>document.getElementById(id);let bootstrap,token,agents=[];
async function api(path,body){const r=await fetch('/api/'+path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json','x-tiered-token':token}:{},body:body?JSON.stringify(body):undefined});const value=await r.json();if(!r.ok)throw Error(value.error||'请求失败');return value;}
function cfg(){return {mode:$('mode').value,project:$('project').value.trim(),billing_account:$('account').value.trim().toUpperCase(),location:$('location').value.trim()};}
function fill(){const agent=agents.find(a=>a.id===$('agent').value);const c=agent?.google_billing||{};
  $('mode').value=c.mode||'developer';$('project').value=c.project||'';$('account').value=c.billing_account||'';$('location').value=c.location||'global';links();}
function links(){const account=$('account').value.trim();$('credits').href=/^[A-Z0-9]{6}-[A-Z0-9]{6}-[A-Z0-9]{6}$/.test(account)?'https://console.cloud.google.com/billing/'+account+'/credits':'https://console.cloud.google.com/billing';}
async function refresh(){
  bootstrap=await api('bootstrap');token=bootstrap.token;agents=bootstrap.preferences.agents.filter(a=>{try{return new URL(a.base_url).hostname==='generativelanguage.googleapis.com';}catch{return false;}});
  const selected=$('agent').value;$('agent').replaceChildren(...agents.map(a=>new Option(a.name,a.id)));if(agents.some(a=>a.id===selected))$('agent').value=selected;
  const state=await api('google-billing');$('extension').value=state.extension_path;$('origin').value=state.origin;$('pairing').value=state.pairing_token;$('adc').textContent=state.adc?'已发现本机 ADC 文件；点击核验确认权限。':'尚未发现本机 ADC 文件。';
  $('reading').replaceChildren();for(const a of agents){const reading=bootstrap.account_usage.agents[a.id];if(!a.google_billing)continue;
    const title=document.createElement('h3');title.textContent=a.name;const detail=document.createElement('p');detail.textContent=reading?.detail||'未知';$('reading').append(title,detail);
    for(const b of reading?.balances||[]){const row=document.createElement('p');row.textContent=(b.route==='vertex'?'Vertex 赠金':'Gemini API 可用')+' · '+b.currency+' '+b.total_balance+(b.route==='developer'?'（预付 '+b.topped_up_balance+'，适用赠金 '+b.granted_balance+'）':'');$('reading').append(row);}}
  if(!$('reading').children.length)$('reading').textContent='尚无已配置的 Google 余额。先保存连接与调用方式，再同步账单页面。';
  $('raw').textContent=JSON.stringify(state.accounts,null,2);
}
async function action(work){try{await work();}catch(error){$('status').textContent=error.message;}}
$('agent').onchange=fill;$('account').oninput=links;
$('form').onsubmit=event=>{event.preventDefault();action(async()=>{if(!$('agent').value)throw Error('请先在 Agent 管理添加官方 Gemini 连接');await api('agents/'+$('agent').value,{google_billing:cfg()});await refresh();$('status').textContent='调用方式已保存，下次请求生效。';});};
$('verify').onclick=()=>action(async()=>{await api('google-billing',{action:'verify',config:cfg()});$('status').textContent='项目已启用结算，绑定账号一致，账号处于开放状态。模型权限仍需通过 Agent 调试验证。';});
$('refresh').onclick=()=>action(refresh);
$('copy').onclick=()=>action(async()=>{try{await navigator.clipboard.writeText($('pairing').value);$('status').textContent='配对码已复制';}catch{$('pairing').type='text';$('pairing').select();$('status').textContent='请复制选中的配对码';}});
$('rotate').onclick=()=>action(async()=>{await api('google-billing',{action:'rotate'});await refresh();$('status').textContent='旧配对码已失效，请更新扩展设置';});
await action(async()=>{await refresh();fill();});
setInterval(()=>action(refresh),30000);
