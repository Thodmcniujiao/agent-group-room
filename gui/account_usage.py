"""Read-only account meters, scoped to the exact CLI account or API credential."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import math
import time

import httpx
from engines import chatgpt_limits, claude_limits
from model_catalog import connection_context, provider_for

LINKS={'chatgpt':'https://chatgpt.com/codex/settings/usage','claude':'https://claude.ai/settings/usage',
    'openai':'https://platform.openai.com/usage','claude-api':'https://platform.claude.com/usage',
    'gemini':'https://aistudio.google.com/billing','deepseek':'https://platform.deepseek.com/usage'}


def percent(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):return None
    return min(100,max(0,value))


def reset_time(value):
    if isinstance(value,bool):return None
    try:
        if isinstance(value,(int,float)):return datetime.fromtimestamp(value,timezone.utc).isoformat()
        if isinstance(value,str):return datetime.fromisoformat(value.replace('Z','+00:00')).isoformat()
    except (ValueError,OverflowError,OSError):pass
    return None


def window(label,used,resets):
    used=percent(used)
    return None if used is None else {'label':label,'used_percent':used,'remaining_percent':100-used,'resets_at':reset_time(resets)}


def codex_meter(data):
    windows=[];plan=''
    limits=data.get('rateLimitsByLimitId') or {'codex':data.get('rateLimits')}
    for lid,entry in limits.items():
        if not isinstance(entry,dict):continue
        plan=entry.get('planType') or plan
        for kind in ['primary','secondary']:
            value=entry.get(kind)
            if not isinstance(value,dict):continue
            mins=value.get('windowDurationMins')
            label='5 小时' if mins==300 else '7 天' if mins==10080 else (str(mins//60)+' 小时' if isinstance(mins,int) and mins%60==0 else str(mins)+' 分钟')
            if lid!='codex':label=(entry.get('limitName') or lid)+' · '+label
            item=window(label,value.get('usedPercent'),value.get('resetsAt'))
            if item:windows.append(item)
    return {'status':'ready' if windows else 'unavailable','plan':str(plan),'windows':windows,
        'balances':[],'detail':'同一本机 ChatGPT 账户共享额度。' if windows else '当前账户未返回额度数据，请在官方页面查看。'}


def claude_meter(data):
    rates=data.get('rate_limits') or {};windows=[]
    for key,label in [('five_hour','5 小时'),('seven_day','7 天'),('seven_day_opus','7 天 · Opus'),('seven_day_sonnet','7 天 · Sonnet')]:
        value=rates.get(key)
        if not isinstance(value,dict):continue
        item=window(label,value.get('utilization'),value.get('resets_at'))
        if item:windows.append(item)
    return {'status':'ready' if data.get('rate_limits_available') and windows else 'unavailable',
        'plan':str(data.get('subscription_type') or ''),'windows':windows if data.get('rate_limits_available') else [],
        'balances':[],'detail':'同一本机 Claude 账户共享额度。' if data.get('rate_limits_available') and windows else '当前登录方式未提供订阅额度数据，请在官方页面查看。'}


async def read_account(agent,key):
    provider=provider_for(agent)
    if provider=='chatgpt':return codex_meter(await chatgpt_limits())
    if provider=='claude':return claude_meter(await claude_limits())
    if provider!='deepseek':raise ValueError('此连接没有可用的账户读数接口。')
    async with httpx.AsyncClient(timeout=15) as client:
        response=await client.get('https://api.deepseek.com/user/balance',headers={'Authorization':'Bearer '+key,'Accept':'application/json'})
        response.raise_for_status();data=response.json()
    balances=[]
    for value in data.get('balance_infos',[]):
        if value.get('currency') not in ['CNY','USD']:continue
        clean={'currency':value['currency']}
        for field in ['total_balance','granted_balance','topped_up_balance']:
            try:
                amount=Decimal(str(value.get(field)))
                if not amount.is_finite():continue
                clean[field]=str(amount)
            except InvalidOperation:continue
        if 'total_balance' in clean:balances.append(clean)
    return {'status':'ready' if balances else 'unavailable','plan':'','windows':[],'balances':balances,
        'detail':'API 可用余额，包含赠送与充值余额。' if balances else '服务没有返回可用余额数据。'}


def external(provider):
    detail={'gemini':'当前 Gemini API Key 不提供账户用量／余额读数，请在 AI Studio 查看。',
        'openai':'当前 OpenAI API Key 不提供账户余额读数，请在官方用量与账单页面查看。',
        'claude-api':'当前 Claude API Key 不提供账户余额读数，请在官方控制台查看。'}[provider]
    return {'status':'external','plan':'','windows':[],'balances':[],'detail':detail}


class UsageMonitor:
    def __init__(self,ttl=60):
        self.ttl=ttl;self.cache={};self.checked={};self.tasks={};self.errors={};self.limit=asyncio.Semaphore(3)

    def snapshot(self,settings):
        entries={};refreshing=False
        for agent in settings['agents']:
            provider=provider_for(agent)
            if not provider:continue
            if provider in ['openai','claude-api','gemini']:
                entries[agent['id']]={**external(provider),'url':LINKS[provider],'updated':None};continue
            fingerprint,key=connection_context(agent)
            data=self.cache.get(fingerprint,{'plan':'','windows':[],'balances':[],'detail':'正在读取账户数据…','updated':None})
            loading=fingerprint in self.tasks and not self.tasks[fingerprint].done()
            state='loading' if loading else data.get('status','idle')
            detail=self.errors.get(fingerprint) or data['detail']
            if fingerprint in self.errors and not loading:state='error'
            if provider=='deepseek' and not key:state='unconfigured';detail='先配置 API 密钥，再查询余额。'
            entries[agent['id']]={**data,'status':state,'detail':detail,'url':LINKS[provider]}
            refreshing=refreshing or loading
        return {'agents':entries,'refreshing':refreshing}

    def request_refresh(self,settings,ids=None,force=False):
        requested=[]
        for agent in settings['agents']:
            if ids is not None and agent['id'] not in ids:continue
            if provider_for(agent) not in ['chatgpt','claude','deepseek']:continue
            fingerprint,key=connection_context(agent)
            if provider_for(agent)=='deepseek' and not key:continue
            if fingerprint in self.tasks and not self.tasks[fingerprint].done():requested.append(self.tasks[fingerprint]);continue
            if not force and time.monotonic()-self.checked.get(fingerprint,float('-inf'))<self.ttl:continue
            self.checked[fingerprint]=time.monotonic();self.errors.pop(fingerprint,None)
            task=asyncio.create_task(self.fetch(fingerprint,agent.copy(),key));self.tasks[fingerprint]=task;requested.append(task)
        return list(dict.fromkeys(requested))

    async def fetch(self,fingerprint,agent,key):
        try:
            async with self.limit,asyncio.timeout(25):data=await read_account(agent,key)
            self.cache[fingerprint]={**data,'updated':datetime.now(timezone.utc).isoformat()}
        except asyncio.CancelledError:raise
        except Exception as exc:
            code=getattr(getattr(exc,'response',None),'status_code',None)
            self.errors[fingerprint]='账户数据读取失败'+('（'+str(code)+'）' if code else '')+'，请检查登录或密钥权限。'+(' 当前显示上次结果。' if fingerprint in self.cache else '')

    async def close(self):
        for task in self.tasks.values():
            if not task.done():task.cancel()
        await asyncio.gather(*self.tasks.values(),return_exceptions=True)
