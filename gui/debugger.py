"""Bounded, independent agent diagnostics, without project or room context."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import time
import uuid

from engines import preferences, cli_reply, deepseek_reply


class DebugJob:
    def __init__(self, agent, prompt, secret):
        self.agent=agent.copy();self.secret=secret;self.clock=time.monotonic();self.version=0
        self.data=dict(id=uuid.uuid4().hex,agent_id=agent['id'],name=agent['name'],model=agent['model'],
            backend=agent['backend'],started=datetime.now(timezone.utc).isoformat(),status='running',
            prompt=prompt,output='',error='',activity='正在连接…',duration_ms=0)
        self.task=None

    def safe(self, text):
        text=str(text)
        return text.replace(self.secret,'[密钥已隐藏]') if self.secret else text

    def snapshot(self):
        data=self.data.copy()
        if data['status']=='running':data['duration_ms']=round((time.monotonic()-self.clock)*1000)
        return {k:self.safe(v) if isinstance(v,str) else v for k,v in data.items()}

    # The same output interface is used by production CLI/API adapters and diagnostics.
    def message(self, rid, provider, text='', **fields):
        self.data['output']=self.safe(text)[:100000];self.version+=1
        return self.data['id']

    def update(self, mid, text, status='streaming'):
        self.data['output']=self.safe(text)[:100000];self.version+=1

    def event(self, rid, value):
        if value.get('type')=='activity':
            self.data['activity']=self.safe(value.get('text',''))[:1000];self.version+=1

    def finish(self, status, error=''):
        self.data.update(status=status,error=self.safe(error)[:2000],duration_ms=round((time.monotonic()-self.clock)*1000),activity='')
        self.data={k:self.safe(v) if isinstance(v,str) else v for k,v in self.data.items()}
        self.version+=1
        self.secret=''


class AgentDebugger:
    def __init__(self):
        self.jobs={}

    def history(self, aid):
        return [job.snapshot() for job in self.jobs.values() if job.agent['id']==aid]

    def get(self, jid):
        if jid not in self.jobs:raise ValueError('调试记录已过期，请重新发送指令。')
        return self.jobs[jid]

    def start(self, agent, body):
        prompt=body.get('prompt','')
        if not isinstance(prompt,str) or not 1<=len(prompt.strip())<=8000:raise ValueError('请输入 1 至 8000 字符的测试指令。')
        if any(j.data['status']=='running' and j.agent['id']==agent['id'] for j in self.jobs.values()):raise ValueError('此 Agent 正在调试，请等待完成或先停止。')
        if sum(j.data['status']=='running' for j in self.jobs.values())>=4:raise ValueError('已有 4 个调试请求正在运行，请稍后再试。')
        source={**agent,'model':preferences.model_id(body.get('model',agent['model'])),'effort':body.get('effort',agent['effort'])}
        source['effort']=preferences.effort_id(source['effort'])
        key=preferences.get_member_key(source) if source['backend'] not in ['chatgpt','claude'] else ''
        if not key and source['backend'] not in ['chatgpt','claude'] and not source.get('no_auth'):raise ValueError(source['name']+' 尚未配置 API 密钥，请先保存密钥。')
        # Retain a short in-memory transcript per agent; never create group-chat rooms.
        previous=[jid for jid,j in self.jobs.items() if j.agent['id']==agent['id'] and j.data['status']!='running']
        for jid in previous[:-7]:del self.jobs[jid]
        while len(self.jobs)>=80:
            expired=next((jid for jid,j in self.jobs.items() if j.data['status']!='running'),None)
            if expired is None:break
            del self.jobs[expired]
        job=DebugJob(source,prompt.strip(),key);self.jobs[job.data['id']]=job
        job.task=asyncio.create_task(self.run(job,prompt.strip(),key))
        return job.snapshot()

    async def run(self, job, prompt, key):
        source=job.agent;aid=source['id']
        instruction='你正在独立的 Agent 调试控制台。直接回答以下测试指令，不执行工具或修改文件，不声称完成未做的操作，不输出私有推理。没有项目或群聊上下文。\n\n'+prompt
        try:
            async with asyncio.timeout(180):
                if source['backend'] in ['chatgpt','claude']:
                    with tempfile.TemporaryDirectory(prefix='agent-room-debug-') as folder:
                        await cli_reply(job,job.data['id'],source['backend'],instruction,Path(folder),
                            source['model'],source['effort'],'调试',identity=aid,coding=False)
                else:
                    from model_catalog import effective_effort
                    effort=await effective_effort(source,source['model'],source['effort'])
                    await deepseek_reply(job,job.data['id'],instruction,source['model'],'调试',identity=aid,
                        api_key=key or 'local-no-auth',base_url=source['base_url'],display_name=source['name'],effort=effort)
            job.finish('complete')
        except asyncio.CancelledError:
            job.finish('stopped')
        except TimeoutError:
            job.finish('error','调试超过 180 秒，已停止。请检查连接或缩短测试指令。')
        except Exception as exc:
            job.finish('error',str(exc) or '调用失败，请检查登录、密钥、模型权限及额度。')

    async def cancel(self, jid):
        job=self.get(jid)
        if job.task and not job.task.done():
            job.task.cancel()
            try:await job.task
            except asyncio.CancelledError:pass
            if job.data['status']=='running':job.finish('stopped')
        return job.snapshot()

    async def close(self):
        for jid in list(self.jobs):await self.cancel(jid)
