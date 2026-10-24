"""Isolated browser QA fixture: no credentials and no outbound model calls."""
import json
import os
from pathlib import Path
import sys
from datetime import datetime,timezone,timedelta

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'gui'))
STATE=ROOT/'work/google-billing-ui-state'
os.environ['TIERED_GUI_DATA_DIR']=str(STATE/'gui')
os.environ['TIERED_GUI_PORT']='49318'
from engines import preferences
preferences.STATE=STATE/'prefs';preferences.PREFS=preferences.STATE/'preferences.json';preferences.SECRET=preferences.STATE/'deepseek.dpapi';preferences.PROFILE=STATE/'profile.toml'
preferences.STATE.mkdir(parents=True,exist_ok=True)
agent=dict(id='gemini-fixture',name='Gemini 离线验证',backend='openai-compatible',model='gemini-fixture',effort=None,
    base_url='https://generativelanguage.googleapis.com/v1beta/openai',google_billing=dict(mode='auto',project='fixture-project',billing_account='ABCDEF-123456-ABC123',location='global'))
preferences.PREFS.write_text(json.dumps(dict(schema_version=2,agents=[agent],team=[],supervisor=dict(provider=agent['id'],model=agent['model'],effort=None,max_steps=12))),encoding='utf-8')
import google_billing as billing
now=datetime.now(timezone.utc)
billing.ingest(dict(kind='cloud_credits',billing_account='ABCDEF-123456-ABC123',source_url='https://console.cloud.google.com/billing/ABCDEF-123456-ABC123/credits/all',credits=[dict(id='FreeTrial:fixture',name='Free Trial',currency='GBP',remaining='226.17',status='available',expires_at=(now+timedelta(days=60)).isoformat(),eligible_routes=['vertex'],scope='fixture')]))
import server
server.MODELS.request_refresh=lambda *a,**kw:[]
async def verified(*args):return {'billingEnabled':True,'billingAccountName':'billingAccounts/ABCDEF-123456-ABC123'}
async def token():return 'offline-fixture-token'
billing.verify_project=verified;billing.access_token=token
server.DEBUG.start=lambda *args:(_ for _ in ()).throw(ValueError('离线界面验证不调用真实模型。'))
if __name__=='__main__':
    import uvicorn
    uvicorn.run(server.app,host='127.0.0.1',port=49318,log_level='warning',access_log=False)
