"""Discussion limits force the last objector's proposal straight to human approval."""
import asyncio
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock,patch

from coordination import Coordinator
from discussion_resolution import FINAL_PHASE,last_objector,resolve,opposes
from engines import preferences
from preflight import RESUME_BUTTON
from store import Store
from workflow_state import new_state,adopt,accept_user_approval
from workflows import Workflows

FINAL='# 最终方案\n## 目标\n实现要求。\n## 实施方案\n修改 out.txt，保持接口，先实现后核对。\n## 验收\n检查 out.txt 内容。\n## 风险与未决项\n无。'


class DiscussionResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.root=self.base/'project';self.root.mkdir()
        subprocess.run(['git','init',str(self.root)],capture_output=True,check=True)
        self.patches=[patch.object(preferences,'PREFS',self.base/'preferences.json'),patch.object(preferences,'STATE',self.base/'user'),
            patch('coordination.code_hash',AsyncMock(return_value='fixture-code')),
            patch('coordination.roster.connections',AsyncMock(return_value={'chatgpt':{'ready':True},'claude':{'ready':True}}))]
        for p in self.patches:p.start()
        self.settings=preferences.load();self.settings['executor']='deepseek';self.settings['discussion_tolerance']=1
        self.store=Store(self.base/'state');self.rid=self.store.create('Discussion limit',str(self.root))['id']
        mid=self.store.message(self.rid,'you','实现要求');self.policy=self.store.policy(self.rid,'keep real evidence')
        state=new_state('实现要求',mid,self.policy)
        original=self.store.message(self.rid,'chatgpt',FINAL.replace('最终','原始'))
        self.plan=adopt(state,self.store,self.rid,{'plan_id':'P2','message_id':original},{'chatgpt','claude'})
        self.store.save_workflow(self.rid,state,0)
        self.work=Workflows(self.store);self.work.quota=AsyncMock(return_value={'participants':[]})
        self.work.reply=AsyncMock(side_effect=self.answer)
        self.c=Coordinator(self.work,self.rid,self.root,self.settings)
        self.connections={'chatgpt':{'ready':True},'claude':{'ready':True}}
        self.output=FINAL
    async def asyncTearDown(self):
        self.store.db.close()
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    async def answer(self,rid,agent,prompt,root,settings,phase,**kwargs):
        if agent=='supervisor':raise AssertionError('Host resolution must not call the supervisor')
        self.store.message(rid,agent,self.output,phase=phase);return self.output

    def vote(self,actor,agree=False):
        text='表态：'+('同意 ' if agree else '反对 ')+self.plan['ref']+('' if agree else '：需要修改实现方式')
        mid=self.store.message(self.rid,actor,text,phase='协作发言')
        self.c.evidence(dict(kind='ask',ask_kind='review',agent=actor,status='complete',plan_ref=self.plan['ref'],message_id=mid))
        return mid

    def limit(self):self.c.state['discussion_rounds']['P2']=1;self.c.save()

    async def test_last_completed_objector_submits_exact_original_without_supervisor_or_fake_approval(self):
        self.vote('chatgpt');self.vote('claude');self.limit()
        await self.c.supervisor_loop({'type':'user_input'})
        self.work.reply.assert_awaited_once();self.assertEqual(self.work.reply.await_args.args[1],'claude')
        self.assertEqual(self.c.state['step'],0);self.assertEqual(self.c.state['current_plan'],'P2@2')
        self.assertEqual(self.store.room(self.rid)['status'],'awaiting_approval')
        document=self.store.approval_document(self.rid,self.c.state['approval_document_id'])
        self.assertEqual(document['data']['original'],FINAL);self.assertEqual(document['data']['author'],'Claude')
        self.assertFalse(any(a['kind']=='user' for a in self.store.approvals(self.rid)))
        await resolve(self.c,self.policy,{'type':'host_action'},self.connections,None)
        self.assertEqual(self.work.reply.await_count,1)
        mid=self.store.message(self.rid,'you','同意');self.assertIsNotNone(accept_user_approval(self.c.state,self.store,self.rid,mid));self.c.save()
        self.assertFalse(await resolve(self.c,self.policy,{'type':'user_input'},self.connections,None))

    async def test_parallel_completion_order_overrides_message_start_order(self):
        first=self.store.message(self.rid,'chatgpt','表态：反对 '+self.plan['ref']+'：先开始后完成')
        second=self.store.message(self.rid,'claude','表态：反对 '+self.plan['ref']+'：后开始先完成')
        for actor,mid in [('claude',second),('chatgpt',first)]:
            self.c.evidence(dict(kind='ask',ask_kind='review',agent=actor,status='complete',plan_ref=self.plan['ref'],message_id=mid))
        self.assertEqual(last_objector(self.c.state,self.store,self.rid,{'chatgpt','claude'})[0],'chatgpt')

    async def test_explicit_natural_rebuttal_is_recognized_without_guessing_from_quotes_or_shared_names(self):
        self.assertTrue(opposes('我的结论：反对当前方案，应该换一种实现。',self.plan['ref']))
        for text in ['我不反对当前方案。','> 我反对当前方案。','```\n表态：反对 '+self.plan['ref']+'：示例\n```','我反对 P2@99：另一版','建议补充验收。']:
            self.assertFalse(opposes(text,self.plan['ref']))
        self.c.labels['chatgpt']=self.c.labels['claude']='同名实例'
        self.vote('chatgpt')
        mid=self.store.message(self.rid,'claude','我不同意当前方案，需要提供替代。')
        self.c.evidence(dict(kind='ask',ask_kind='review',agent='claude',status='complete',plan_ref=self.plan['ref'],message_id=mid))
        self.assertEqual(last_objector(self.c.state,self.store,self.rid,{'chatgpt','claude'})[0],'claude')

    async def test_unavailable_last_objector_pauses_without_reassigning_or_spending_monitor_rounds(self):
        self.vote('claude');self.limit()
        self.assertTrue(await resolve(self.c,self.policy,{}, {'claude':{'ready':False}},None))
        self.work.reply.assert_not_awaited();self.assertEqual(self.c.state['step'],0)
        self.assertTrue(self.c.state['blocker']['discussion_limit']);self.assertFalse(self.c.state.get('approval_document_id'))

    async def test_limit_reached_this_round_immediately_forces_last_objector_before_next_monitor_round(self):
        self.c.state['rules']=[{'id':'R1','text':'真实证据','criterion':'检查原文'}];self.c.save()
        async def replies(rid,agent,prompt,root,settings,phase,**kwargs):
            if agent=='supervisor':return json.dumps({'public_message':'请评审','control':{'checks_patch':[],'action':{
                'type':'ask','kind':'review','agents':['chatgpt','claude'],'parallel':True,'plan_ref':self.plan['ref'],'instruction':'核对方案'}}})
            if phase==FINAL_PHASE:return await self.answer(rid,agent,prompt,root,settings,phase,**kwargs)
            if agent=='claude':await asyncio.sleep(0.01)
            text='表态：反对 '+self.plan['ref']+'：实现方式需修改';self.store.message(rid,agent,text,phase=phase);return text
        self.work.reply.side_effect=replies
        await self.c.supervisor_loop({'type':'user_input'})
        self.assertEqual(self.c.state['step'],1);self.assertEqual(self.c.state['discussion_rounds']['P2'],1)
        calls=[(a.args[1],a.args[5]) for a in self.work.reply.await_args_list]
        self.assertEqual(sum(a=='supervisor' for a,_ in calls),1)
        self.assertEqual(calls[-1],('claude',FINAL_PHASE));self.assertEqual(self.store.room(self.rid)['status'],'awaiting_approval')

    async def test_no_explicit_objector_sends_existing_plan_without_inventing_opposition(self):
        self.vote('chatgpt',True);self.vote('claude',True);self.limit()
        self.assertTrue(await resolve(self.c,self.policy,{},self.connections,None))
        self.work.reply.assert_not_awaited();self.assertEqual(self.c.state['current_plan'],self.plan['ref'])
        self.assertEqual(self.store.room(self.rid)['status'],'awaiting_approval')

    async def test_previously_user_approved_plan_is_not_reopened_after_runtime_recovery(self):
        self.vote('claude');self.limit();self.c.state['awaiting_approval']=self.plan['ref']
        mid=self.store.message(self.rid,'you','同意');self.assertIsNotNone(accept_user_approval(self.c.state,self.store,self.rid,mid));self.c.save()
        self.assertFalse(await resolve(self.c,self.policy,{'type':'execution_result'},self.connections,None))
        self.work.reply.assert_not_awaited();self.assertEqual(self.c.state['current_plan'],self.plan['ref'])

    async def test_failed_final_reply_never_becomes_approval_and_manual_recheck_retries_same_person(self):
        self.vote('claude');self.limit();self.output='仅修改上面的第二点。'
        await self.c.supervisor_loop({'type':'user_input'})
        self.assertEqual(self.work.reply.await_count,1);self.assertTrue(self.c.state['blocker']['discussion_limit'])
        self.assertEqual(self.c.state['current_plan'],self.plan['ref']);self.assertFalse(self.c.state.get('approval_document_id'))
        self.output=FINAL;await self.c.run(RESUME_BUTTON)
        self.assertEqual(self.work.reply.await_count,2);self.assertEqual(self.work.reply.await_args.args[1],'claude')
        self.assertEqual(self.c.state['step'],0);self.assertEqual(self.store.room(self.rid)['status'],'awaiting_approval')

    async def test_restart_reuses_completed_original_and_recovers_missing_submission_without_new_calls(self):
        objection=self.vote('claude');self.limit();boundary=self.store.boundary(self.rid)
        self.c.state['discussion_resolution']=dict(source_ref=self.plan['ref'],agent='claude',status='preparing',boundary=boundary,objection_message_id=objection)
        self.store.message(self.rid,'claude',FINAL,phase=FINAL_PHASE);self.c.save()
        reopened=Coordinator(self.work,self.rid,self.root,self.settings)
        await resolve(reopened,self.policy,{},self.connections,None)
        self.work.reply.assert_not_awaited();ref=reopened.state['current_plan']
        reopened.state['awaiting_approval']=None;reopened.state.pop('approval_document_id');reopened.save()
        await resolve(reopened,self.policy,{},self.connections,None)
        self.work.reply.assert_not_awaited();self.assertEqual(reopened.state['awaiting_approval'],ref)
        self.assertFalse(any(a['kind']=='user' for a in self.store.approvals(self.rid)))

    async def test_user_rejection_goes_back_to_last_objector_for_new_version_without_reopening_debate(self):
        self.vote('claude');self.limit();await resolve(self.c,self.policy,{},self.connections,None)
        self.output=FINAL.replace('实现要求','按新要求实现')
        self.store.message(self.rid,'you','不同意，请调整实现方式')
        spy=AsyncMock(wraps=self.c.ask);self.c.ask=spy
        await self.c.run('不同意，请调整实现方式')
        self.assertEqual(self.work.reply.await_count,2);self.assertEqual(self.c.state['current_plan'],'P2@3')
        self.assertIn('不同意，请调整实现方式',spy.await_args.args[0]['instruction'])
        self.assertEqual(self.store.room(self.rid)['status'],'awaiting_approval')
