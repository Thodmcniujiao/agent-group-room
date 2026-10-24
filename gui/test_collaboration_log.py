"""Actual local request capture, cancellation, isolation and opt-out verification."""
import asyncio
import base64
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch,AsyncMock

import httpx
from test_token_optimization import Fixture
from collaboration_log import call_for,metrics
from display_settings import DisplaySettings
from room_settings import RoomSettings
from transport_log import spool
from usage import record
import provider_sessions


def events(call):
    path=call.owner.trace(call.turn.rid,call.run_id,call.id)
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


class LogTests(Fixture):
    def test_default_on_strict_boolean_and_room_isolation_without_rotation(self):
        rooms=RoomSettings(self.store);rooms.seed(self.rid,self.settings,legacy=True)
        other=self.store.create('other',str(self.root))['id'];rooms.seed(other,self.settings,legacy=True)
        with patch('room_settings.preferences.load',return_value=self.settings):
            initial=rooms.load(self.rid);self.assertTrue(initial['logging_enabled'])
            changed=rooms.update(self.rid,{'logging_enabled':False})
            self.assertFalse(changed['logging_enabled']);self.assertTrue(rooms.load(other)['logging_enabled'])
            self.assertEqual(initial['context_epoch'],changed['context_epoch'])
            with self.assertRaises(ValueError):rooms.update(self.rid,{'logging_enabled':1})

    def test_off_creates_no_payload_files_and_live_disable_blocks_spool(self):
        self.store.logs.begin(self.rid,'discuss','all',False);call=call_for(self.turn(),'claude')
        call.emit('host_input',{'secret_prompt':'OFF-MUST-NOT-EXIST'})
        self.assertFalse(self.store.logs.directory.exists())
        self.store.logs.configure(self.rid,True);call.emit('host_input',{'prompt':'ON'})
        control=self.store.logs.directory/self.rid/call.run_id/'manifest.json';target=self.base/'spool.jsonl'
        with patch.dict(os.environ,{'TIERED_CALL_LOG_SPOOL':str(target),'TIERED_CALL_LOG_CONTROL':str(control)}):
            spool('request',{'text':'on'});before=target.read_bytes()
            self.store.logs.configure(self.rid,False);spool('request',{'text':'off'})
            self.assertEqual(target.read_bytes(),before)
        self.assertFalse(call.emit('host_input',{'prompt':'OFF-AGAIN'}))
        call.finish('complete','OFF-OUTPUT');self.store.logs.end(self.rid,'complete')
        contents=''.join(p.read_text(encoding='utf-8') for p in self.store.logs.directory.rglob('*.json*'))
        self.assertNotIn('OFF-',contents)
        detail=self.store.logs.detail(self.rid,call.run_id)
        self.assertEqual(detail['calls'][call.id]['status'],'complete')

    def test_runs_and_messages_are_separate_and_unicode_pages_restore_exactly(self):
        first=self.store.logs.begin(self.rid,'workflow','all')
        self.store.message(self.rid,'you','用户真实输入🙂'*1500)
        call=call_for(self.turn(),'claude');call.emit('cli_stdin',{'text':'真实提示🙂'*1000});call.finish('complete','实际输出')
        self.store.logs.end(self.rid,'complete')
        second=self.store.logs.begin(self.rid,'discuss','claude');self.assertNotEqual(first,second)
        path=self.store.logs.trace(self.rid,first,call.id);chunks=[];offset=0
        while True:
            page=self.store.logs.page(path,offset,1024);chunks.append(page['text'])
            if page['next_offset'] is None:break
            offset=page['next_offset']
        self.assertEqual(''.join(chunks),path.read_text(encoding='utf-8'))
        other=self.store.create('other',str(self.root))['id']
        with self.assertRaises(ValueError):self.store.logs.trace(other,first,call.id)
        with self.assertRaises(ValueError):self.store.logs.trace(self.rid,'../other','events')
        with self.assertRaises(ValueError):self.store.logs.trace(self.rid,first,'../../rooms.sqlite3')
        partial=self.base/'partial.jsonl';encoded='🙂'.encode('utf-8');partial.write_bytes(b'prefix'+encoded[:2])
        page=self.store.logs.page(partial,0,1024);self.assertEqual(page['text'],'prefix')
        with partial.open('ab') as stream:stream.write(encoded[2:])
        self.assertEqual(self.store.logs.page(partial,page['next_offset'],1024)['text'],'🙂')

    def test_usage_deduplicates_requests_and_never_uses_cumulative_context(self):
        turn=self.turn('supervisor');call=call_for(turn,'chatgpt')
        raw={'total':{'inputTokens':10000,'cachedInputTokens':6000,'outputTokens':80},
            'last':{'inputTokens':2000},'modelContextWindow':100000}
        record(turn,'codex',raw,request_ref='one')
        value=call.meta['usage'];self.assertEqual(value['tokens']['input']['known_sum'],10000)
        self.assertEqual(value['cache_hit_rate'],.6);self.assertEqual(value['peak_context_occupancy'],.02)
        measurement=events(call)[-1]['data']['measurement'];self.assertEqual(measurement['context_window_source'],'provider')
        normalized={'input':100,'cached_read':60,'cache_write':None,'output':5,'reasoning':None}
        call.usage('openai_compatible',{},normalized,'complete','two');call.usage('openai_compatible',{},normalized,'complete','two')
        self.assertEqual(call.meta['usage']['requests'],2);self.assertEqual(call.meta['usage']['tokens']['input']['known_sum'],10100)

    def test_unknown_fields_and_disk_failure_are_visible(self):
        turn=self.turn();call=call_for(turn,'claude');record(turn,'claude',{},request_ref='unknown')
        self.assertIsNone(call.meta['usage']['tokens']['input']['known_sum']);self.assertIsNone(call.meta['usage']['cache_hit_rate'])
        self.assertIsNone(metrics(turn,'codex',{'total':{'inputTokens':9999}},dict.fromkeys(['input','cached_read']))['context_occupancy'])
        with patch.object(self.store.logs,'_append',side_effect=OSError('fixture disk full')):
            self.assertFalse(call.emit('output',{'text':'must be reported'}))
        self.assertIn('写入失败',self.store.logs.last_error[self.rid])

    def test_avatar_display_persists_and_rejects_invalid_values(self):
        path=self.base/'display.json';display=DisplaySettings(path);self.assertEqual(display.snapshot()['chat_avatar_size'],32)
        display.save({'chat_avatar_size':80});self.assertEqual(DisplaySettings(path).snapshot()['chat_avatar_size'],80)
        for value in [True,23,97,'64',float('nan')]:
            with self.assertRaises(ValueError):display.save({'chat_avatar_size':value})


class RuntimeLogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):self.fixture=Fixture();self.fixture.setUp();self.fixture.settings['executor']=None
    def tearDown(self):self.fixture.tearDown()

    async def test_http_tool_continuation_captures_actual_bodies_and_no_auth_header(self):
        f=self.fixture;turn=f.turn('deepseek');requests=[];responses=[];mid=f.store.messages(f.rid)[0]['id']
        def respond(request):
            requests.append(json.loads(request.content))
            delta={'tool_calls':[{'index':0,'id':'read-1','type':'function','function':{'name':'group_get_message','arguments':json.dumps({'message_id':mid})}}]} if len(requests)==1 else {'content':'实际答复'}
            chunk={'id':'wire','object':'chat.completion.chunk','created':0,'model':turn.model,'choices':[{'index':0,'delta':delta,'finish_reason':'tool_calls' if len(requests)==1 else 'stop'}],
                'usage':{'prompt_tokens':1000,'completion_tokens':20,'prompt_tokens_details':{'cached_tokens':800}}}
            body=('data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n').encode();responses.append(body)
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=body)
        real=provider_sessions.AsyncOpenAI
        def client(**kwargs):return real(**kwargs,http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        with patch.object(provider_sessions,'AsyncOpenAI',side_effect=client):
            answer,_=await provider_sessions.api_conversation(turn,turn.compose('真实任务'),'AUTH-MUST-NOT-LOG','http://127.0.0.1:1/v1',lambda _:None)
        self.assertEqual(answer,'实际答复');trace=events(turn.audit)
        captured=[e['data'] for e in trace if e['event']=='http_request']
        self.assertEqual([e['body'] for e in captured],requests);self.assertEqual(len(captured),2)
        for request,response in zip(captured,responses):
            raw=b''.join(base64.b64decode(e['data']['base64']) for e in trace if e['event']=='http_response_bytes' and e['data']['request_ref']==request['request_ref'])
            self.assertEqual(raw,response)
        self.assertIn('编写并验证程序',requests[-1]['messages'][-1]['content'])
        self.assertNotIn('AUTH-MUST-NOT-LOG',json.dumps(trace,ensure_ascii=False))
        self.assertEqual(turn.audit.meta['usage']['requests'],2);self.assertEqual(turn.audit.meta['usage']['cache_hit_rate'],.8)

    async def test_failed_http_response_is_retained_and_usage_is_unknown(self):
        f=self.fixture;turn=f.turn('deepseek');real=provider_sessions.AsyncOpenAI
        def client(**kwargs):return real(**kwargs,http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(403,json={'error':{'message':'fixture rejection'}}))))
        with patch.object(provider_sessions,'AsyncOpenAI',side_effect=client):
            with self.assertRaises(Exception):await provider_sessions.api_conversation(turn,turn.compose('request'),'fixture','http://127.0.0.1:1/v1',lambda _:None)
        trace=events(turn.audit);self.assertTrue(any(e['event']=='http_response_started' and e['data']['status_code']==403 for e in trace))
        self.assertIsNone(turn.audit.meta['usage']['tokens']['input']['known_sum'])

    async def test_cli_records_actual_host_stdin_and_returned_events(self):
        f=self.fixture;turn=f.turn('supervisor');call=call_for(turn,'chatgpt');output=[];reader=asyncio.StreamReader()
        wire=[{'method':'item/agentMessage/delta','params':{'delta':'CLI实际回复'}},{'method':'thread/tokenUsage/updated','params':{'threadId':'fixture','tokenUsage':{'total':{'inputTokens':100,'cachedInputTokens':60,'outputTokens':10},'last':{'inputTokens':100},'modelContextWindow':1000}}},{'method':'turn/completed','params':{'turn':{'status':'completed'}}}]
        for event in wire:reader.feed_data((json.dumps(event)+'\n').encode())
        reader.feed_eof();stdin=[];proc=SimpleNamespace(stdout=reader,stdin=SimpleNamespace(write=stdin.append,drain=lambda:asyncio.sleep(0)))
        rpc=provider_sessions.CodexRPC(proc,turn,output.append);rpc.request_active=True
        sent={'method':'turn/start','params':{'input':[{'type':'text','text':'宿主真实提示'}]}}
        await rpc.send(sent);await rpc.consume()
        trace=events(call);self.assertEqual(json.loads(stdin[0]),sent)
        self.assertEqual(next(e['data'] for e in trace if e['event']=='cli_stdin'),sent)
        self.assertEqual([json.loads(e['data']['line']) for e in trace if e['event']=='cli_stdout'],wire)
        self.assertEqual(call.meta['capture_scope'],'cli_host_boundary');self.assertFalse(call.meta['internal_cli_payload_available'])
        self.assertEqual(call.meta['usage']['peak_context_occupancy'],.1)

    async def test_claude_request_events_preserve_cache_totals_and_context_window(self):
        import engines
        f=self.fixture;turn=f.turn('claude');wire=[];sent=[]
        for ref,count in [('one',100),('two',200)]:
            wire.extend([{'type':'stream_event','event':{'type':'message_start','message':{'id':ref,'usage':{'input_tokens':count,'cache_read_input_tokens':400,'cache_creation_input_tokens':50,'output_tokens':0}}}},
                {'type':'stream_event','event':{'type':'message_delta','usage':{'output_tokens':20}}},
                {'type':'stream_event','event':{'type':'message_stop'}}])
        wire.append({'type':'result','result':'CLI最终回复','usage':{},'modelUsage':{turn.model:{'contextWindow':1000}}})
        proc=SimpleNamespace(stdin=SimpleNamespace(write=sent.append,drain=AsyncMock(),close=lambda:None),
            stdout=SimpleNamespace(readline=AsyncMock(side_effect=[(json.dumps(e)+'\n').encode() for e in wire]+[b''])),wait=AsyncMock(),returncode=0)
        with patch.object(engines,'find_cli',return_value='fixture-cli'),patch.object(asyncio,'create_subprocess_exec',new=AsyncMock(return_value=proc)),patch.object(engines,'kill_tree',new=AsyncMock()):
            answer,_=await provider_sessions.claude_conversation(turn,'CLI真实输入',f.root,None,lambda _:None)
        self.assertEqual(answer,'CLI最终回复');self.assertEqual(sent,[b'CLI\xe7\x9c\x9f\xe5\xae\x9e\xe8\xbe\x93\xe5\x85\xa5'])
        usage=turn.audit.meta['usage'];self.assertEqual(usage['requests'],2)
        self.assertEqual(usage['tokens']['input']['known_sum'],1200);self.assertEqual(usage['tokens']['cached_read']['known_sum'],800)
        self.assertAlmostEqual(usage['peak_context_occupancy'],.65);self.assertFalse(usage['context_window_estimated'])

    async def test_workflow_starts_independent_logs_and_off_run_has_no_file(self):
        from workflows import Workflows
        f=self.fixture;workflow=Workflows(f.store)
        async def run(rid,*args):f.store.status(rid,'complete')
        with patch.object(workflow,'run',side_effect=run):
            await workflow.start(f.rid,'本次协作真实请求','discuss','all',f.settings)
            values=f.store.logs.list(f.rid)['runs'];self.assertEqual(len(values),1);self.assertEqual(values[0]['status'],'complete')
            await workflow.start(f.rid,'关闭后不得进入新日志','discuss','all',{**f.settings,'logging_enabled':False})
        self.assertEqual(len(f.store.logs.list(f.rid)['runs']),1)
        body=''.join(p.read_text(encoding='utf-8') for p in f.store.logs.directory.rglob('*.jsonl'))
        self.assertIn('本次协作真实请求',body);self.assertNotIn('关闭后不得进入新日志',body)

    async def test_cancelled_stream_keeps_partial_output_and_marks_interrupted(self):
        f=self.fixture;turn=f.turn('deepseek');received=asyncio.Event();real=provider_sessions.AsyncOpenAI
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                chunk={'id':'partial','object':'chat.completion.chunk','created':0,'model':turn.model,'choices':[{'index':0,'delta':{'content':'未完成片段'},'finish_reason':None}]}
                yield ('data: '+json.dumps(chunk)+'\n\n').encode();await asyncio.Event().wait()
            async def aclose(self):pass
        def respond(request):return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Stream())
        def client(**kwargs):return real(**kwargs,http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        original=turn.store.update
        def update(mid,text,*args,**kwargs):
            if '未完成片段' in text:received.set()
            return original(mid,text,*args,**kwargs)
        with patch.object(provider_sessions,'AsyncOpenAI',side_effect=client),patch('model_catalog.effective_effort',new=AsyncMock(return_value=None)),patch('engines.preferences.get_member_key',return_value='fixture'),patch.object(turn.store,'update',side_effect=update):
            task=asyncio.create_task(provider_sessions.reply(turn,'request',f.root,None,turn.source))
            await asyncio.wait_for(received.wait(),3);task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(turn.audit.meta['status'],'interrupted')
        self.assertEqual(next(e['data']['output'] for e in events(turn.audit) if e['event']=='call_finished'),'未完成片段')
        self.assertTrue(any(e['event']=='http_response_bytes' for e in events(turn.audit)))

    async def test_log_download_scope_and_display_http_validation(self):
        import server
        f=self.fixture;rooms=RoomSettings(f.store);rooms.seed(f.rid,f.settings,legacy=True);call=call_for(f.turn(),'claude');call.emit('input',{'text':'真实原文🙂'})
        with patch.object(server,'STORE',f.store),patch.object(server,'ROOMS',rooms),patch.object(server,'DISPLAY',DisplaySettings(f.base/'display.json')):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url=server.ORIGIN) as client:
                response=await client.get('/api/rooms/'+f.rid+'/logs/'+call.run_id+'/'+call.id+'?download=1')
                self.assertEqual(response.status_code,200);self.assertIn('attachment',response.headers['content-disposition'])
                self.assertIn('真实原文',response.text);self.assertEqual(response.headers['cache-control'],'no-store')
                other=f.store.create('other',str(f.root))['id'];response=await client.get('/api/rooms/'+other+'/logs/'+call.run_id+'/'+call.id)
                self.assertEqual(response.status_code,400)
                self.assertEqual((await client.post('/api/display',json={'chat_avatar_size':80})).status_code,403)
                headers={'X-Tiered-Token':server.TOKEN}
                self.assertEqual((await client.post('/api/display',json={'chat_avatar_size':80},headers=headers)).status_code,200)
                self.assertEqual((await client.get('/api/display')).json()['display']['chat_avatar_size'],80)
                self.assertEqual((await client.post('/api/display',json={'chat_avatar_size':True},headers=headers)).status_code,400)

    async def test_real_executor_initialization_without_generation_has_no_spool_error(self):
        from engines import deepseek_session
        f=self.fixture;f.settings['executor']='deepseek';turn=f.turn('deepseek')
        source={**turn.source,'base_url':'http://127.0.0.1:1/v1','no_auth':True}
        with patch('engines.preferences.get_member_key',return_value='fixture'),patch('model_catalog.effective_effort',new=AsyncMock(return_value=None)):
            async with deepseek_session(f.root,turn.model,f.store,f.rid,member=source,context_turn=turn) as session:
                result=await session.list_tools();self.assertTrue(result.tools)
        self.assertNotIn(f.rid,f.store.logs.last_error)
        self.assertEqual(turn.audit.meta['http_requests_captured'],0)


class ExecutorWireTests(unittest.TestCase):
    def test_real_isolated_provider_child_captures_http_and_correlates_usage(self):
        import tempfile
        received=[]
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                body=json.dumps({'id':'fixture-response','choices':[{'message':{'role':'assistant','content':'executor actual response'},'finish_reason':'stop'}],
                    'usage':{'prompt_tokens':100,'completion_tokens':10,'prompt_tokens_details':{'cached_tokens':60}}}).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                root=Path(folder);control=root/'control.json';control.write_text('{"enabled":true}',encoding='utf-8')
                script='''import sys,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from execution_context import install_usage,wrapped_child_argv
from executor_limits import install_output_limits
from deepseek_mcp import provider_process,provider_retry
from deepseek_mcp.config import Config
install_output_limits()
provider_process.isolated_child_argv=lambda module,*args:wrapped_child_argv('native_provider_child.py',args)
install_usage()
config=Config('AUTH-EXCLUDE',Path(sys.argv[2]),base_url=sys.argv[3],max_output_tokens=32768)
response,error,retry=provider_retry.request_in_subprocess(config,[{'role':'user','content':'executor actual input'}],[],None,None)
print(json.dumps({'success':response is not None,'error':error}))
'''
                env={**os.environ,'TIERED_USAGE_FILE':str(root/'usage.jsonl'),'TIERED_CALL_LOG_SPOOL':str(root/'trace.jsonl'),'TIERED_CALL_LOG_CONTROL':str(control)}
                result=subprocess.run([sys.executable,'-c',script,str(Path(__file__).parent),str(root),'http://127.0.0.1:'+str(server.server_port)+'/v1'],env=env,capture_output=True,text=True,timeout=40)
                self.assertEqual(result.returncode,0,result.stderr);self.assertTrue(json.loads(result.stdout)['success'],result.stdout)
                self.assertEqual(received[0]['max_tokens'],32768)
                trace=[json.loads(line) for line in (root/'trace.jsonl').read_text(encoding='utf-8').splitlines()]
                request=next(e['data'] for e in trace if e['event']=='http_request');self.assertEqual(request['body'],received[0])
                usage=[json.loads(line) for line in (root/'usage.jsonl').read_text(encoding='utf-8').splitlines()]
                self.assertEqual(request['request_ref'],usage[-1]['request_ref']);self.assertEqual(usage[-1]['raw']['prompt_tokens'],100)
                self.assertNotIn('AUTH-EXCLUDE',(root/'trace.jsonl').read_text(encoding='utf-8'))
        finally:server.shutdown();server.server_close();thread.join(timeout=2)
