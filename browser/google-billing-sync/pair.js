/* Pair only through the user's click on the local application's settings page. */
document.addEventListener('click',async event=>{
  if(!event.isTrusted||!event.target.closest('#pair-extension'))return;
  const origin=document.querySelector('#origin')?.value;
  const token=document.querySelector('#pairing')?.value;
  const result=await chrome.runtime.sendMessage({type:'pair',origin,token,autoRefresh:true});
  const status=document.querySelector('#status');if(status)status.textContent=result?.ok?'扩展已配对，已启用每 5 分钟刷新账单页；请刷新已打开的 Google 账单页。':result?.error||'配对失败';
});
