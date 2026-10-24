"""Approval packets, orphan locks, real-owner queueing and exact button provenance."""
import asyncio
import copy
import json
import unittest
from unittest.mock import patch,AsyncMock

import httpx
import test_token_optimization as fixtures
from approval_documents import create,ensure_pending,render,markdown,info,present_message
from room_settings import RoomSettings
from workflows import Workflows


class ApprovalDocumentTests(fixtures.Fixture):
    def pending(self):
        plan=self.plan();self.wf.update(awaiting_approval=plan['ref'],status='awaiting_approval')
        self.store.save_workflow(self.rid,self.wf,0)
        return ensure_pending(self.store,self.rid,self.settings)

    def test_packet_is_host_generated_and_preserves_exact_version_and_original(self):
        doc=self.pending();self.assertEqual(doc['data']['original'],'方案原文 UNIQUE-PLAN-BODY')
        self.assertEqual(doc['plan_sha256'],self.store.plan(self.rid,'P2@1')['sha256'])
        self.assertIn(doc['data']['original'],markdown(doc));self.assertIn('方案审批书',render(doc))
        again=ensure_pending(self.store,self.rid,self.settings);self.assertEqual(again,doc)
        self.assertEqual(self.store.db.execute('select count(*) from approval_documents').fetchone()[0],1)

    def test_html_never_executes_member_html_or_fetches_remote_images(self):
        doc=self.pending();doc=copy.deepcopy(doc)
        doc['data']['original']='<script>alert(1)</script>\n\n![image](https://example.invalid/tracker)\n\n[bad](javascript:alert(1))'
        html=render(doc)
        self.assertNotIn('<script>',html);self.assertNotIn('<img',html);self.assertNotIn('href="javascript:',html)
        self.assertIn('&lt;script&gt;',html)

    def test_foreign_room_document_is_not_accessible_and_member_vote_is_not_user_approval(self):
        doc=self.pending();other=self.store.create('other',str(self.root))['id']
        with self.assertRaises(ValueError):self.store.approval_document(other,doc['id'])
        mid=self.store.message(self.rid,'claude','表态：同意 P2@1');self.store.add_approval(self.rid,'P2@1','claude','member',mid)
        state,rev=self.store.load_workflow(self.rid);state['awaiting_approval']=None;self.store.save_workflow(self.rid,state,rev)
        self.assertEqual(info(self.store,self.rid,doc)['status'],'superseded')


class ApprovalRuntimeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.RuntimeTests.asyncSetUp
    asyncTearDown=fixtures.RuntimeTests.asyncTearDown
    coordinator=fixtures.RuntimeTests.coordinator

    def pending(self):
        plan=self.fixture.plan();self.wf=self.fixture.wf;self.wf.update(awaiting_approval=plan['ref'],status='awaiting_approval')
        self.store.save_workflow(self.rid,self.wf,0)
        doc=ensure_pending(self.store,self.rid,self.settings)
        return doc,{'kind':'approve','plan_ref':doc['plan_ref'],'document_id':doc['id'],'document_sha256':doc['document_sha256']}

    async def test_orphan_project_lock_no_longer_blocks_approval(self):
        doc,action=self.pending();work=Workflows(self.store)
        work.project_busy[str(self.root).casefold()]=self.rid
        work.supervised=AsyncMock()
        await work.start(self.rid,'','workflow','all',self.settings,source='button',action=action)
        work.supervised.assert_awaited_once();self.assertEqual(work.project_busy,{})
        self.assertEqual(len(self.store.approvals(self.rid)),1)
        self.assertEqual(self.store.approvals(self.rid)[0]['source'],'button')

    async def test_real_other_owner_keeps_lock_approval_records_now_and_execution_waits(self):
        doc,action=self.pending();work=Workflows(self.store)
        other=self.store.create('real owner',str(self.root))['id'];ready=asyncio.Event()
        owner=asyncio.create_task(ready.wait());work.running[other]=owner;work.project_busy[str(self.root).casefold()]=other
        work.supervised=AsyncMock()
        task=work.start(self.rid,'','workflow','all',self.settings,source='button',action=action)
        await asyncio.sleep(0)
        self.assertEqual(self.store.room(self.rid)['status'],'queued');self.assertEqual(len(self.store.approvals(self.rid)),1)
        self.assertEqual(work.project_busy[str(self.root).casefold()],other);work.supervised.assert_not_awaited()
        ready.set();await asyncio.wait_for(task,2)
        work.supervised.assert_awaited_once();self.assertEqual(work.project_busy,{})
        self.assertEqual(self.store.load_workflow(self.rid)[0]['status'],'active')

    async def test_cancelling_approval_queue_does_not_cancel_current_project_owner(self):
        _,action=self.pending();work=Workflows(self.store)
        other=self.store.create('real owner',str(self.root))['id'];ready=asyncio.Event();owner=asyncio.create_task(ready.wait())
        work.running[other]=owner;work.project_busy[str(self.root).casefold()]=other
        work.supervised=AsyncMock();work.start(self.rid,'','workflow','all',self.settings,source='button',action=action)
        await asyncio.sleep(0);await work.stop(self.rid)
        self.assertFalse(owner.done());self.assertEqual(work.project_busy[str(self.root).casefold()],other)
        self.assertNotIn(self.rid,work.running);work.supervised.assert_not_awaited()
        self.assertEqual(self.store.load_workflow(self.rid)[0]['status'],'stopped')
        ready.set();await owner

    async def test_stop_before_task_begins_also_releases_project_reservation(self):
        work=Workflows(self.store);work.supervised=AsyncMock()
        work.start(self.rid,'fixture','workflow','all',self.settings)
        await work.stop(self.rid)
        self.assertEqual(work.running,{});self.assertEqual(work.project_busy,{})

    async def test_legacy_pending_message_is_a_document_card_and_document_routes_are_scoped(self):
        import server
        doc,action=self.pending()
        mid=self.store.message(self.rid,'system','OLD-DUPLICATE-PLAN-BODY',phase='确认方案',actions=[{'id':'approve','plan_ref':doc['plan_ref']}])
        work=Workflows(self.store);work.supervised=AsyncMock()
        with patch.object(server,'STORE',self.store),patch.object(server,'WORK',work),patch.object(server,'ROOMS',RoomSettings(self.store)):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url=server.ORIGIN) as client:
                data=(await client.get('/api/rooms/'+self.rid)).json();message=next(m for m in data['messages'] if m['id']==mid)
                self.assertNotIn('OLD-DUPLICATE',message['text']);self.assertEqual(message['actions'][0]['id'],'review_plan')
                self.assertEqual(self.store.message_by_id(mid)['text'],'OLD-DUPLICATE-PLAN-BODY')
                archived_event=next(e for e in self.store.events(self.rid,0) if e.get('type')=='message' and e['message']['id']==mid)
                self.assertEqual(present_message(self.store,self.rid,archived_event['message'])['actions'][0]['id'],'review_plan')
                url=data['approval_document']['url'];html=await client.get(url);download=await client.get(url+'?format=md')
                self.assertEqual(html.status_code,200);self.assertIn('UNIQUE-PLAN-BODY',html.text)
                self.assertIn("frame-ancestors 'self'",html.headers['Content-Security-Policy'])
                self.assertIn('attachment',download.headers['Content-Disposition']);self.assertIn('UNIQUE-PLAN-BODY',download.text)
                other=self.store.create('other',str(self.root))['id']
                self.assertEqual((await client.get(url.replace(self.rid,other))).status_code,400)
                headers={'X-Tiered-Token':server.TOKEN};path='/api/rooms/'+self.rid+'/approve'
                self.assertEqual((await client.post(path,json=action)).status_code,403)
                bad={**action,'document_sha256':'tampered'}
                self.assertEqual((await client.post(path,json=bad,headers=headers)).status_code,400)
                self.assertEqual(self.store.approvals(self.rid),[])
                self.assertEqual((await client.post(path,json=action,headers=headers)).status_code,200)
                task=work.running.get(self.rid)
                if task:await task
                work.supervised.assert_awaited_once()
                self.assertEqual((await client.post(path,json=action,headers=headers)).status_code,400)


if __name__=='__main__':unittest.main()
