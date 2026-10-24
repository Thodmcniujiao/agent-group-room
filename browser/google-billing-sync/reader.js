/* Pure, fail-closed parsing shared with the regression fixtures. */
(function(root){
  const compact=s=>String(s||'').replace(/\s+/g,' ').trim();
  function money(text){
    text=compact(text);let currency;
    if(/GBP|£/.test(text))currency='GBP';
    else if(/USD|US\$/.test(text))currency='USD';
    else if(/EUR|€/.test(text))currency='EUR';
    else if(/CNY|CN¥|人民币/.test(text))currency='CNY';
    else throw Error('金额缺少明确币种；单独的 $ 或 ¥ 无法确认币种');
    // Comma decimal locales must not silently become 100x larger balances.
    const numbers=text.match(/\d[\d,.]*/g)||[];
    if(numbers.length!==1||!/^\d+(?:,\d{3})*(?:\.\d+)?$/.test(numbers[0]))throw Error('金额格式无法确认');
    return {currency,remaining:numbers[0].replaceAll(',','')};
  }
  function date(text){
    text=compact(text);let match=text.match(/(\d{4})[年/.-](\d{1,2})[月/.-](\d{1,2})日?/);
    if(match){const value=new Date(Date.UTC(+match[1],+match[2]-1,+match[3]));
      if(value.getUTCMonth()!==+match[2]-1||value.getUTCDate()!==+match[3])return null;
      return value.toISOString();}
    const millis=Date.parse(text);return Number.isFinite(millis)?new Date(millis).toISOString():null;
  }
  function credits(headers,rows,url){
    const account=new URL(url).pathname.match(/^\/billing\/([A-Z0-9]{6}-[A-Z0-9]{6}-[A-Z0-9]{6})\/credits/);
    if(!account)throw Error('请打开具体结算账号的赠金页');
    const index=pattern=>headers.findIndex(h=>pattern.test(compact(h)));
    const columns={name:index(/赠金名称|Credit name/i),status:index(/^状态|^Status\b/i),
      remaining:index(/剩余金额|Remaining (value|amount)/i),id:index(/赠金\s*ID|Credit ID/i),
      scope:index(/使用范围|^Scope$/i),start:index(/开始日期|Start date/i),end:index(/结束日期|到期日期|End date|Expiration date/i)};
    if(Object.values(columns).some(i=>i<0))throw Error('赠金表缺少完整 ID、适用范围或到期日期列；请展开表格');
    const result=[];warnings=[];
    for(const cells of rows){
      try{
        const get=key=>compact(cells[columns[key]]);
        const id=get('id').replace(/\s/g,'');
        if(!id||/…|\.\.\./.test(id))throw Error('赠金 ID 被截断');
        const statusText=get('status');const status=/已过期|Expired/i.test(statusText)?'expired':
          /已用完|已耗尽|Used|Exhausted/i.test(statusText)?'used':/可用|Available|Active/i.test(statusText)&&!/不可用|Unavailable|Inactive/i.test(statusText)?'available':'unknown';
        const name=get('name'),scope=get('scope');const eligible=[];
        // Only the documented welcome credit or an explicit Vertex scope is inferred.
        if(/free\s*trial|免费试用/i.test(name+id)||/aiplatform\.googleapis\.com|Vertex AI|Gemini Enterprise Agent Platform/i.test(scope))eligible.push('vertex');
        if(/排除.*Vertex|except.*Vertex|不适用.*Vertex/i.test(scope))eligible.length=0;
        result.push({id,name,scope,status,...money(get('remaining')),starts_at:date(get('start')),
          expires_at:date(get('end')),eligible_routes:eligible});
      }catch(error){warnings.push(error.message);}
    }
    if(!result.length)throw Error(warnings[0]||'未读到完整赠金行');
    return {kind:'cloud_credits',billing_account:account[1],source_url:url,credits:result,complete:false,warnings};
  }
  function prepay(text,cards,url){
    const ids=[...new Set((text.match(/\b[A-Z0-9]{6}-[A-Z0-9]{6}-[A-Z0-9]{6}\b/g)||[]))];
    if(ids.length!==1)throw Error('AI Studio 页面需要显示唯一的结算账号 ID，无法按项目名称猜测');
    const matches=cards.filter(t=>/Available credits|Prepay balance|Credit balance|可用赠金|可用余额|预付余额|赠金余额/i.test(t));
    const values=[];
    for(const card of matches){try{values.push(money(card));}catch{}}
    const unique=[...new Map(values.map(v=>[v.currency+':'+v.remaining,v])).values()];
    if(unique.length!==1)throw Error('未找到唯一且币种明确的预付余额卡片');
    return {kind:'developer_prepay',billing_account:ids[0],account_verified:true,source_url:url,...unique[0]};
  }
  const api={money,date,credits,prepay};root.GoogleBillingReader=api;
  if(typeof module!=='undefined')module.exports=api;
})(globalThis);
