"""Local, opt-out collaboration traces. Payloads stay outside the project tree."""
import codecs
from datetime import datetime,timezone
import json
import math
from pathlib import Path
import re
import threading
import time
import uuid


def now():return datetime.now(timezone.utc).isoformat()
def number(value):return value if type(value) in (int,float) and math.isfinite(value) and value>=0 else None
def ratio(numerator,denominator):
    return numerator/denominator if number(numerator) is not None and number(denominator) not in (None,0) else None


def metrics(turn,adapter,raw,normalized):
    raw=raw or {};budget=getattr(turn,'budget',{})
    current=number((raw.get('last') or {}).get('inputTokens')) if adapter=='codex' else number(normalized.get('input'))
    window=number(raw.get('modelContextWindow',raw.get('context_window')))
    source='provider' if window else 'host_estimate'
    window=window or number(budget.get('window'))
    return {'tokens':normalized,'cache_hit_rate':ratio(normalized.get('cached_read'),normalized.get('input')),
        'current_input_tokens':current,'context_window':window,'context_window_source':source,
        'context_occupancy':ratio(current,window),'soft_budget':budget.get('soft'),
        'soft_budget_occupancy':ratio(current,budget.get('soft')),
        'host_input_estimate':getattr(turn,'last_request_estimate',None)}


def aggregate(requests):
    rows=list(requests.values());tokens={}
    for key in ('input','cached_read','cache_write','output','reasoning'):
        values=[r['tokens'].get(key) for r in rows if number(r['tokens'].get(key)) is not None]
        tokens[key]={'known_sum':sum(values) if values else None,'unknown_requests':len(rows)-len(values)}
    paired=[r for r in rows if number(r['tokens'].get('input')) is not None and number(r['tokens'].get('cached_read')) is not None]
    occupancy=[r['context_occupancy'] for r in rows if r['context_occupancy'] is not None]
    soft=[r['soft_budget_occupancy'] for r in rows if r['soft_budget_occupancy'] is not None]
    return {'requests':len(rows),'tokens':tokens,'cache_hit_rate':ratio(sum(r['tokens']['cached_read'] for r in paired),sum(r['tokens']['input'] for r in paired)),
        'cache_measured_requests':len(paired),'peak_context_occupancy':max(occupancy,default=None),
        'peak_soft_budget_occupancy':max(soft,default=None),
        'context_window_estimated':any(r['context_window_source']!='provider' for r in rows)}


class CollaborationLogs:
    def __init__(self,store,directory):
        self.store=store;self.directory=Path(directory);self.lock=threading.RLock();self.active={};self.last_error={}

    def _append(self,path,event):
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('ab') as stream:stream.write((json.dumps(event,ensure_ascii=False,allow_nan=False)+'\n').encode('utf-8'))

    def _save(self,run):
        path=self.directory/run['room']/run['id']/'manifest.json';path.parent.mkdir(parents=True,exist_ok=True)
        pending=path.with_suffix('.new');pending.write_text(json.dumps(run,ensure_ascii=False,allow_nan=False),encoding='utf-8');pending.replace(path)

    def _attempt(self,rid,operation):
        try:operation();return True
        except (OSError,ValueError,TypeError):
            if rid not in self.last_error:
                self.last_error[rid]='本地协作日志写入失败；协作继续，请检查日志目录和磁盘空间。'
                self.store.event(rid,{'type':'log_error','message':self.last_error[rid]})
            return False

    def begin(self,rid,mode,target,enabled=True):
        with self.lock:
            run={'id':uuid.uuid4().hex,'room':rid,'mode':mode,'target':target,'enabled':enabled,'started':now(),
                'status':'running','calls':{},'schema_version':1,'partial_recording':not enabled}
            self.active[rid]=run
            if enabled:self._attempt(rid,lambda:self._save(run))
            self.event(rid,'run_started',{'mode':mode,'target':target})
            return run['id']

    def configure(self,rid,enabled):
        with self.lock:
            run=self.active.get(rid)
            if run is None or run['enabled']==enabled:return
            if run['enabled']:self.event(rid,'logging_disabled',{})
            run['enabled']=enabled
            run['partial_recording']=True
            # Once recorded, keep the manifest truthful even when future payload logging is off.
            if enabled or (self.directory/rid/run['id']/'manifest.json').exists():self._attempt(rid,lambda:self._save(run))
            if enabled:self.event(rid,'logging_enabled',{'partial_run':True})

    def event(self,rid,kind,data,call=None):
        with self.lock:
            run=self.active.get(rid)
            if run is None or not run['enabled']:return False
            filename=(call+'.jsonl') if call else 'events.jsonl'
            event={'schema_version':1,'time':now(),'run_id':run['id'],'room_id':rid,'call_id':call,'event':kind,'data':data}
            return self._attempt(rid,lambda:self._append(self.directory/rid/run['id']/filename,event))

    def end(self,rid,status):
        with self.lock:
            run=self.active.get(rid)
            if run is None:return
            self.event(rid,'run_finished',{'status':status})
            run.update(status=status,finished=now())
            if (self.directory/rid/run['id']/'manifest.json').exists():self._attempt(rid,lambda:self._save(run))
            self.active.pop(rid,None)

    def call(self,turn,backend):
        with self.lock:
            if turn.rid not in self.active:self.begin(turn.rid,'direct',turn.identity,turn.settings.get('logging_enabled',True))
            return CallLog(self,turn,backend,self.active[turn.rid]['id'])

    def list(self,rid):
        values=[]
        for path in sorted((self.directory/rid).glob('*/manifest.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:100]:
            try:
                run=json.loads(path.read_text(encoding='utf-8'));calls=run.pop('calls',{})
                if run['status']=='running' and self.active.get(rid,{}).get('id')!=run['id']:run['status']='interrupted'
                values.append({**run,'call_count':len(calls)})
            except (OSError,ValueError,KeyError):continue
        return {'runs':values,'directory':str(self.directory/rid),'error':self.last_error.get(rid)}

    def path(self,rid,run_id,filename='manifest.json'):
        if not re.fullmatch(r'[a-f0-9]{32}',run_id):raise ValueError('日志编号无效。')
        path=self.directory/rid/run_id/filename
        if not path.is_file():raise ValueError('当前协作室没有此日志。')
        return path

    def detail(self,rid,run_id):
        value=json.loads(self.path(rid,run_id).read_text(encoding='utf-8'))
        if value['status']=='running' and self.active.get(rid,{}).get('id')!=run_id:
            value['status']='interrupted'
            for call in value['calls'].values():
                if call['status']=='running':call['status']='interrupted'
        return value

    def trace(self,rid,run_id,call_id):
        if call_id!='events' and not re.fullmatch(r'[a-f0-9]{32}',call_id):raise ValueError('发言日志编号无效。')
        return self.path(rid,run_id,call_id+'.jsonl')

    def page(self,path,offset=0,limit=32000):
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1024<=limit<=64000:raise ValueError('日志分页参数无效。')
        with path.open('rb') as stream:
            size=path.stat().st_size
            if offset>size:raise ValueError('日志读取位置越界。')
            stream.seek(offset);raw=stream.read(min(limit,size-offset))
        try:
            decoder=codecs.getincrementaldecoder('utf-8')();text=decoder.decode(raw,final=False)
        except UnicodeError:raise ValueError('日志读取位置不是 UTF-8 字符边界。') from None
        end=offset+len(raw)-len(decoder.getstate()[0])
        return {'text':text,'offset':offset,'next_offset':end if end<size else None,'total_bytes':size}


class CallLog:
    def __init__(self,owner,turn,backend,run_id):
        self.owner=owner;self.turn=turn;self.run_id=run_id;self.id=uuid.uuid4().hex;self.requests={}
        self.started=time.monotonic();self.http_requests=0
        self.meta={'id':self.id,'identity':turn.identity,'role':turn.role,'phase':turn.phase,'model':turn.model,
            'backend':backend,'session_key':turn.key,'branch':turn.state['branch'],'started':now(),'status':'running',
            'capture_scope':'cli_host_boundary' if backend in ['chatgpt','claude'] else 'api_http_body',
            'internal_cli_payload_available':False if backend in ['chatgpt','claude'] else None}
        self.emit('call_started',self.meta);self._save()

    def _save(self):
        with self.owner.lock:
            run=self.owner.active.get(self.turn.rid)
            if run and run['id']==self.run_id and (run['enabled'] or self.id in run['calls']):
                run['calls'][self.id]=self.meta.copy();self.owner._attempt(self.turn.rid,lambda:self.owner._save(run))

    def emit(self,kind,data):
        with self.owner.lock:
            run=self.owner.active.get(self.turn.rid)
            if run and run['id']==self.run_id:
                if run['enabled'] and self.id not in run['calls']:self._save()
                recorded=self.owner.event(self.turn.rid,kind,data,self.id)
                if recorded and kind=='http_request':self.http_requests+=1
                return recorded
        return False

    def usage(self,adapter,raw,normalized,status,request_ref):
        value=metrics(self.turn,adapter,raw,normalized)
        if self.emit('usage',{'adapter':adapter,'request_ref':request_ref,'status':status,'raw':raw,'measurement':value}):
            self.requests[adapter+':'+request_ref]=value;self.meta['usage']=aggregate(self.requests);self._save()

    def finish(self,status,answer='',message_id=None):
        self.emit('call_finished',{'status':status,'output':answer,'message_id':message_id})
        self.meta.update(status=status,finished=now(),message_id=message_id,elapsed_ms=round((time.monotonic()-self.started)*1000),
            http_requests_captured=self.http_requests);self._save()


def call_for(turn,backend):
    if not hasattr(turn,'audit'):
        owner=getattr(turn.store,'logs',None);turn.audit=owner.call(turn,backend) if owner else None
    return turn.audit


def emit(turn,kind,data):
    audit=getattr(turn,'audit',None)
    if audit:audit.emit(kind,data)
