"""Shared model choices and Windows DPAPI credential storage."""
import json
import os
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit

STATE = Path.home()/'.local/share/tiered-dev'
PREFS = STATE/'preferences.json'
SECRET = STATE/'deepseek.dpapi'
PROFILE = Path.home()/'.codex/tiered.config.toml'
LOCK = threading.RLock()
DEFAULTS = {'models': {'chatgpt': 'gpt-6-astra', 'claude': 'claude-opus-5-5', 'deepseek': 'deepseek-flash'},
            'effort': {'chatgpt': 'high', 'claude': 'high'}, 'pause_before_execution': False,
            'cooperation_policy': '按任务需要动态安排团队成员协作。让不同意见得到充分表达，有证据地检查是否遵守我的要求；发现偏离就纠正，避免无意义的重复讨论。',
            'supervisor': {'provider': 'chatgpt', 'model': 'gpt-6-astra', 'effort': 'high', 'max_steps': 12}}


def default_members(value):
    return [{'id':p,'name':n,'backend':p,'model':value['models'][p],
             'effort':value['effort'].get(p,'high'),'base_url':'https://api.deepseek.com' if p=='deepseek' else '',
             'no_auth':False} for p,n in [('chatgpt','ChatGPT'),('claude','Claude'),('deepseek','DeepSeek')]]


def member_id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,47}',value) or value in ['supervisor','system','you','all']:
        raise ValueError('成员标识无效。')
    return value


def validate_members(members):
    if not isinstance(members,list) or not 1<=len(members)<=20:
        raise ValueError('团队需要包含 1 至 20 位成员。')
    result=[];seen=set()
    for m in members:
        if not isinstance(m,dict): raise ValueError('成员配置无效。')
        mid=member_id(m.get('id'));backend=m.get('backend')
        if mid in seen: raise ValueError('成员标识重复。')
        seen.add(mid)
        if backend not in ['chatgpt','claude','deepseek','openai-compatible']: raise ValueError('连接方式无效。')
        name=m.get('name','')
        if not isinstance(name,str) or not 1<=len(name.strip())<=40: raise ValueError('成员名称需要 1 至 40 字符。')
        model=model_id(m.get('model'));effort=m.get('effort','high')
        if effort not in ['low','medium','high','xhigh','max']: raise ValueError('推理强度无效。')
        endpoint=m.get('base_url','') if backend=='openai-compatible' else ('https://api.deepseek.com' if backend=='deepseek' else '')
        no_auth=bool(m.get('no_auth',False))
        if backend=='openai-compatible':
            try: parsed=urlsplit(endpoint);parsed.port
            except (ValueError,TypeError): raise ValueError('API 地址无效。') from None
            if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or any(c.isspace() for c in endpoint): raise ValueError('API 地址需包含主机，不允许内嵌凭据、查询或空白。')
            if parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname in ['127.0.0.1','localhost','::1']): raise ValueError('API 使用 HTTPS；本机服务可使用 HTTP。')
            if no_auth and parsed.hostname not in ['127.0.0.1','localhost','::1']: raise ValueError('免密钥模式仅用于本机服务。')
        result.append(dict(id=mid,name=name.strip(),backend=backend,model=model,effort=effort,base_url=endpoint.rstrip('/'),no_auth=no_auth))
    return result


def model_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/\[\]-]{0,149}', value):
        raise ValueError('模型 ID 格式无效。')
    return value


def load():
    result = json.loads(json.dumps(DEFAULTS))
    try:
        saved = json.loads(PREFS.read_text(encoding='utf-8'))
        for key in ['models', 'effort']:
            result[key].update(saved.get(key, {}))
        for key in ['cooperation_policy', 'pause_before_execution']:
            if key in saved:
                result[key] = saved[key]
        result['supervisor'].update(saved.get('supervisor',{}))
        result['members']=saved.get('members') or default_members(result)
        result['member_names']=saved.get('member_names',{})
    except (FileNotFoundError, ValueError):
        pass
    if result['cooperation_policy']=='按任务需要动态安排三方协作。让不同意见得到充分表达，有证据地检查是否遵守我的要求；发现偏离就纠正，避免无意义的重复讨论。':result['cooperation_policy']=DEFAULTS['cooperation_policy']
    result.setdefault('members',default_members(result))
    result.setdefault('member_names',{})
    result['member_names'].update({m['id']:m['name'] for m in result['members']})
    return result


def save(value):
    with LOCK:
        result = load()
        for provider in DEFAULTS['models']:
            if provider in value.get('models', {}):
                result['models'][provider] = model_id(value['models'][provider])
        for provider, effort in value.get('effort', {}).items():
            if provider not in DEFAULTS['effort'] or effort not in ['low', 'medium', 'high', 'xhigh', 'max']:
                raise ValueError('推理强度无效。')
            result['effort'][provider] = effort
        if 'cooperation_policy' in value:
            text=value['cooperation_policy']
            if not isinstance(text,str) or not 1<=len(text.strip())<=16000:
                raise ValueError('合作方式需要包含 1 至 16000 字符。')
            result['cooperation_policy']=text.strip()
        if 'supervisor' in value:
            config={**result['supervisor'],**value['supervisor']}
            member_id(config['provider'])
            model_id(config['model'])
            if config['effort'] not in ['low','medium','high','xhigh','max']:
                raise ValueError('监工推理强度无效。')
            if not isinstance(config['max_steps'],int) or not 2<=config['max_steps']<=32:
                raise ValueError('监工协作轮数应为 2 至 32。')
            result['supervisor']=config
        if 'members' in value:
            result['members']=validate_members(value['members'])
            if result['supervisor']['provider'] not in {m['id'] for m in result['members']}:
                raise ValueError('请先为监工选择仍在团队中的连接。')
            for m in result['members']:
                if m['id'] in result['models']:
                    result['models'][m['id']]=m['model']
                    if m['id'] in result['effort']:result['effort'][m['id']]=m['effort']
        elif 'models' in value or 'effort' in value:
            for m in result['members']:
                if m['id'] in value.get('models',{}):m['model']=result['models'][m['id']]
                if m['id'] in value.get('effort',{}):m['effort']=result['effort'][m['id']]
        if 'pause_before_execution' in value:
            result['pause_before_execution'] = bool(value['pause_before_execution'])
        result['member_names'].update({m['id']:m['name'] for m in result['members']})
        STATE.mkdir(parents=True, exist_ok=True)
        temporary = PREFS.with_suffix('.new')
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, PREFS)
        # This is the framework's dedicated CLI profile, not the user's default.
        import tomlkit
        profile=PROFILE
        profile.parent.mkdir(parents=True,exist_ok=True)
        data=tomlkit.parse(profile.read_text(encoding='utf-8')) if profile.exists() else tomlkit.document()
        data['model']=result['models']['chatgpt']
        data['model_reasoning_effort']=result['effort']['chatgpt']
        pending_profile=profile.with_name(profile.name+'.new')
        pending_profile.write_text(tomlkit.dumps(data),encoding='utf-8')
        os.replace(pending_profile,profile)
        return result


def save_key(value):
    if not isinstance(value, str) or not value.startswith('sk-') or len(value) > 512 or '\n' in value:
        raise ValueError('请输入有效的 DeepSeek API 密钥。')
    if os.name != 'nt':
        raise RuntimeError('此安装使用 Windows 加密存储。')
    import win32crypt
    encrypted = win32crypt.CryptProtectData(value.strip().encode(), 'Tiered Dev DeepSeek', None, None, None, 0)
    with LOCK:
        STATE.mkdir(parents=True, exist_ok=True)
        temporary = SECRET.with_suffix('.new')
        temporary.write_bytes(encrypted)
        os.replace(temporary, SECRET)


def get_key():
    if SECRET.exists() and os.name == 'nt':
        import win32crypt
        return win32crypt.CryptUnprotectData(SECRET.read_bytes(), None, None, None, 0)[1].decode()
    value = os.environ.get('DEEPSEEK_API_KEY', '')
    if not value and os.name == 'nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
                value = winreg.QueryValueEx(key, 'DEEPSEEK_API_KEY')[0]
        except FileNotFoundError:
            pass
    return value.strip()


def save_member_key(mid,value):
    member_id(mid)
    if not isinstance(value,str) or not 1<=len(value.strip())<=2048 or '\n' in value: raise ValueError('API 密钥无效。')
    import win32crypt
    encrypted=win32crypt.CryptProtectData(value.strip().encode(),'Tiered Dev member',None,None,None,0)
    with LOCK:
        folder=STATE/'credentials';folder.mkdir(parents=True,exist_ok=True)
        path=folder/(mid+'.dpapi');temp=path.with_suffix('.new');temp.write_bytes(encrypted);os.replace(temp,path)


def get_member_key(member):
    mid=member_id(member['id']);path=STATE/'credentials'/(mid+'.dpapi')
    if path.exists():
        import win32crypt
        return win32crypt.CryptUnprotectData(path.read_bytes(),None,None,None,0)[1].decode()
    return get_key() if member['backend']=='deepseek' else ''
