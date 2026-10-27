"""Offline money routing, account isolation, native wire and pairing regressions."""
import asyncio
import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, AsyncMock

import httpx
import google_billing as billing
import vertex_protocol as vertex
from engines import preferences

ACCOUNT='ABCDEF-123456-ABC123'
OTHER='ABCDEF-123456-ABC999'
CFG=dict(mode='auto',project='fixture-project',billing_account=ACCOUNT,location='global')
AGENT=dict(id='gemini-test',name='Gemini fixture',backend='openai-compatible',model='gemini-fixture',
    base_url='https://generativelanguage.googleapis.com/v1beta/openai',effort=None,no_auth=False,google_billing=CFG)
NOW=datetime(2026,10,24,12,tzinfo=timezone.utc)


def snapshot(rows=None,account=ACCOUNT):
    row=dict(id='FreeTrial:fixture',name='Free Trial',scope='Google Cloud',status='available',currency='GBP',remaining='226.17',
        starts_at=(NOW-timedelta(days=1)).isoformat(),expires_at=(NOW+timedelta(days=60)).isoformat(),eligible_routes=['vertex'])
    return dict(kind='cloud_credits',billing_account=account,source_url='https://console.cloud.google.com/billing/'+account+'/credits/all',
        complete=False,credits=[row] if rows is None else rows)


class BillingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'billing.json'
        self.patches=[patch.object(billing,'state_path',return_value=self.path),patch.object(billing,'utc',return_value=NOW)]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def test_trial_prefers_vertex_and_never_funds_developer(self):
        data=snapshot();data['credits'][0]['eligible_routes']=['vertex','developer'];billing.ingest(data)
        self.assertEqual(billing.select(AGENT,True),'vertex')
        self.assertEqual(billing.balances(ACCOUNT)['balances'],[{'route':'vertex','currency':'GBP','total_balance':'226.17','granted_balance':'226.17'}])
    def test_expired_and_unknown_expiry_are_not_spendable(self):
        rows=[]
        for expiry,status in [(NOW-timedelta(seconds=1),'available'),(None,'available'),(NOW+timedelta(days=1),'expired')]:
            r=snapshot()['credits'][0];r.update(id=r['id']+str(len(rows)),expires_at=expiry.isoformat() if expiry else None,status=status);rows.append(r)
        billing.ingest(snapshot(rows))
        with self.assertRaises(ValueError):billing.select(AGENT,True)
    def test_scope_unknown_and_future_grant_are_not_spendable(self):
        data=snapshot();data['credits'][0]['eligible_routes']=[];billing.ingest(data)
        with self.assertRaises(ValueError):billing.select(AGENT,True)
        data['credits'][0].update(eligible_routes=['vertex'],starts_at=(NOW+timedelta(days=1)).isoformat());billing.ingest(data)
        with self.assertRaises(ValueError):billing.select(AGENT,True)
    def test_stale_observations_reuse_cache_but_clock_rollback_does_not_route(self):
        billing.ingest(snapshot())
        self.assertEqual(billing.select(AGENT,True,NOW+timedelta(seconds=601)),'vertex')
        self.assertTrue(billing.balances(ACCOUNT,NOW+timedelta(seconds=601))['cached'])
        with self.assertRaises(ValueError):billing.select(AGENT,True,NOW-timedelta(seconds=1))
    def test_account_isolation_and_currency_separation(self):
        billing.ingest(snapshot(account=OTHER))
        with self.assertRaises(ValueError):billing.select(AGENT,True)
        rows=snapshot()['credits'];rows.append({**rows[0],'id':'FreeTrial:other-currency','currency':'USD','remaining':'10'})
        billing.ingest(snapshot(rows));self.assertEqual(len(billing.balances(ACCOUNT)['balances']),2)
    def test_prepay_fallback_requires_key_and_exact_source(self):
        data=dict(kind='developer_prepay',billing_account=ACCOUNT,source_url='https://aistudio.google.com/billing',account_verified=True,currency='GBP',remaining='5')
        billing.ingest(data);self.assertEqual(billing.select(AGENT,True),'developer')
        with self.assertRaises(ValueError):billing.select(AGENT,False)
        with self.assertRaises(ValueError):billing.ingest({**data,'account_verified':False})
        with self.assertRaises(ValueError):billing.ingest({**data,'source_url':'https://aistudio.google.com.evil.invalid/billing'})
    def test_eligible_developer_grant_requires_positive_prepay(self):
        data=dict(kind='developer_prepay',billing_account=ACCOUNT,source_url='https://aistudio.google.com/billing',account_verified=True,currency='GBP',remaining='4.00',eligible_cloud_credit={'currency':'GBP','remaining':'6.14'})
        billing.ingest(data);self.assertEqual(billing.balances(ACCOUNT)['balances'][0]['total_balance'],'10.14')
        data['remaining']='0';billing.ingest(data)
        with self.assertRaises(ValueError):billing.select(AGENT,True)
    def test_invalid_amounts_duplicates_and_wrong_account_urls_rejected(self):
        for value in ['NaN','Infinity','-1',None]:
            data=snapshot();data['credits'][0]['remaining']=value
            with self.assertRaises(ValueError):billing.ingest(data)
        data=snapshot();data['credits']*=2
        with self.assertRaises(ValueError):billing.ingest(data)
        with self.assertRaises(ValueError):billing.ingest({**snapshot(),'billing_account':OTHER})
    def test_pairing_rotation_and_no_accidental_auto_enable(self):
        first=billing.pairing();self.assertTrue(billing.paired(first));self.assertFalse(billing.paired('wrong'))
        self.assertIsNone(billing.select({},True))
        clean=preferences.validate_members([AGENT])[0];self.assertEqual(clean['google_billing'],CFG)
        data=billing.load();data.pop('pairing_token');billing.save(data);self.assertNotEqual(first,billing.pairing());self.assertFalse(billing.paired(first))
    def test_config_rejects_non_google_endpoint_and_malformed_ids(self):
        with self.assertRaises(ValueError):preferences.validate_members([{**AGENT,'base_url':'https://example.com/v1'}])
        for update in [dict(project='foo/../../x'),dict(billing_account='no'),dict(location='global/evil')]:
            with self.assertRaises(ValueError):billing.config({**CFG,**update})
    def test_browser_parser_fixtures(self):
        result=subprocess.run(['node',str(Path(__file__).parents[1]/'scripts/test_google_billing_reader.cjs')],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)


class OAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_project_mismatch_and_closed_accounts_block(self):
        async def handler(request):
            if request.url.path.endswith('/billingInfo'):return httpx.Response(200,json={'billingEnabled':True,'billingAccountName':'billingAccounts/'+OTHER})
            raise AssertionError('account lookup must not happen after mismatch')
        client=httpx.AsyncClient
        with patch.object(billing.httpx,'AsyncClient',side_effect=lambda **kw:client(transport=httpx.MockTransport(handler),**kw)):
            with self.assertRaisesRegex(ValueError,'不一致'):await billing.verify_project(CFG,'fixture-token')
    async def test_closed_account_blocks_even_with_enabled_project(self):
        def handler(request):return httpx.Response(200,json={'billingEnabled':True,'billingAccountName':'billingAccounts/'+ACCOUNT} if request.url.path.endswith('/billingInfo') else {'open':False})
        client=httpx.AsyncClient
        with patch.object(billing.httpx,'AsyncClient',side_effect=lambda **kw:client(transport=httpx.MockTransport(handler),**kw)):
            with self.assertRaisesRegex(ValueError,'未开放'):await billing.verify_project(CFG,'fixture-token')
    async def test_oauth_only_uses_fixed_google_endpoint_and_sanitizes_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'adc.json';path.write_text(json.dumps(dict(type='authorized_user',client_id='fixture',client_secret='secret-fixture',refresh_token='refresh-fixture',token_uri='https://evil.invalid/token')))
            seen=[]
            def handler(request):seen.append(str(request.url));return httpx.Response(400,json={'error':'secret-fixture refresh-fixture'})
            client=httpx.AsyncClient
            with patch.object(billing,'adc_path',return_value=path),patch.object(billing.httpx,'AsyncClient',side_effect=lambda **kw:client(transport=httpx.MockTransport(handler),**kw)):
                with self.assertRaises(ValueError) as error:await billing.access_token()
            self.assertNotIn('secret-fixture',str(error.exception));self.assertEqual(seen,['https://oauth2.googleapis.com/token'])
    async def test_auto_developer_key_project_mismatch_blocks_before_inference(self):
        def handler(request):return httpx.Response(200,json={'parent':'projects/999/locations/global'} if request.url.host=='apikeys.googleapis.com' else {'projectNumber':'123'})
        client=httpx.AsyncClient
        with patch.object(billing,'select',return_value='developer'),patch.object(preferences,'get_member_key',return_value='fixture-key'),patch.object(billing,'access_token',AsyncMock(return_value='fixture-token')),patch.object(billing,'verify_project',AsyncMock()),patch.object(billing.httpx,'AsyncClient',side_effect=lambda **kw:client(transport=httpx.MockTransport(handler),**kw)):
            with self.assertRaisesRegex(ValueError,'API Key'):await billing.prepare(AGENT)
    async def test_prepare_changes_session_binding_before_creating_history(self):
        import model_catalog
        with patch.object(billing,'select',return_value='vertex'),patch.object(billing,'access_token',AsyncMock(return_value='fixture-token')),patch.object(billing,'verify_project',AsyncMock()),patch.object(preferences,'get_member_key',return_value='fixture-key'):
            source=await billing.prepare(AGENT)
            self.assertNotEqual(model_catalog.connection_context(AGENT)[0],model_catalog.connection_context(source)[0])
            self.assertNotIn('fixture-token',json.dumps(source));self.assertTrue(vertex.is_vertex(source['base_url']))


class VertexTests(unittest.TestCase):
    def test_native_call_id_is_returned_on_function_result(self):
        raw=vertex.response({'candidates':[{'finishReason':'STOP','content':{'role':'model','parts':[{'functionCall':{'id':'server-call','name':'read','args':{}},'thoughtSignature':'opaque'}]}}]})
        message=raw['choices'][0]['message'];body=vertex.request('m',[message,{'role':'tool','tool_call_id':'server-call','content':'ok'}])
        self.assertEqual(body['contents'][-1]['parts'][0]['functionResponse']['id'],'server-call')
    def test_signed_parallel_function_history_is_lossless(self):
        original={'candidates':[{'finishReason':'STOP','content':{'role':'model','parts':[
            {'thought':True,'text':'private','thoughtSignature':'opaque-a'},
            {'functionCall':{'name':'read','args':{'path':'a'}},'thoughtSignature':'opaque-b'},
            {'functionCall':{'name':'read','args':{'path':'b'}}}]}}],
            'usageMetadata':{'promptTokenCount':10,'candidatesTokenCount':2,'thoughtsTokenCount':3,'cachedContentTokenCount':4}}
        preserved=copy.deepcopy(original);raw=vertex.response(original);message=raw['choices'][0]['message'];self.assertEqual(message['content'],'')
        messages=[{'role':'system','content':'system'},{'role':'user','content':'question'},message]
        messages += [{'role':'tool','tool_call_id':c['id'],'content':'result'} for c in message['tool_calls']]
        body=vertex.request('gemini-fixture',messages)
        self.assertEqual(body['contents'][1],original['candidates'][0]['content']);self.assertEqual(len(body['contents'][2]['parts']),2)
        self.assertEqual(original,preserved);self.assertEqual(raw['usage']['completion_tokens'],5)
    def test_truncation_or_unsigned_history_never_claims_success(self):
        with self.assertRaises(ValueError):vertex.response({'candidates':[{'finishReason':'MAX_TOKENS'}]})
        with self.assertRaises(ValueError):vertex.request('m',[{'role':'assistant','content':'unsigned'}])
        with self.assertRaises(ValueError):vertex.request('m',[{'role':'tool','tool_call_id':'missing','content':'result'}])
    def test_native_tool_schema_does_not_send_openai_only_fields(self):
        body=vertex.request('m',[{'role':'user','content':'hello'}],[{'type':'function','function':{'name':'read','strict':True,'parameters':{'type':'object'}}}],effort='high')
        declaration=body['tools'][0]['functionDeclarations'][0]
        self.assertNotIn('strict',declaration);self.assertEqual(declaration['parametersJsonSchema'],{'type':'object'})
        self.assertEqual(body['generationConfig']['thinkingConfig'],{'thinkingLevel':'HIGH'})


class WireTests(unittest.IsolatedAsyncioTestCase):
    async def test_production_conversation_posts_to_vertex_and_keeps_bearer_out_of_body(self):
        import provider_sessions
        seen=[]
        def handler(request):
            seen.append(request)
            return httpx.Response(200,json={'candidates':[{'finishReason':'STOP','content':{'role':'model','parts':[{'text':'passed','thoughtSignature':'signed'}]}}],'usageMetadata':{'promptTokenCount':2,'candidatesTokenCount':1}})
        turn=SimpleNamespace(source=AGENT,state={'native':{}},budget={'output':1024,'tool_rounds':2},input_budget=100000,
            model='gemini-fixture',store=object(),instructions=lambda:'system',schemas=lambda:[],save=lambda:None)
        client=httpx.AsyncClient
        with patch.object(provider_sessions.httpx,'AsyncClient',side_effect=lambda **kw:client(transport=httpx.MockTransport(handler),**kw)),patch('prefix_observer.observe'):
            answer,usage=await provider_sessions.api_conversation(turn,'hello','fixture-token','https://aiplatform.googleapis.com/v1/projects/fixture-project/locations/global',lambda text:None)
        self.assertEqual(answer,'passed');self.assertEqual(len(seen),1)
        self.assertEqual(seen[0].headers['Authorization'],'Bearer fixture-token');self.assertNotIn('fixture-token',seen[0].content.decode())
        self.assertEqual(seen[0].headers['x-goog-user-project'],'fixture-project')
        self.assertIn('/publishers/google/models/gemini-fixture:generateContent',str(seen[0].url))
        self.assertEqual(turn.state['native']['messages'][-1]['_vertex_content']['parts'][0]['thoughtSignature'],'signed')


class PairingApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
        # Reuse the existing isolated server harness; its SQLite lifetime spans
        # the suite, not this individual test's temporary credential directory.
        import test_gui
        server=test_gui.server;self.server=server
        self.patches=[patch.object(billing,'state_path',return_value=root/'billing.json'),
            patch.object(preferences,'STATE',root/'prefs'),patch.object(preferences,'PREFS',root/'prefs/preferences.json'),
            patch.object(preferences,'PROFILE',root/'profile.toml')]
        for p in self.patches:p.start()
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url=server.ORIGIN)
    async def asyncTearDown(self):
        await self.client.aclose()
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    async def test_companion_is_write_only_and_needs_pairing(self):
        origin={'Origin':'chrome-extension://fixture-extension','x-google-billing-token':billing.pairing()}
        self.assertEqual((await self.client.post('/api/google-billing/sync',json=snapshot())).status_code,403)
        self.assertEqual((await self.client.get('/api/google-billing',headers=origin)).status_code,403)
        self.assertEqual((await self.client.get('/api/bootstrap',headers=origin)).status_code,403)
        r=await self.client.post('/api/google-billing/sync',json=snapshot(),headers=origin)
        self.assertEqual(r.status_code,200,r.text);self.assertNotIn('pairing_token',r.text)
    async def test_pairing_does_not_bypass_origin_source_or_size_validation(self):
        headers={'x-google-billing-token':billing.pairing(),'Origin':'https://evil.invalid'}
        self.assertEqual((await self.client.post('/api/google-billing/sync',json=snapshot(),headers=headers)).status_code,403)
        headers['Origin']='chrome-extension://fixture-extension'
        self.assertEqual((await self.client.post('/api/google-billing/sync',json={**snapshot(),'source_url':'https://evil.invalid'},headers=headers)).status_code,400)
        self.assertEqual((await self.client.post('/api/google-billing/sync',content='x'*100001,headers=headers)).status_code,413)
    async def test_verification_requires_app_csrf_and_only_calls_read_api(self):
        server=self.server
        body={'action':'verify','config':CFG}
        self.assertEqual((await self.client.post('/api/google-billing',json=body)).status_code,403)
        with patch.object(billing,'access_token',AsyncMock(return_value='private-token')),patch.object(billing,'verify_project',AsyncMock(return_value={'billingEnabled':True})) as verify:
            r=await self.client.post('/api/google-billing',json=body,headers={'x-tiered-token':server.TOKEN})
        self.assertEqual(r.status_code,200);self.assertNotIn('private-token',r.text);verify.assert_awaited_once()


if __name__=='__main__':unittest.main()
