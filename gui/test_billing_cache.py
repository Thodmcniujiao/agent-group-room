"""Cached account readings and host-only billing waits must not burn model turns."""
import asyncio
import copy
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock,patch

import account_usage
from engines import preferences
import google_billing as billing
from coordination import Coordinator
from preflight import RESUME_BUTTON
from store import Store
from workflows import Workflows
from workflow_state import new_state
from test_google_billing import ACCOUNT,AGENT,NOW,snapshot


class CacheReadingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.patches=[patch.object(billing,'state_path',return_value=self.root/'billing.json'),patch.object(billing,'utc',return_value=NOW)]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def test_old_google_cache_routes_and_reminds_without_renewing_its_timestamp(self):
        billing.ingest(snapshot());original=billing.load()
        with patch.object(preferences,'get_member_key',return_value='fixture-key'):
            settings={'agents':[AGENT]};monitor=account_usage.UsageMonitor()
            with patch.object(billing,'utc',return_value=NOW+timedelta(minutes=29)):
                first=monitor.snapshot(settings);self.assertEqual(first['alerts'],[])
                self.assertTrue(first['agents'][AGENT['id']]['cached'])
            for minutes in [30,180]:
                with patch.object(billing,'utc',return_value=NOW+timedelta(minutes=minutes)):
                    state=monitor.snapshot(settings);entry=state['agents'][AGENT['id']]
                    self.assertEqual(entry['selected_route'],'vertex');self.assertEqual(entry['balances'][0]['total_balance'],'226.17')
                    self.assertEqual(len(state['alerts']),1)
                    if minutes==30:identity=state['alerts'][0]['id']
                    else:self.assertEqual(state['alerts'][0]['id'],identity)
            self.assertEqual(billing.load(),original)
            billing.ingest(snapshot())
            self.assertEqual(monitor.snapshot(settings)['alerts'],[])

    def test_failed_empty_sync_keeps_good_cache_and_expired_credit_still_blocks(self):
        data=snapshot();data['credits'][0]['expires_at']=(NOW+timedelta(hours=1)).isoformat();billing.ingest(data)
        before=billing.load()
        with self.assertRaises(ValueError):billing.ingest(snapshot([]))
        self.assertEqual(billing.load(),before)
        with self.assertRaises(billing.BillingUnavailable):billing.select(AGENT,True,NOW+timedelta(hours=2))

    def test_partial_table_retains_unseen_and_unconfirmed_rows_without_freshening_them(self):
        initial=snapshot();other={**initial['credits'][0],'id':'FreeTrial:second','remaining':'10'}
        initial['credits'].append(other);billing.ingest(initial)
        partial=snapshot();partial['credits'][0].update(expires_at=None,status='unknown',remaining='1')
        later=NOW+timedelta(hours=1)
        with patch.object(billing,'utc',return_value=later):
            billing.ingest(partial);reading=billing.balances(ACCOUNT)
            self.assertEqual(reading['balances'][0]['total_balance'],'236.17')
            self.assertEqual(reading['cache_age_seconds'],3600);self.assertEqual(reading['updated'],NOW.isoformat())
            zero=snapshot();zero['credits'][0]['remaining']='0';billing.ingest(zero)
            self.assertEqual(billing.balances(ACCOUNT)['balances'][0]['total_balance'],'10')
            cleared=snapshot([]);cleared['complete']=True;billing.ingest(cleared)
            with self.assertRaises(billing.BillingUnavailable):billing.select(AGENT,True)

    def test_cached_grant_keeps_priority_and_real_zero_replaces_cache(self):
        billing.ingest(snapshot())
        later=NOW+timedelta(hours=1)
        prepay=dict(kind='developer_prepay',billing_account=ACCOUNT,source_url='https://aistudio.google.com/billing',account_verified=True,currency='GBP',remaining='4.00')
        with patch.object(billing,'utc',return_value=later):
            billing.ingest(prepay);self.assertEqual(billing.select(AGENT,True),'vertex')
            cleared=snapshot();cleared['credits'][0]['remaining']='0';billing.ingest(cleared)
            self.assertEqual(billing.select(AGENT,True),'developer')
            billing.ingest({**prepay,'remaining':'0'})
            with self.assertRaises(billing.BillingUnavailable):billing.select(AGENT,True)

    async def test_non_google_cache_survives_failures_restart_and_isolated_credentials(self):
        agent=dict(id='deepseek-test',name='DeepSeek',backend='deepseek',model='deepseek-flash',base_url='https://api.deepseek.com',effort=None,no_auth=False)
        settings={'agents':[agent],'supervisor':{'provider':agent['id']},'members':[]}
        path=self.root/'account-cache.json';monitor=account_usage.UsageMonitor(path=path)
        reading=dict(status='ready',plan='',windows=[],balances=[{'currency':'CNY','total_balance':'12.50'}],detail='balance')
        with patch.object(preferences,'get_member_key',return_value='fixture-private-key'):
            with patch.object(account_usage,'read_account',AsyncMock(return_value=reading)):
                await asyncio.gather(*monitor.request_refresh(settings))
            fingerprint=account_usage.connection_context(agent)[0]
            monitor.cache[fingerprint]['updated']=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat();monitor.persist()
            stamp=monitor.cache[fingerprint]['updated']
            with patch.object(account_usage,'read_account',AsyncMock(side_effect=RuntimeError('fixture-private-key'))):
                await asyncio.gather(*monitor.request_refresh(settings,force=True))
            result=monitor.snapshot(settings);entry=result['agents'][agent['id']]
            self.assertEqual(entry['status'],'ready');self.assertTrue(entry['cached']);self.assertEqual(entry['updated'],stamp)
            self.assertEqual(entry['balances'],reading['balances']);self.assertEqual(len(result['alerts']),1)
            self.assertNotIn('fixture-private-key',path.read_text());self.assertNotIn('fixture-private-key',json.dumps(result))
            reopened=account_usage.UsageMonitor(path=path)
            self.assertEqual(reopened.snapshot(settings)['agents'][agent['id']]['balances'],reading['balances'])
            self.assertEqual(account_usage.team_quota(settings,result)['participants'][0]['level'],'ok')
            with patch.object(account_usage,'read_account',AsyncMock(return_value={'status':'unavailable','balances':[],'windows':[],'detail':'no reading'})):
                await asyncio.gather(*monitor.request_refresh(settings,force=True))
            self.assertEqual(monitor.snapshot(settings)['agents'][agent['id']]['updated'],stamp)
            with patch.object(preferences,'get_member_key',return_value='different-fixture-key'):
                self.assertEqual(reopened.snapshot(settings)['agents'][agent['id']]['balances'],[])
            with patch.object(account_usage,'read_account',AsyncMock(return_value={**reading,'balances':[{'currency':'CNY','total_balance':'0'}]})):
                await asyncio.gather(*monitor.request_refresh(settings,force=True))
            self.assertEqual(account_usage.team_quota(settings,monitor.snapshot(settings))['participants'][0]['level'],'exhausted')
        await monitor.close();await reopened.close()


class BillingWaitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.patches=[patch.object(preferences,'PREFS',self.root/'preferences.json'),patch.object(preferences,'STATE',self.root/'user'),
            patch.object(billing,'state_path',return_value=self.root/'billing.json'),patch.object(billing,'utc',return_value=NOW),
            patch.object(preferences,'get_member_key',return_value='fixture-key'),patch('coordination.code_hash',AsyncMock(return_value='fixture-code')),
            patch('coordination.roster.connections',AsyncMock(return_value={AGENT['id']:{'ready':True},'claude':{'ready':True}}))]
        for p in self.patches:p.start()
        self.settings=preferences.load();self.settings['agents'].append(copy.deepcopy(AGENT))
        self.settings['members']=[{**AGENT,'agent_id':AGENT['id']}];self.settings['supervisor']['provider']=AGENT['id']
        self.settings['executor']=None;self.settings['supervisor']['max_steps']=3
        self.store=Store(self.root/'state');self.rid=self.store.create('Billing wait',str(self.root))['id']
        mid=self.store.message(self.rid,'you','工作');policy=self.store.policy(self.rid,'keep real evidence')
        self.store.save_workflow(self.rid,new_state('工作',mid,policy),0)
        self.work=Workflows(self.store);self.work.quota=AsyncMock(return_value={'participants':[]})
        self.work.reply=AsyncMock(return_value=json.dumps({'public_message':'等待下一条需求','control':{'rules':[{'id':'R1','text':'真实记录','criterion':'核对证据'}],'checks_patch':[],'action':{'type':'wait'}}}))
        self.coordinator=Coordinator(self.work,self.rid,self.root,self.settings)
    async def asyncTearDown(self):
        self.store.db.close()
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    async def test_no_cache_waits_once_without_any_monitor_request_or_round_then_resumes(self):
        await self.coordinator.supervisor_loop({'type':'user_input'})
        await self.coordinator.supervisor_loop({'type':'user_input'})
        state,_=self.store.load_workflow(self.rid)
        self.work.reply.assert_not_awaited();self.assertEqual(state['step'],0);self.assertTrue(state['blocker']['billing'])
        self.assertEqual(len([m for m in self.store.messages(self.rid) if m['status']=='host_error']),1)
        billing.ingest(snapshot());self.store.message(self.rid,'you','继续')
        await self.coordinator.run(RESUME_BUTTON)
        self.work.reply.assert_awaited_once();state,_=self.store.load_workflow(self.rid)
        self.assertEqual(state['step'],1);self.assertIsNone(state['blocker'])

    async def test_prepare_failure_does_not_loop_or_charge_a_round(self):
        billing.ingest(snapshot());self.work.reply.side_effect=billing.BillingUnavailable('临时无法核验 Google 连接')
        await self.coordinator.supervisor_loop({'type':'user_input'})
        self.work.reply.assert_awaited_once();self.assertEqual(self.coordinator.state['step'],0)
        self.assertTrue(self.coordinator.state['blocker']['billing'])
        self.assertFalse(any(m['phase']=='安排校验' for m in self.store.messages(self.rid)))

    async def test_stale_cache_notice_is_host_authored_once_across_resume(self):
        billing.ingest(snapshot())
        alert=dict(id='one-cache-epoch',agents=['Gemini'],text='缓存已超过 30 分钟未更新',updated=NOW.isoformat(),url='/assets/google-billing.html')
        self.work.quota.return_value={'participants':[],'alerts':[alert]}
        with patch.object(billing,'utc',return_value=NOW+timedelta(hours=1)):
            await self.coordinator.supervisor_loop({'type':'user_input'})
            reopened=Coordinator(self.work,self.rid,self.root,self.settings)
            reply=json.loads(self.work.reply.return_value);reply['control'].pop('rules');self.work.reply.return_value=json.dumps(reply)
            await reopened.supervisor_loop({'type':'user_input'})
        self.assertEqual(self.work.reply.await_count,2)
        self.assertEqual(len([m for m in self.store.messages(self.rid) if m['phase']=='余额缓存提醒']),1)

    async def test_member_billing_failure_waits_for_parallel_peers_without_an_extra_monitor_call(self):
        billing.ingest(snapshot())
        claude=copy.deepcopy(next(a for a in self.settings['agents'] if a['id']=='claude'))
        self.settings['members'].append({**claude,'agent_id':'claude'});self.coordinator.labels['claude']=claude['name']
        control=json.loads(self.work.reply.return_value)
        control['control']['action']={'type':'ask','kind':'clarify','instruction':'核对','agents':[AGENT['id'],'claude'],'parallel':True}
        completed=[]
        async def reply(rid,identity,*args,**kwargs):
            if identity=='supervisor':return json.dumps(control)
            if identity==AGENT['id']:raise billing.BillingUnavailable('余额核验临时不可用')
            await asyncio.sleep(0.01);self.store.message(rid,identity,'真实同伴回复');completed.append(identity);return '真实同伴回复'
        self.work.reply.side_effect=reply
        await self.coordinator.supervisor_loop({'type':'user_input'})
        self.assertEqual(self.work.reply.await_count,3);self.assertEqual(completed,['claude'])
        self.assertEqual(self.coordinator.state['step'],1);self.assertTrue(self.coordinator.state['blocker']['billing'])
        self.assertFalse(any(m['phase']=='安排校验' for m in self.store.messages(self.rid)))
