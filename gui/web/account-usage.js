const number=value=>Number.isInteger(value)?String(value):Number(value).toFixed(1);
const localTime=value=>new Date(value).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});

export class AccountAlerts {
  constructor(root){this.root=root;this.signature=''}
  render(alerts=[]){
    const signature=JSON.stringify(alerts);if(signature===this.signature)return;this.signature=signature;
    this.root.replaceChildren();this.root.hidden=!alerts.length;
    for(const alert of alerts){
      const row=document.createElement('div');row.className='account-cache-alert';
      const text=document.createElement('span');text.textContent=alert.agents.join('、')+'：'+alert.text+(alert.updated?' 上次成功：'+localTime(alert.updated):'');
      const link=document.createElement('a');link.href=alert.url.includes('console.cloud.google.com')?'/assets/google-billing.html':alert.url;link.textContent='检查余额与同步';
      row.append(text,link);this.root.append(row);
    }
  }
}

export class AccountUsageView {
  constructor(root,onRefresh){
    this.root=root;this.onRefresh=onRefresh;this.entry=null;
    root.classList.add('account-usage');
    root.innerHTML='<div class="usage-heading"><strong></strong><span class="usage-plan"></span><span class="usage-updated"></span></div><div class="usage-windows"></div><div class="usage-balances"></div><p class="usage-detail"></p><div class="usage-actions"><a target="_blank" rel="noreferrer"></a><button type="button" aria-label="刷新账户用量或余额">↻</button></div>';
    root.querySelector('button').onclick=async()=>{
      const button=root.querySelector('button');button.disabled=true;
      try{await this.onRefresh()}
      catch{root.querySelector('.usage-detail').textContent='刷新失败，请稍后重试。'}
      finally{button.disabled=this.entry?.status==='loading'||this.entry?.refreshing}
    };
  }

  render(entry){
    this.root.hidden=!entry;if(!entry)return;
    if(JSON.stringify(this.entry)===JSON.stringify(entry))return;
    this.entry=entry;this.root.dataset.status=entry.status;
    const root=this.root;root.querySelector('strong').textContent=entry.balances?.length?'API 账户余额':'账户用量';
    root.querySelector('.usage-plan').textContent=entry.plan?entry.plan.charAt(0).toUpperCase()+entry.plan.slice(1):'';
    root.querySelector('.usage-updated').textContent=(entry.status==='loading'?'更新中':entry.updated?localTime(entry.updated)+(entry.cached?' · 缓存':' 更新'):'');
    const windows=root.querySelector('.usage-windows');windows.replaceChildren();
    for(const value of entry.windows||[]){
      const row=document.createElement('div');row.className='usage-window';
      row.innerHTML='<div class="usage-window-label"><span></span><strong></strong></div><meter min="0" max="100"></meter><small></small>';
      row.querySelector('span').textContent=value.label;
      row.querySelector('strong').textContent='已用 '+number(value.used_percent)+'% · 剩余 '+number(value.remaining_percent)+'%';
      const meter=row.querySelector('meter');meter.value=value.used_percent;meter.setAttribute('aria-label',value.label+' 已用百分比');
      row.dataset.level=value.used_percent>=95?'critical':value.used_percent>=80?'warning':'normal';
      row.querySelector('small').textContent=value.resets_at?'重置 '+localTime(value.resets_at):'';
      windows.append(row);
    }
    const balances=root.querySelector('.usage-balances');balances.replaceChildren();
    for(const balance of entry.balances||[]){
      const row=document.createElement('div');row.className='usage-balance';
      const amount=document.createElement('strong');amount.textContent=(balance.route?balance.route+' · ':'')+balance.currency+' '+balance.total_balance;
      const detail=document.createElement('small');detail.textContent=[balance.granted_balance!==undefined?'赠送 '+balance.granted_balance:'',balance.topped_up_balance!==undefined?'充值 '+balance.topped_up_balance:''].filter(Boolean).join(' · ');
      row.append(amount,detail);balances.append(row);
    }
    root.querySelector('.usage-detail').textContent=entry.detail;
    const link=root.querySelector('a');link.href=entry.url;link.textContent=entry.status==='external'?'查看用量／账单 ↗':'官方账户页 ↗';
    const refresh=root.querySelector('button');refresh.hidden=['external','unconfigured'].includes(entry.status);refresh.disabled=entry.status==='loading'||entry.refreshing;
  }
}
