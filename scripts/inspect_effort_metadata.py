"""Archive sanitized official model metadata. No generation, balance or user text."""
import argparse
import asyncio
from datetime import datetime,timezone
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'gui'))
from engines import codex_metadata,claude_metadata,chatgpt_model_info,claude_model_info,preferences
from model_catalog import normalize,provider_for,api_model_info
from openai import AsyncOpenAI


async def main(path):
    report={'timestamp_utc':datetime.now(timezone.utc).isoformat(),'live_model_calls':False,
        'sources':['https://learn.chatgpt.com/docs/app-server','https://platform.claude.com/docs/en/api/models','https://api-docs.deepseek.com/api/list-models/'],
        'providers':{}}
    async with codex_metadata() as request:
        rows=[];cursor=None
        for _ in range(10):
            data=await request('model/list',{'limit':100,'includeHidden':False,**({'cursor':cursor} if cursor else {})})
            rows+=data.get('data',[]);cursor=data.get('nextCursor')
            if not cursor:break
        raw=[{k:m[k] for k in ['id','model','displayName','supportedReasoningEfforts','defaultReasoningEffort'] if k in m} for m in rows]
        report['providers']['chatgpt']={'method':'model/list','raw_models':raw,'models':normalize([chatgpt_model_info(m) for m in raw])}
    async with claude_metadata() as (_,data):
        raw=[{k:m[k] for k in ['value','resolvedModel','displayName','supportsEffort','supportedEffortLevels'] if k in m}
            for m in data.get('models',[]) if m.get('value')!='default']
        report['providers']['claude']={'method':'control initialize','raw_models':raw,'models':normalize([claude_model_info(m) for m in raw])}
    for agent in preferences.load()['agents']:
        provider=provider_for(agent)
        if provider not in ['gemini','deepseek'] or provider in report['providers']:continue
        key=preferences.get_member_key(agent)
        if not key:continue
        try:
            async with AsyncOpenAI(api_key=key,base_url=agent['base_url'],timeout=18,max_retries=0) as client:
                page=await client.models.list();raw=[];fields=set()
                async for model in page:
                    data=model.model_dump();fields.update(data)
                    # Preserve only public model capability fields, never owner/account values.
                    raw.append({k:v for k,v in data.items() if k in ['id','name','display_name','effort','supportedReasoningEfforts','supportedEffortLevels','defaultReasoningEffort','capabilities']})
            report['providers'][provider]={'method':'GET models (OpenAI compatibility)','response_field_names':sorted(fields),
                'raw_models':raw,'models':normalize([api_model_info(m,provider) for m in raw])}
        except Exception as exc:
            report['providers'][provider]={'method':'GET models','error_type':type(exc).__name__}
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({p:len(v.get('models',[])) for p,v in report['providers'].items()}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    asyncio.run(main(Path(parser.parse_args().output)))
