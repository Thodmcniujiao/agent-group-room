"""Returned effort metadata, safe persistence and exact native wire parameters."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch

import engines
from engines import preferences
import model_catalog
import provider_sessions


class EffortMetadataTests(unittest.IsolatedAsyncioTestCase):
    def test_normalization_keeps_all_returned_values_and_original_descriptions(self):
        levels=['none','minimal','low','medium','high','xhigh','max','ultra','future_level']
        raw=dict(id='fixture-model',efforts=levels,effort_source='fixture.official_field',
            default_effort='minimal',effort_descriptions={'ultra':'Original English description'})
        result=model_catalog.normalize([raw])[0]
        self.assertEqual(result['efforts'],levels)
        self.assertEqual(result['default_effort'],'minimal')
        self.assertEqual(result['effort_descriptions']['ultra'],'Original English description')

    def test_legacy_cache_and_absent_metadata_cannot_supply_choices_or_defaults(self):
        for raw in [dict(id='model',efforts=['high']),dict(id='model'),
                    dict(id='model',effort_source='returned',efforts=[],default_effort='high')]:
            result=model_catalog.normalize([raw])[0]
            self.assertEqual(result['efforts'],[])
            self.assertIsNone(result['default_effort'])

    def test_deepseek_uses_returned_order_and_default_without_compatibility_aliases(self):
        raw={'id':'deepseek-fixture','effort':{'supported_levels':['low','high','max'],'default_level':'high'}}
        model=model_catalog.normalize([model_catalog.api_model_info(raw,'deepseek')])[0]
        self.assertEqual(model['efforts'],['low','high','max'])
        self.assertEqual(model['default_effort'],'high')
        self.assertNotIn('medium',model['efforts']);self.assertNotIn('none',model['efforts'])
        self.assertEqual(model_catalog.normalize([model_catalog.api_model_info({'id':'without-effort'},'deepseek')])[0]['efforts'],[])

    async def test_cli_adapters_read_per_model_fields_without_inference_or_fallback(self):
        calls=[]
        async def request(method,params):
            calls.append(method)
            return {'data':[{'id':'gpt-fixture','supportedReasoningEfforts':[
                {'reasoningEffort':'ultra','description':'Original text'}],'defaultReasoningEffort':'ultra'}]}
        @asynccontextmanager
        async def codex():yield request
        @asynccontextmanager
        async def claude():
            yield None,{'models':[{'value':'opus','resolvedModel':'claude-fixture','supportedEffortLevels':['low','max']},
                {'value':'haiku','resolvedModel':'claude-no-effort'}]}
        with patch.object(engines,'codex_metadata',codex),patch.object(engines,'claude_metadata',claude):
            gpt=model_catalog.normalize(await engines.chatgpt_catalog())[0]
            models=model_catalog.normalize(await engines.claude_catalog())
        self.assertEqual(calls,['model/list'])
        self.assertEqual(gpt['efforts'],['ultra']);self.assertEqual(gpt['default_effort'],'ultra')
        self.assertEqual(models[0]['efforts'],['low','max'])
        self.assertEqual(models[1]['efforts'],[])
        self.assertIsNone(models[0]['default_effort'])

    async def test_runtime_rejects_unsupported_stored_value_and_omits_unreturned_field(self):
        with tempfile.TemporaryDirectory() as folder:
            catalog=model_catalog.ModelCatalog(Path(folder)/'catalog.json')
            catalog.cache['fixture']={'models':model_catalog.normalize([
                dict(id='real-model',efforts=['low','ultra'],effort_source='returned'),dict(id='no-field')]),'updated':'fixture'}
            agent=dict(id='chatgpt',backend='chatgpt')
            with patch.object(model_catalog,'shared_catalog',return_value=catalog),patch.object(catalog,'context',return_value=('fixture','')):
                self.assertEqual(await model_catalog.effective_effort(agent,'real-model','ultra'),'ultra')
                with self.assertRaisesRegex(ValueError,'未返回推理强度'):
                    await model_catalog.effective_effort(agent,'real-model','max')
                self.assertIsNone(await model_catalog.effective_effort(agent,'no-field','high'))
                self.assertIsNone(await model_catalog.effective_effort(agent,'custom-id','high'))
                self.assertIsNone(await model_catalog.effective_effort(agent,'real-model',None))

    async def test_deepseek_api_effort_uses_credential_scoped_metadata_too(self):
        with tempfile.TemporaryDirectory() as folder:
            catalog=model_catalog.ModelCatalog(Path(folder)/'catalog.json')
            raw={'id':'deepseek-fixture','effort':{'supported_levels':['low','high','max'],'default_level':'high'}}
            catalog.cache['fixture']={'models':model_catalog.normalize([model_catalog.api_model_info(raw,'deepseek')]),'updated':'fixture'}
            agent=dict(id='deepseek',backend='deepseek',base_url='https://api.deepseek.com')
            with patch.object(model_catalog,'shared_catalog',return_value=catalog),patch.object(catalog,'context',return_value=('fixture','')):
                self.assertEqual(await model_catalog.effective_effort(agent,'deepseek-fixture','max'),'max')
                with self.assertRaises(ValueError):await model_catalog.effective_effort(agent,'deepseek-fixture','medium')

    def test_preferences_preserve_exact_ids_and_allow_omission_without_fixed_enum(self):
        for value in ['ultra','minimal','future_level','ExactCase',None,'']:
            self.assertEqual(preferences.effort_id(value),value or None)
        for value in ['high"\nmalicious=1',[],{},1]:
            with self.assertRaises(ValueError):preferences.effort_id(value)
        agents=preferences.default_members(preferences.DEFAULTS)
        agents[0]['effort']='ultra';agents[1]['effort']=None
        validated=preferences.validate_members(agents)
        _,team=preferences.resolve_team(validated,[dict(agent_id='chatgpt',effort='ultra')])
        self.assertEqual(team[0]['effort'],'ultra')
        self.assertIsNone(validated[1]['effort'])


class NativeEffortWireTests(unittest.IsolatedAsyncioTestCase):
    def test_coding_executor_overrides_inherited_effort_or_explicitly_omits_it(self):
        previous=asyncio.get_event_loop_policy()
        try:import deepseek_server
        finally:asyncio.set_event_loop_policy(previous)
        from deepseek_mcp.config import Config
        from deepseek_mcp.provider_child import _request_arguments
        with tempfile.TemporaryDirectory() as folder:
            original=Config('fixture-key',Path(folder),allowed_tools=['Read'],reasoning_effort='high')
            for value in ['max','']:
                with patch.object(deepseek_server,'_original_load',return_value=original),patch.dict(os.environ,{'TIERED_MEMBER_EFFORT':value}):
                    selected=deepseek_server._selected_config()
                self.assertEqual(selected.reasoning_effort,value or 'provider-default')
                settings={'model':selected.model,'base_url':selected.base_url,'max_output_tokens':1000}
                if value:settings['reasoning_effort']=selected.reasoning_effort
                wire=_request_arguments(settings,[{'role':'user','content':'fixture'}],[])
                if value:self.assertEqual(wire['reasoning_effort'],'max')
                else:self.assertNotIn('reasoning_effort',wire)

    async def test_deepseek_sdk_serializes_exact_level_or_omits_in_every_request(self):
        import httpx
        real=provider_sessions.AsyncOpenAI
        for effort in ['max',None]:
            calls=[]
            def respond(request):
                calls.append(json.loads(request.content))
                chunk={'id':'fixture','object':'chat.completion.chunk','created':0,'model':'deepseek-fixture',
                    'choices':[{'index':0,'delta':{'role':'assistant','content':'fixture reply'},'finish_reason':'stop'}]}
                return httpx.Response(200,headers={'content-type':'text/event-stream'},content='data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n')
            def client(**kwargs):return real(**kwargs,http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
            turn=SimpleNamespace(state={'native':{}},role='executor',model='deepseek-fixture',tools=False,
                input_budget=32000,budget={'output':1000},save=lambda:None)
            with patch.object(provider_sessions,'AsyncOpenAI',side_effect=client):
                answer,_=await provider_sessions.api_conversation(turn,'fixture','fixture-key','https://api.deepseek.com',lambda _:None,effort)
            self.assertEqual(answer,'fixture reply')
            if effort is None:self.assertNotIn('reasoning_effort',calls[0])
            else:self.assertEqual(calls[0]['reasoning_effort'],effort)

    async def test_codex_turn_and_thread_config_use_identical_raw_value_or_omit(self):
        for effort in ['ultra',None]:
            calls=[]
            class RPC:
                def __init__(self,*args):self.done=None;self.text='fixture reply';self.usage={}
                async def consume(self):return None
                async def send(self,*args):pass
                async def request(self,method,params):
                    calls.append((method,params))
                    if method=='turn/start':self.done={'status':'completed'}
                    return {'thread':{'id':'fixture-native-thread'}}
            turn=SimpleNamespace(input_budget=32000,coding=False,model='fixture-model',role='discussion',
                state={'native':{}},tools=False,project_tools=None,save=lambda:None)
            with tempfile.TemporaryDirectory() as folder,patch.object(provider_sessions,'CodexRPC',RPC),\
                    patch.object(engines,'find_cli',return_value='fixture-cli'),\
                    patch.object(asyncio,'create_subprocess_exec',new=AsyncMock(return_value=object())),\
                    patch.object(engines,'kill_tree',new=AsyncMock()):
                await provider_sessions.codex_conversation(turn,'fixture',Path(folder),effort,lambda _:None)
            start=next(p for method,p in calls if method=='thread/start')
            message=next(p for method,p in calls if method=='turn/start')
            if effort is None:
                self.assertNotIn('model_reasoning_effort',start['config']);self.assertNotIn('effort',message)
            else:
                self.assertEqual(start['config']['model_reasoning_effort'],effort);self.assertEqual(message['effort'],effort)

    async def test_claude_cli_sends_raw_level_or_no_effort_argument(self):
        for effort in ['max',None]:
            proc=SimpleNamespace(stdin=SimpleNamespace(write=lambda _:None,drain=AsyncMock(),close=lambda:None),
                stdout=SimpleNamespace(readline=AsyncMock(side_effect=[json.dumps({'type':'result','result':'fixture reply','usage':{}}).encode(),b''])),
                wait=AsyncMock(),returncode=0)
            turn=SimpleNamespace(state={'native':{}},coding=False,model='fixture-model',tools=False,role='discussion',budget={'soft':100000},save=lambda:None)
            with tempfile.TemporaryDirectory() as folder,patch.object(engines,'find_cli',return_value='fixture-cli'),\
                    patch.object(asyncio,'create_subprocess_exec',new=AsyncMock(return_value=proc)) as spawn,\
                    patch.object(engines,'kill_tree',new=AsyncMock()):
                await provider_sessions.claude_conversation(turn,'fixture',Path(folder),effort,lambda _:None)
            args=spawn.call_args.args
            if effort is None:self.assertNotIn('--effort',args)
            else:self.assertEqual(args[args.index('--effort')+1],effort)
