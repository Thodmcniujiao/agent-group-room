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
            ready=bool(preferences.get_member_key(m)) or m.get('no_auth',False)
            result[m['id']]={'ready':ready,'detail':('本机免密钥服务' if m.get('no_auth') else '密钥已保存') if ready else '需要 API 密钥'}
    return result


async def reply(store,rid,mid,prompt,root,settings,phase,coding=False,boundary=None,branch='',allowed_paths=None):
    monitor=mid=='supervisor'
    config=settings['supervisor'] if monitor else member(settings,mid)
    source=agent(settings,config['provider']) if monitor else config
    backend=source['backend'];model=config['model'];effort=config.get('effort')
    if hasattr(store,'session'):
        from context import ContextTurn
        from provider_sessions import reply as session_reply
        turn=ContextTurn(store,rid,mid,source,model,settings,phase,coding,boundary,branch,allowed_paths)
        return await session_reply(turn,prompt,root,effort,source)
    if backend in ['chatgpt','claude']:
        return await cli_reply(store,rid,backend,prompt,root,model,effort,phase,identity=mid,coding=coding)
    from model_catalog import effective_effort
    effort=await effective_effort(source,model,effort)
    return await deepseek_reply(store,rid,prompt,model,phase,identity=mid,
        api_key=preferences.get_member_key(source) or ('local-no-auth' if source.get('no_auth') else ''),
        base_url=source['base_url'],display_name=source['name'],effort=effort)
