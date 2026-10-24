"""Account-scoped Google billing observations. Unknown is never spendable money.

The public Cloud Billing API exposes account/project state, not the console's
remaining promotional or AI Studio prepay balances. A paired browser companion
supplies visible balances; OAuth independently verifies the project binding.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
import os
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

import httpx

ACCOUNT = re.compile(r'[A-Z0-9]{6}-[A-Z0-9]{6}-[A-Z0-9]{6}')
PROJECT = re.compile(r'[a-z][a-z0-9-]{4,61}[a-z0-9]')
LOCK = threading.RLock()
MAX_AGE = 600


class _KeyLogFilter(logging.Filter):
    def filter(self,record):
        # API Keys lookup is a documented GET API with keyString in its query.
        # httpx otherwise prints the full URL even though we never log headers.
        message=record.getMessage()
        if 'keyString=' in message:
            record.msg=re.sub(r'(keyString=)[^&\s\"\']+',r'\1[REDACTED]',message);record.args=()
        return True


logging.getLogger('httpx').addFilter(_KeyLogFilter())


def utc():
    return datetime.now(timezone.utc)


def config(value):
    if value is None:return None
    if not isinstance(value,dict):raise ValueError('Google 结算配置无效。')
    mode=value.get('mode','developer')
    if mode not in ['developer','vertex','auto']:raise ValueError('Google 调用渠道无效。')
    project=value.get('project','');account=value.get('billing_account','').upper()
    location=value.get('location','global')
    if project and not PROJECT.fullmatch(project):raise ValueError('Google Cloud 项目 ID 无效。')
    if account and not ACCOUNT.fullmatch(account):raise ValueError('结算账号 ID 应为 XXXXXX-XXXXXX-XXXXXX。')
    if not re.fullmatch(r'global|[a-z]+-[a-z]+[0-9]',location):raise ValueError('Google Cloud 区域无效。')
    if mode!='developer' and not (project and account):raise ValueError('Vertex / 自动选择需要项目 ID 和结算账号 ID。')
    return dict(mode=mode,project=project,billing_account=account,location=location)


def state_path():
    from engines import preferences
    return preferences.STATE/'google-billing.json'


def load():
    try:return json.loads(state_path().read_text(encoding='utf-8'))
    except (OSError,ValueError):return {}


def save(value):
    path=state_path();path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.new');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(temp,path)


def pairing():
    with LOCK:
        data=load()
        if not data.get('pairing_token'):
            data['pairing_token']=secrets.token_urlsafe(32);save(data)
        return data['pairing_token']


def paired(token):
    saved=load().get('pairing_token')
    return bool(saved and secrets.compare_digest(saved,token or ''))


def amount(value):
    try:
        result=Decimal(str(value))
        if not result.is_finite() or result<0:raise ValueError()
        return str(result)
    except (ValueError,InvalidOperation):raise ValueError('余额必须是非负金额。') from None


def date(value):
    if not value:return None
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None:raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (ValueError,TypeError,AttributeError):raise ValueError('赠金日期需要带时区的 ISO 时间。') from None


def ingest(body):
    """A receipt time is owned by the host; browser timestamps cannot renew credit."""
    if not isinstance(body,dict):raise ValueError('余额同步格式无效。')
    account=body.get('billing_account','').upper();kind=body.get('kind')
    url=body.get('source_url','');parsed=urlsplit(url)
    if not ACCOUNT.fullmatch(account):raise ValueError('未能确认当前页面的结算账号。')
    if kind=='cloud_credits':
        if parsed.scheme!='https' or parsed.hostname!='console.cloud.google.com' or not parsed.path.startswith('/billing/'+account+'/credits'):
            raise ValueError('赠金必须来自对应结算账号的 Google Cloud 赠金页面。')
    elif kind=='developer_prepay':
        if parsed.scheme!='https' or parsed.hostname!='aistudio.google.com' or parsed.path.rstrip('/')!='/billing':
            raise ValueError('预付余额必须来自 AI Studio 账单页。')
        if body.get('account_verified') is not True:raise ValueError('AI Studio 页面尚未显示可核对的结算账号 ID。')
    else:raise ValueError('未知的 Google 余额来源。')
    rows=body.get('credits',[])
    if not isinstance(rows,list) or len(rows)>200:raise ValueError('赠金条数无效。')
    clean=[];ids=set()
    for row in rows:
        if not isinstance(row,dict):raise ValueError('赠金记录无效。')
        cid=str(row.get('id','')).strip()
        if not cid or cid in ids:raise ValueError('赠金 ID 缺失或重复；请展开完整 ID 后重试。')
        ids.add(cid)
        currency=row.get('currency','')
        if not re.fullmatch('[A-Z]{3}',currency):raise ValueError('需要明确的 ISO 货币代码。')
        status=row.get('status','unknown')
        if status not in ['available','expired','used','unknown']:raise ValueError('赠金状态无效。')
        routes=[r for r in row.get('eligible_routes',[]) if r in ['vertex','developer']]
        name=str(row.get('name',''))[:300];scope=str(row.get('scope',''))[:2000]
        # New welcome credits are never converted into Developer API funds.
        if 'freetrial' in re.sub(r'\s','',name+cid).lower() or '免费试用' in name:
            routes=[r for r in routes if r!='developer']
        clean.append(dict(id=cid,name=name,currency=currency,remaining=amount(row.get('remaining')),
            status=status,starts_at=date(row.get('starts_at')).isoformat() if row.get('starts_at') else None,
            expires_at=date(row.get('expires_at')).isoformat() if row.get('expires_at') else None,
            eligible_routes=routes,scope=scope))
    snapshot=dict(billing_account=account,kind=kind,source_url=url,credits=clean,
        complete=body.get('complete') is True,observed_at=utc().isoformat())
    if kind=='developer_prepay':
        currency=body.get('currency','')
        if not re.fullmatch('[A-Z]{3}',currency):raise ValueError('预付余额缺少明确币种。')
        snapshot.update(currency=currency,remaining=amount(body.get('remaining')))
        granted=body.get('eligible_cloud_credit')
        if isinstance(granted,dict) and granted.get('currency')==currency:
            snapshot['eligible_cloud_credit']=amount(granted.get('remaining'))
    with LOCK:
        data=load();data.setdefault('accounts',{}).setdefault(account,{})[kind]=snapshot;save(data)
    return snapshot


def balances(account,now=None):
    now=now or utc();observations=load().get('accounts',{}).get(account,{})
    totals={};details=[];fresh=False
    for kind,snapshot in observations.items():
        observed=date(snapshot.get('observed_at'))
        if not observed or not 0<=(now-observed).total_seconds()<=MAX_AGE:
            details.append('余额观察已过期，请打开账单页重新同步。');continue
        fresh=True
        if kind=='developer_prepay':
            paid=Decimal(snapshot['remaining'])
            totals[('developer',snapshot['currency'])]=paid+(Decimal(snapshot.get('eligible_cloud_credit','0')) if paid>0 else Decimal(0));continue
        for row in snapshot.get('credits',[]):
            starts=date(row.get('starts_at'));expires=date(row.get('expires_at'))
            if row['status']!='available' or not expires or expires<=now or starts and starts>now:continue
            for route in row['eligible_routes']:
                # Developer Cloud credit eligibility also needs an active Prepay balance.
                if route=='developer':continue
                key=(route,row['currency']);totals[key]=totals.get(key,Decimal(0))+Decimal(row['remaining'])
    prepay=observations.get('developer_prepay',{})
    return dict(status='ready' if fresh else 'unavailable',balances=[dict(route=r,currency=c,total_balance=str(v),
        **({'granted_balance':str(v)} if r=='vertex' else {'topped_up_balance':prepay.get('remaining'),
            'granted_balance':prepay.get('eligible_cloud_credit','0')})) for (r,c),v in totals.items()],
        observations=list(observations.values()),detail='；'.join(details) or '浏览器读取的账单快照，最多有效 10 分钟；Google 账单本身可能延迟。')


def select(agent,key_present,now=None):
    cfg=agent.get('google_billing')
    if not cfg:return None
    if cfg['mode']=='developer':return 'developer'
    if cfg['mode']=='vertex':return 'vertex'
    reading=balances(cfg['billing_account'],now)
    if reading['status']=='ready':
        for route in ['vertex','developer']:
            if route=='developer' and not key_present:continue
            if any(b['route']==route and Decimal(b['total_balance'])>0 for b in reading['balances']):return route
    raise ValueError('没有新鲜且已确认适用的 Google 余额。请同步赠金／预付余额，或手动选择调用渠道。')


def adc_path():
    explicit=os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
    return Path(explicit) if explicit else Path(os.environ.get('APPDATA',str(Path.home()/'.config')))/'gcloud/application_default_credentials.json'


def auth_available():return adc_path().is_file()


def auth_headers(token,project=None):
    if not project:
        try:project=json.loads(adc_path().read_text(encoding='utf-8')).get('quota_project_id')
        except (OSError,ValueError):pass
    return {'Authorization':'Bearer '+token,**({'x-goog-user-project':project} if project else {})}


async def access_token():
    """Refresh ADC without storing refresh tokens in room configs or model logs."""
    try:data=json.loads(adc_path().read_text(encoding='utf-8'))
    except (OSError,ValueError):raise ValueError('请先安装 Google Cloud CLI 并运行 gcloud auth application-default login。') from None
    if data.get('type')=='authorized_user':
        form=dict(grant_type='refresh_token',client_id=data['client_id'],client_secret=data['client_secret'],refresh_token=data['refresh_token'])
    elif data.get('type')=='service_account':
        import jwt
        now=int(time.time())
        assertion=jwt.encode(dict(iss=data['client_email'],scope='https://www.googleapis.com/auth/cloud-platform',
            aud='https://oauth2.googleapis.com/token',iat=now,exp=now+3600),data['private_key'],algorithm='RS256')
        form=dict(grant_type='urn:ietf:params:oauth:grant-type:jwt-bearer',assertion=assertion)
    else:raise ValueError('当前 ADC 类型暂不支持，请使用用户 ADC 或服务账号。')
    async with httpx.AsyncClient(timeout=20) as client:
        response=await client.post('https://oauth2.googleapis.com/token',data=form)
    if response.is_error:raise ValueError('Google Cloud 凭据刷新失败，请重新运行 gcloud auth application-default login。')
    token=response.json().get('access_token')
    if not isinstance(token,str) or not token:raise ValueError('Google 没有返回访问令牌。')
    return token


async def verify_project(cfg,token):
    async with httpx.AsyncClient(timeout=20) as client:
        headers=auth_headers(token,cfg['project'])
        r=await client.get('https://cloudbilling.googleapis.com/v1/projects/'+cfg['project']+'/billingInfo',headers=headers)
        if r.is_error:raise ValueError('无法核实项目结算绑定（HTTP '+str(r.status_code)+'），请检查 Cloud Billing API 和读取权限。')
        info=r.json()
        if not info.get('billingEnabled') or info.get('billingAccountName')!='billingAccounts/'+cfg['billing_account']:
            raise ValueError('项目未启用结算，或项目绑定的结算账号与所选余额不一致。')
        r=await client.get('https://cloudbilling.googleapis.com/v1/billingAccounts/'+cfg['billing_account'],headers=headers)
        if r.is_error or r.json().get('open') is not True:raise ValueError('结算账号未开放或当前登录无权读取，请检查 Billing Account Viewer 权限。')
    return info


async def prepare(agent):
    """Resolve once before session binding; never fail over after sending inference."""
    from engines import preferences
    cfg=agent.get('google_billing')
    if not cfg:return agent
    route=select(agent,bool(preferences.get_member_key(agent)))
    source={**agent,'_google_route':route}
    if route=='vertex' or cfg['mode']=='auto':
        token=await access_token()
        await verify_project(cfg,token)
        if route=='developer':
            async with httpx.AsyncClient(timeout=20) as client:
                headers=auth_headers(token,cfg['project'])
                r=await client.get('https://apikeys.googleapis.com/v2/keys:lookupKey',headers=headers,
                    params={'keyString':preferences.get_member_key(agent)})
                p=await client.get('https://cloudresourcemanager.googleapis.com/v1/projects/'+cfg['project'],headers=headers)
            if r.is_error or p.is_error or r.json().get('parent')!='projects/'+str(p.json().get('projectNumber'))+'/locations/global':
                raise ValueError('无法确认 Gemini API Key 属于所选结算项目，请检查 API Keys 查询权限和项目绑定。')
    if route=='vertex':
        location=cfg['location'];host='aiplatform.googleapis.com' if location=='global' else location+'-aiplatform.googleapis.com'
        source['base_url']=f'https://{host}/v1/projects/{cfg["project"]}/locations/{location}'
    else:source['base_url']='https://generativelanguage.googleapis.com/v1beta/openai'
    return source


async def credential(source):
    if source.get('_google_route')=='vertex':return await access_token()
    from engines import preferences
    return preferences.get_member_key(source) or ('local-no-auth' if source.get('no_auth') else '')
