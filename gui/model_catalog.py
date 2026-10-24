"""One credential-scoped model directory shared by every model selector."""
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

import httpx
from openai import AsyncOpenAI
from engines import preferences, chatgpt_catalog, claude_catalog


def now():
    return datetime.now(timezone.utc).isoformat()


def provider_for(agent):
    if agent.get('google_billing'):return 'gemini'
    if agent['backend'] in ['chatgpt','claude','deepseek']:return agent['backend']
    host=urlsplit(agent.get('base_url','')).hostname
    return {'api.openai.com':'openai','api.anthropic.com':'claude-api',
        'generativelanguage.googleapis.com':'gemini','api.deepseek.com':'deepseek'}.get(host)


def connection_context(agent):
    backend=agent['backend'];key=''
    if backend in ['chatgpt','claude']:
        auth=Path.home()/('.codex/auth.json' if backend=='chatgpt' else '.claude/.credentials.json')
        identity=[backend,str(auth.stat().st_mtime_ns) if auth.exists() else '']
    else:
        key=preferences.get_member_key(agent)
        identity=['api',agent['base_url'],key,bool(agent.get('no_auth'))]
        if agent.get('google_billing'):
            from google_billing import adc_path
            path=adc_path()
            identity.extend([agent['google_billing'],agent.get('_google_route'),str(path),path.stat().st_mtime_ns if path.exists() else None])
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest(),key


def normalize(models):
    result={}
    for model in models:
        try:mid=preferences.model_id(model.get('id'))
        except (ValueError,AttributeError):continue
        if mid in result:continue
        label=model.get('label',mid)
        # A legacy cache had a lossy, hard-coded enum. Only explicit provenance
        # from a metadata adapter makes an effort list authoritative.
        source=model.get('effort_source','')
        levels=[]
        for value in model.get('efforts',[]) if source else []:
            try:value=preferences.effort_id(value)
            except ValueError:continue
            if value is not None and value not in levels:levels.append(value)
        default=model.get('default_effort')
        descriptions=model.get('effort_descriptions',{})
        result[mid]={'id':mid,'label':str(label)[:200],'efforts':levels,'effort_source':source,
            'default_effort':default if default in levels else None,
            'effort_descriptions':{e:descriptions[e] for e in levels if isinstance(descriptions.get(e),str)}}
    return list(result.values())[:1000]


def api_model_info(model,provider):
    info={'id':model['id'],'label':model.get('name') or model.get('display_name') or model['id']}
    if provider=='deepseek' and isinstance(model.get('effort'),dict):
        effort=model['effort']
        if isinstance(effort.get('supported_levels'),list):
            info.update(efforts=effort['supported_levels'],default_effort=effort.get('default_level'),
                effort_source='GET /models.effort.supported_levels')
    return info


async def discover(agent, key):
    """Only metadata requests/CLI initialization; no inference or user turn."""
    backend=agent['backend']
    if backend=='chatgpt':return await chatgpt_catalog()
    if backend=='claude':return await claude_catalog()
    cfg=agent.get('google_billing')
    if cfg and (cfg['mode']=='vertex' or cfg['mode']=='auto' and __import__('google_billing').auth_available()):
        from google_billing import access_token,auth_headers
        token=await access_token();result=[];cursor=None
        async with httpx.AsyncClient(timeout=18) as client:
            for _ in range(10):
                r=await client.get('https://aiplatform.googleapis.com/v1beta1/publishers/google/models',
                    headers=auth_headers(token,cfg['project']),params={'pageSize':100,**({'pageToken':cursor} if cursor else {})})
                if r.is_error:
                    error=RuntimeError('Vertex model directory failed');error.status_code=r.status_code;raise error
                data=r.json()
                for model in data.get('publisherModels',[]):
                    mid=model.get('name','').split('/models/')[-1]
                    if mid.startswith('gemini-'):result.append({'id':mid,'label':model.get('displayName') or mid})
                following=data.get('nextPageToken')
                if not following or following==cursor:break
                cursor=following
        return result
    if provider_for(agent)=='claude-api':
        result=[];cursor=None;base=agent['base_url'].rstrip('/')
        if not base.endswith('/v1'):base+='/v1'
        async with httpx.AsyncClient(timeout=18) as client:
            for _ in range(10):
                response=await client.get(base+'/models',headers={'x-api-key':key,'anthropic-version':'2023-06-01'},
                    params={'limit':100,**({'after_id':cursor} if cursor else {})})
                if response.is_error:
                    error=RuntimeError('Claude model directory failed');error.status_code=response.status_code;raise error
                page=response.json()
                result.extend({'id':m['id'],'label':m.get('display_name',m['id'])} for m in page.get('data',[]))
                following=page.get('last_id')
                if not page.get('has_more') or not following or following==cursor:break
                cursor=following
        return result
    result=[]
    async with AsyncOpenAI(api_key=key or 'local-no-auth',base_url=agent['base_url'],timeout=18,max_retries=0) as client:
        page=await client.models.list()
        # SDK auto-pagination preserves provider-specific cursors without guessing parameters.
        async for model in page:
            result.append(api_model_info(model.model_dump(),provider_for(agent)))
            if len(result)>=1000:break
    return result


class ModelCatalog:
    def __init__(self, path):
        self.path=Path(path);self.cache={};self.attempts={};self.tasks={};self.limit=asyncio.Semaphore(4)
        try:
            saved=json.loads(self.path.read_text(encoding='utf-8'))
            for fingerprint,entry in saved.items():
                self.cache[fingerprint]={'models':normalize(entry['models']),'updated':entry['updated']}
        except (OSError,ValueError,KeyError,TypeError):pass

    def context(self, agent):
        return connection_context(agent)

    def snapshot(self, settings):
        entries={};refreshing=False
        for agent in settings['agents']:
            if not provider_for(agent):
                entries[agent['id']]={'models':[],'state':'manual','source':'手动模型 ID','updated':None,'checked':None,'error':''}
                continue
            fingerprint,key=self.context(agent);cached=self.cache.get(fingerprint,{})
            attempt=self.attempts.get(fingerprint,{})
            loading=fingerprint in self.tasks and not self.tasks[fingerprint].done()
            missing=agent['backend'] not in ['chatgpt','claude'] and not key and not agent.get('no_auth')
            if agent.get('google_billing',{}).get('mode') in ['vertex','auto']:
                from google_billing import auth_available
                missing=not auth_available() and not key
            state='loading' if loading else attempt.get('state','idle')
            if missing:state='unconfigured'
            source={'chatgpt':'ChatGPT CLI 模型目录','claude':'Claude CLI 模型目录'}.get(agent['backend'],'API 模型目录')
            entries[agent['id']]={'models':cached.get('models',[]),'state':state,'source':source,
                'updated':cached.get('updated'),'checked':attempt.get('checked'),
                'action_url':attempt.get('action_url',''),
                'action_label':attempt.get('action_label',''),
                'error':('请先保存 API 密钥，再自动读取模型。' if missing else attempt.get('error',''))}
            refreshing=refreshing or loading
        return {'agents':entries,'refreshing':refreshing}

    def request_refresh(self, settings, ids=None):
        requested=[]
        for agent in settings['agents']:
            if ids is not None and agent['id'] not in ids:continue
            if not provider_for(agent):continue
            fingerprint,key=self.context(agent)
            if fingerprint in self.tasks and not self.tasks[fingerprint].done():
                requested.append(self.tasks[fingerprint]);continue
            if agent['backend'] not in ['chatgpt','claude'] and not key and not agent.get('no_auth'):
                from google_billing import auth_available
                if agent.get('google_billing',{}).get('mode') not in ['vertex','auto'] or not auth_available():continue
            self.attempts[fingerprint]={'state':'loading','checked':now(),'error':''}
            task=asyncio.create_task(self.fetch(fingerprint,agent.copy(),key))
            self.tasks[fingerprint]=task;requested.append(task)
        return list(dict.fromkeys(requested))

    async def fetch(self, fingerprint, agent, key):
        try:
            async with self.limit,asyncio.timeout(25):
                models=normalize(await discover(agent,key))
            self.cache[fingerprint]={'models':models,'updated':now()}
            self.attempts[fingerprint]={'state':'ready','checked':now(),'error':''}
            # Persist only IDs/labels/capabilities, indexed by opaque connection fingerprints.
            self.path.parent.mkdir(parents=True,exist_ok=True)
            pending=self.path.with_suffix('.new')
            pending.write_text(json.dumps(self.cache,ensure_ascii=False),encoding='utf-8')
            os.replace(pending,self.path)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            code=getattr(exc,'status_code',None)
            reason={401:'密钥无效或登录已失效',403:'目录访问被拒绝（403），请检查服务权限与密钥限制',404:'此服务未提供模型目录接口',
                    429:'服务请求过于频繁，请稍后刷新'}.get(code,'无法读取模型目录，请检查连接、登录及服务地址')
            action='';action_label=''
            if provider_for(agent)=='gemini' and code==403:
                body=getattr(exc,'body',{})
                if isinstance(body,dict):body=body.get('error',body)
                message=body.get('message','') if isinstance(body,dict) else ''
                if isinstance(message,str) and 'generativelanguage.googleapis.com' in message and ('has not been used' in message or 'disabled' in message):
                    reason='此密钥所属 Google Cloud 项目尚未启用 Gemini API，请先启用后刷新'
                    project=re.search(r'project\s+(\d+)',message)
                    action='https://console.cloud.google.com/apis/library/generativelanguage.googleapis.com'+('?project='+project[1] if project else '')
                    action_label='启用 Gemini API'
                details=body.get('details',[]) if isinstance(body,dict) else []
                blocked=next((d for d in details if isinstance(d,dict) and d.get('reason')=='API_KEY_SERVICE_BLOCKED'),None)
                if blocked:
                    reason='Gemini 密钥的 API 限制阻止模型列表查询（API_KEY_SERVICE_BLOCKED），请允许 Generative Language API'
                    project=re.fullmatch(r'projects/(\d+)',blocked.get('metadata',{}).get('consumer',''))
                    action='https://console.cloud.google.com/apis/credentials'+('?project='+project[1] if project else '')
                    action_label='检查 Gemini 密钥限制'
            self.attempts[fingerprint]={'state':'error','checked':now(),'error':reason+'；保留上次目录与当前选择。','action_url':action,'action_label':action_label}

    async def close(self):
        for task in self.tasks.values():
            if not task.done():task.cancel()
        await asyncio.gather(*self.tasks.values(),return_exceptions=True)


_catalog=None


def shared_catalog():
    global _catalog
    if _catalog is None:_catalog=ModelCatalog(preferences.STATE/'model-catalog.json')
    return _catalog


async def effective_effort(agent,model,value):
    """Send an exact returned enum, or omit it when no metadata was returned."""
    value=preferences.effort_id(value)
    if value is None or provider_for(agent) not in ['chatgpt','claude','deepseek']:return None
    catalog=shared_catalog();fingerprint,_=catalog.context(agent)
    entry=catalog.cache.get(fingerprint)
    if not entry or not any(m.get('effort_source') for m in entry['models']):
        await asyncio.gather(*catalog.request_refresh({'agents':[agent]}))
        entry=catalog.cache.get(fingerprint,{})
    metadata=next((m for m in entry.get('models',[]) if m['id']==model),None)
    if not metadata or not metadata.get('effort_source') or not metadata['efforts']:return None
    if value not in metadata['efforts']:
        raise ValueError(f'{model} 的官方目录未返回推理强度 {value}，请重新选择。')
    return value
