"""Configurable team identities over CLI and OpenAI-compatible connections."""
from engines import preferences,auth_status,cli_reply,deepseek_reply


def member(settings,mid):
    for item in settings['members']:
        if item['id']==mid:return item
    raise ValueError('成员已不存在，请重新选择。')


def names(settings):
    return {m['id']:m['name'] for m in settings['members']}


async def connections(settings):
    base=await auth_status();result={}
    for m in settings['members']:
        if m['backend'] in ['chatgpt','claude']:result[m['id']]=base[m['backend']]
        else:
            ready=bool(preferences.get_member_key(m)) or m.get('no_auth',False)
            result[m['id']]={'ready':ready,'detail':('本机免密钥服务' if m.get('no_auth') else '密钥已保存') if ready else '需要 API 密钥'}
    return result


async def reply(store,rid,mid,prompt,root,settings,phase,coding=False):
    monitor=mid=='supervisor'
    config=settings['supervisor'] if monitor else member(settings,mid)
    source=member(settings,config['provider']) if monitor else config
    backend=source['backend'];model=config['model'];effort=config.get('effort','high')
    if backend in ['chatgpt','claude']:
        return await cli_reply(store,rid,backend,prompt,root,model,effort,phase,identity=mid,coding=coding)
    return await deepseek_reply(store,rid,prompt,model,phase,identity=mid,
        api_key=preferences.get_member_key(source) or ('local-no-auth' if source.get('no_auth') else ''),
        base_url=source['base_url'],display_name=source['name'])
