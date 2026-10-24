let last='',timer;
function collect(){
  if(location.hostname==='payments.google.com'){
    const cards=[];
    for(const leaf of document.querySelectorAll('span,div')){
      if(leaf.children.length||!/^(赠金余额|Credit balance|Available credits|Prepay balance)$/.test(leaf.textContent.trim()))continue;
      let node=leaf;for(let i=0;i<7&&node;i++,node=node.parentElement)cards.push(node.innerText);
    }
    if(!/Cloud Prepay\s+[A-Z0-9-]+/.test(document.body.innerText))throw Error('内嵌页面不是 Cloud Prepay 账单');
    return GoogleBillingReader.prepay(document.body.innerText,cards,'https://aistudio.google.com/billing');
  }
  if(location.hostname==='console.cloud.google.com'){
    const tables=[...document.querySelectorAll('table,[role="table"],[role="treegrid"]')];
    for(const table of tables){
      const headers=[...table.querySelectorAll('th,[role="columnheader"]')].map(n=>n.innerText);
      if(!headers.some(h=>/剩余金额|Remaining (value|amount)/i.test(h)))continue;
      const rows=[...table.querySelectorAll('tr,[role="row"]')].map(row=>[...row.querySelectorAll('td,[role="cell"],[role="gridcell"]')].map(n=>n.innerText)).filter(row=>row.length);
      return GoogleBillingReader.credits(headers,rows,location.href);
    }
    throw Error('等待赠金表加载；请横向滚动并确认结束日期列可见');
  }
  const text=document.body.innerText;const ids=[...new Set(text.match(/\b[A-Z0-9]{6}-[A-Z0-9]{6}-[A-Z0-9]{6}\b/g)||[])];
  if(ids.length!==1)throw Error('等待当前结算账号 ID 显示');
  let eligibleCloudCredit;
  for(const button of document.querySelectorAll('button')){
    if(/符合条件的 GCP 赠金|eligible GCP credits/i.test(button.innerText)){
      try{eligibleCloudCredit=GoogleBillingReader.money(button.innerText);}catch{}
    }
  }
  return {kind:'developer_context',billing_account:ids[0],source_url:location.href,eligible_cloud_credit:eligibleCloudCredit};
}
async function sync(){
  try{
    const observation=collect();const signature=JSON.stringify(observation);
    if(signature===last)return;
    const result=await chrome.runtime.sendMessage({type:'balance',observation});
    if(!result?.ok)throw Error(result?.error||'未配对');
    last=signature;
  }catch(error){chrome.runtime.sendMessage({type:'status',error:error.message}).catch(()=>{});}
}
new MutationObserver(()=>{clearTimeout(timer);timer=setTimeout(sync,1500);}).observe(document.documentElement,{childList:true,subtree:true,characterData:true});
setInterval(sync,30000); // retry incomplete loads; unchanged successful DOM is never re-stamped
chrome.runtime.onMessage.addListener(message=>{if(message.type==='sync')sync();});
sync();
