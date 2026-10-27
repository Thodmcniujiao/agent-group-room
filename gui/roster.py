"""Configurable team identities over CLI and OpenAI-compatible connections."""
from engines import preferences,auth_status,cli_reply,deepseek_reply
import time

_auth_cache={}
_auth_checked=0.0


def invalidate_auth():
    global _auth_cache,_auth_checked
    _auth_cache={};_auth_checked=0.0


def member(settings,mid):
    for item in settings['members']:
        if item['id']==mid:return item
    raise ValueError('成员已不存在，请重新选择。')


def agent(settings,aid):
    for item in settings.get('agents',settings['members']):
        if item['id']==aid:return item
    raise ValueError('此 Agent 未保存在连接库中，请刷新 Agent 管理。')


def names(settings):
    return {m['id']:m['name'] for m in settings['members']}


def check_billing(settings,identities=None):
    """Check locally before asking any model to reason about a missing meter."""
    from google_billing import select,BillingUnavailable
    sources=[('supervisor',agent(settings,settings['supervisor']['provider'])),*((m['id'],m) for m in settings['members'])]
    for identity,source in sources:
        if identities is not None and identity not in identities:continue
        if not source.get('google_billing'):continue
        try:select(source,bool(preferences.get_member_key(source)))
        except BillingUnavailable as exc:raise BillingUnavailable(source['name']+'：'+str(exc)) from None


async def connections(settings,refresh=False):
    global _auth_cache,_auth_checked
    agents=settings.get('agents',settings['members'])
    if any(m['backend'] in ['chatgpt','claude'] for m in agents):
        if refresh or not _auth_cache or time.monotonic()-_auth_checked>=10:
            _auth_cache=await auth_status();_auth_checked=time.monotonic()
    base=_auth_cache;result={}
    for m in agents:
        if m['backend'] in ['chatgpt','claude']:result[m['id']]=base[m['backend']]
        else:
            if m.get('google_billing',{}).get('mode') in ['auto','vertex']:
                from google_billing import select,auth_available
                try:
                    route=select(m,bool(preferences.get_member_key(m)))
                    ready=route=='developer' or auth_available()
                    detail=('Vertex AI · ADC 已配置' if ready else '需要 Google Cloud ADC 登录') if route=='vertex' else 'Gemini API · 预付余额可用'
                except ValueError as exc:ready=False;detail=str(exc)
                result[m['id']]={'ready':ready,'detail':detail};continue
            ready=bool(preferences.get_member_key(m)) or m.get('no_auth',False)
            result[m['id']]={'ready':ready,'detail':('本机免密钥服务' if m.get('no_auth') else '密钥已保存') if ready else '需要 API 密钥'}
    for m in settings.get('members',[]):
        aid=m.get('agent_id',m['id'])
        if aid in result:result[m['id']]=result[aid]
    return result


async def reply(store,rid,mid,prompt,root,settings,phase,coding=False,boundary=None,branch='',allowed_paths=None,publish=True,contract_text=None):
    from optimization_settings import refresh_for_call
    settings=refresh_for_call(store,rid,settings)
    monitor=mid=='supervisor'
    config=settings['supervisor'] if monitor else member(settings,mid)
    source=agent(settings,config['provider']) if monitor else config
    if source.get('google_billing'):
        from google_billing import prepare
        source=await prepare(source)
        store.event(rid,{'type':'activity','provider':mid,'text':'Google 调用渠道：'+source['_google_route']})
    backend=source['backend'];model=config['model'];effort=config.get('effort')
    if hasattr(store,'session'):
        from context import ContextTurn
        from provider_sessions import reply as session_reply
        turn=ContextTurn(store,rid,mid,source,model,settings,phase,coding,boundary,branch,allowed_paths,contract_text)
        if callable(prompt):prompt=prompt(turn)
        return await session_reply(turn,prompt,root,effort,source,publish=publish)
    if backend in ['chatgpt','claude']:
        return await cli_reply(store,rid,backend,prompt,root,model,effort,phase,identity=mid,coding=coding)
    from model_catalog import effective_effort
    effort=await effective_effort(source,model,effort)
    return await deepseek_reply(store,rid,prompt,model,phase,identity=mid,
        api_key=preferences.get_member_key(source) or ('local-no-auth' if source.get('no_auth') else ''),
        base_url=source['base_url'],display_name=source['name'],effort=effort)
