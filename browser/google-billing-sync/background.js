const matches=url=>/^https:\/\/(console\.cloud\.google\.com\/billing\/[A-Z0-9-]+\/credits|aistudio\.google\.com\/billing)([/?#]|$)/.test(url||'');
chrome.runtime.onMessage.addListener((message,sender,respond)=>{
  (async()=>{
    if(message.type==='pair'){
      if(!/^http:\/\/127\.0\.0\.1:\d+\/assets\/google-billing\.html$/.test(sender.tab?.url||'')||new URL(sender.tab.url).origin!==message.origin||typeof message.token!=='string'||message.token.length<32)throw Error('配对必须来自本机设置页');
      await chrome.storage.local.set({origin:message.origin,token:message.token,autoRefresh:message.autoRefresh===true,status:'已配对，等待账单页面'});respond({ok:true});return;
    }
    if(!sender.tab||!matches(sender.tab.url))throw Error('不支持的账单页面');
    if(message.type==='status'){
      await chrome.storage.local.set({status:message.error});respond({ok:true});return;
    }
    if(message.type!=='balance')throw Error('未知同步消息');
    const observation=message.observation;
    if(observation.kind==='developer_context'){
      if(sender.frameId!==0||new URL(sender.tab.url).hostname!=='aistudio.google.com'||observation.source_url!==sender.tab.url)throw Error('结算账号上下文无效');
      await chrome.storage.session.set({['context-'+sender.tab.id]:{...observation,received:Date.now()}});respond({ok:true});return;
    }
    if(observation.kind==='developer_prepay'){
      const frame=new URL(sender.url);const top=new URL(sender.tab.url);
      if(frame.origin!=='https://payments.google.com'||!/^\/payments\/u\/\d+\/embedded_landing_page$/.test(frame.pathname)||top.hostname!=='aistudio.google.com')throw Error('预付余额必须来自 AI Studio 的内嵌账单');
      const context=(await chrome.storage.session.get('context-'+sender.tab.id))['context-'+sender.tab.id];
      if(!context||Date.now()-context.received>600000||context.source_url!==sender.tab.url||context.billing_account!==observation.billing_account)throw Error('等待父页面结算账号核对');
      if(top.searchParams.has('billing')&&top.searchParams.get('billing')!==observation.billing_account)throw Error('内嵌账单账号与页面账号不一致');
      observation.source_url=sender.tab.url;observation.eligible_cloud_credit=context.eligible_cloud_credit;
    }else if(observation.kind!=='cloud_credits'||sender.frameId!==0||observation.source_url!==sender.tab.url)throw Error('余额来源不一致');
    const settings=await chrome.storage.local.get(['origin','token']);
    if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(settings.origin||'')||!settings.token)throw Error('请先在扩展弹窗中配对本机应用');
    const r=await fetch(settings.origin+'/api/google-billing/sync',{method:'POST',
      headers:{'Content-Type':'application/json','x-google-billing-token':settings.token},body:JSON.stringify(observation)});
    const result=await r.json();if(!r.ok)throw Error(result.error||'本机同步失败');
    await chrome.storage.local.set({status:'已同步 '+new Date().toLocaleString(),last:result.observed_at});respond({ok:true});
  })().catch(async error=>{await chrome.storage.local.set({status:error.message});respond({ok:false,error:error.message});});
  return true;
});
chrome.alarms.create('billing-refresh',{periodInMinutes:5});
chrome.alarms.onAlarm.addListener(async alarm=>{
  if(alarm.name!=='billing-refresh'||!(await chrome.storage.local.get('autoRefresh')).autoRefresh)return;
  for(const tab of await chrome.tabs.query({url:['https://console.cloud.google.com/billing/*','https://aistudio.google.com/billing*']})){
    if(matches(tab.url))chrome.tabs.reload(tab.id);
  }
});
