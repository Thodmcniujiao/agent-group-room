const settings=await chrome.storage.local.get(['origin','token','autoRefresh','status']);
document.querySelector('#origin').value=settings.origin||'http://127.0.0.1:49315';
document.querySelector('#token').value=settings.token||'';
document.querySelector('#auto').checked=!!settings.autoRefresh;
document.querySelector('#status').textContent=settings.status||'等待配对';
document.querySelector('#form').onsubmit=async event=>{event.preventDefault();
  const origin=document.querySelector('#origin').value.trim();
  if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(origin)){document.querySelector('#status').textContent='只支持本机 127.0.0.1 地址';return;}
  await chrome.storage.local.set({origin,token:document.querySelector('#token').value.trim(),autoRefresh:document.querySelector('#auto').checked});
  document.querySelector('#status').textContent='配对已保存，请刷新 Google 账单页';
};
