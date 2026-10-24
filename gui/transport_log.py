"""HTTP body tee, without authentication headers or environment snapshots."""
import base64
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import uuid
import httpx


def request_data(request):
    body=request.content
    try:payload=json.loads(body)
    except (ValueError,UnicodeError):payload={'base64':base64.b64encode(body).decode('ascii')}
    return {'method':request.method,'endpoint':request.url.copy_with(username=None,password=None,query=None,fragment=None).__str__(),
        'body':payload,'body_base64':base64.b64encode(body).decode('ascii')}


class AsyncTee(httpx.AsyncByteStream):
    def __init__(self,stream,emit,ref):self.stream,self.emit,self.ref=stream,emit,ref
    async def __aiter__(self):
        async for chunk in self.stream:
            self.emit('http_response_bytes',{'request_ref':self.ref,'base64':base64.b64encode(chunk).decode('ascii')});yield chunk
    async def aclose(self):await self.stream.aclose()


class SyncTee(httpx.SyncByteStream):
    def __init__(self,stream,emit,ref):self.stream,self.emit,self.ref=stream,emit,ref
    def __iter__(self):
        for chunk in self.stream:
            self.emit('http_response_bytes',{'request_ref':self.ref,'base64':base64.b64encode(chunk).decode('ascii')});yield chunk
    def close(self):self.stream.close()


def async_hooks(emit):
    async def request(value):
        ref=uuid.uuid4().hex;value.extensions['audit_ref']=ref;emit('http_request',{'request_ref':ref,**request_data(value)})
    async def response(value):
        ref=value.request.extensions['audit_ref']
        emit('http_response_started',{'request_ref':ref,'status_code':value.status_code,'content_encoding':value.headers.get('content-encoding','identity')})
        if value.is_stream_consumed:emit('http_response_bytes',{'request_ref':ref,'base64':base64.b64encode(value.content).decode('ascii')})
        else:value.stream=AsyncTee(value.stream,emit,ref)
    return {'request':[request],'response':[response]}


def spool(kind,data):
    target=os.environ.get('TIERED_CALL_LOG_SPOOL')
    if not target:return
    try:
        control=os.environ.get('TIERED_CALL_LOG_CONTROL')
        if control and not json.loads(Path(control).read_text(encoding='utf-8')).get('enabled'):return
        with Path(target).open('ab') as stream:stream.write((json.dumps({'event':kind,'time':datetime.now(timezone.utc).isoformat(),'data':data},ensure_ascii=False)+'\n').encode('utf-8'))
    except (OSError,ValueError):pass


def install_sync_hooks():
    if not os.environ.get('TIERED_CALL_LOG_SPOOL'):return
    original=httpx.Client.__init__
    def init(self,*args,**kwargs):
        def request(value):
            ref=os.environ.get('TIERED_CALL_REQUEST_REF') or uuid.uuid4().hex;value.extensions['audit_ref']=ref;spool('http_request',{'request_ref':ref,**request_data(value)})
        def response(value):
            ref=value.request.extensions['audit_ref'];spool('http_response_started',{'request_ref':ref,'status_code':value.status_code,'content_encoding':value.headers.get('content-encoding','identity')})
            if value.is_stream_consumed:spool('http_response_bytes',{'request_ref':ref,'base64':base64.b64encode(value.content).decode('ascii')})
            else:value.stream=SyncTee(value.stream,spool,ref)
        hooks=kwargs.setdefault('event_hooks',{})
        hooks['request']=[*hooks.get('request',[]),request];hooks['response']=[*hooks.get('response',[]),response]
        original(self,*args,**kwargs)
    httpx.Client.__init__=init
