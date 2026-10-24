"""Room isolation, one-time migration and saved-agent independence."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engines import preferences
from store import Store
from room_settings import RoomSettings


class RoomSettingsTests(unittest.TestCase):
    def test_workflow_and_handoff_controls_have_room_local_defaults_and_validation(self):
        self.rooms.migrate(self.defaults);a=self.rooms.load(self.a)
        self.assertEqual((a['supervisor_context'],a['resume_words'],a['discussion_tolerance']),('state',['继续'],3))
        self.assertEqual((a['context']['maintain_at'],a['context']['handoff_recent']),(0.6,8))
        updated=self.rooms.update(self.a,{'supervisor_context':'full','resume_words':['继续','已修复'],'discussion_tolerance':4,
            'context':{**a['context'],'maintain_at':0.75,'handoff_recent':4}})
        self.assertEqual(updated['supervisor_context'],'full');self.assertEqual(self.rooms.load(self.b)['supervisor_context'],'state')
        before=self.store.configuration(self.a)
        for edit in [{'discussion_tolerance':0},{'resume_words':['']},{'supervisor_context':'summary'},
                     {'context':{**a['context'],'maintain_at':0.2}},{'context':{**a['context'],'handoff_recent':51}}]:
            with self.assertRaises(ValueError):self.rooms.update(self.a,edit)
            self.assertEqual(self.store.configuration(self.a),before)

    def setUp(self):
        self.home=tempfile.TemporaryDirectory();self.path=Path(self.home.name)
        self.store=Store(self.path);self.rooms=RoomSettings(self.store)
        self.pref_patch=patch.object(preferences,'PREFS',self.path/'missing-preferences.json');self.pref_patch.start()
        self.defaults=preferences.load()
        self.a=self.store.create('A','C:/fixture-a')['id'];self.b=self.store.create('B','C:/fixture-b')['id']

    def tearDown(self):
        self.pref_patch.stop();self.store.db.close();self.home.cleanup()

    def test_legacy_migration_is_once_and_new_rooms_start_without_invitees(self):
        self.rooms.migrate(self.defaults)
        original=self.rooms.load(self.a,self.defaults)['team']
        self.assertEqual(original,self.defaults['team'])
        changed=copy.deepcopy(self.defaults);changed['team']=[]
        self.rooms.migrate(changed)
        self.assertEqual(self.rooms.load(self.a,changed)['team'],original)
        new=self.store.create('New','C:/new')['id'];self.rooms.seed(new,self.defaults)
        self.assertEqual(self.rooms.load(new,self.defaults)['members'],[])

    def test_members_models_supervisor_and_pause_are_independent_between_rooms(self):
        for rid in [self.a,self.b]:self.rooms.seed(rid,self.defaults)
        self.rooms.membership(self.a,'chatgpt',True)
        self.rooms.membership(self.b,'claude',True)
        self.rooms.member(self.a,'chatgpt',{'model':'room-a-model','effort':'low'})
        self.rooms.update(self.a,{'supervisor':{'provider':'claude','model':'monitor-a'},'pause_before_execution':False})
        a=self.rooms.load(self.a);b=self.rooms.load(self.b)
        self.assertEqual([m['id'] for m in a['members']],['chatgpt'])
        self.assertEqual(a['members'][0]['model'],'room-a-model')
        self.assertEqual([m['id'] for m in b['members']],['claude'])
        self.assertEqual(b['supervisor'],self.defaults['supervisor'])
        self.assertEqual(b['pause_before_execution'],self.defaults['pause_before_execution'])
        self.assertFalse(preferences.PREFS.exists())

    def test_removal_preserves_room_choice_library_and_other_room(self):
        self.rooms.migrate(self.defaults)
        self.rooms.member(self.a,'chatgpt',{'model':'remember-me'})
        self.rooms.membership(self.a,'chatgpt',False)
        self.assertNotIn('chatgpt',[m['id'] for m in self.rooms.load(self.a)['members']])
        self.assertIn('chatgpt',[m['id'] for m in self.rooms.load(self.b)['members']])
        returned=self.rooms.membership(self.a,'chatgpt',True)
        self.assertEqual(next(m['model'] for m in returned['members'] if m['id']=='chatgpt'),'remember-me')
        self.assertEqual(returned['agents'],self.defaults['agents'])

    def test_bad_edits_leave_existing_configuration_intact(self):
        self.rooms.migrate(self.defaults);before=self.store.configuration(self.a)
        bad=[{'supervisor':[]},{'supervisor':{'provider':'unknown'}},{'supervisor':{'max_steps':True}},
             {'pause_before_execution':'yes'},{'agents':[]},{'team':[{'agent_id':'unknown'}]}]
        for body in bad:
            with self.assertRaises(ValueError):self.rooms.update(self.a,body)
            self.assertEqual(self.store.configuration(self.a),before)
        with self.assertRaises(ValueError):self.rooms.membership(self.a,'chatgpt','false')
        with self.assertRaises(ValueError):self.rooms.member(self.a,'chatgpt',{'base_url':'https://wrong.example'})

    def test_reopening_database_retains_members_and_model(self):
        self.rooms.seed(self.a,self.defaults);self.rooms.membership(self.a,'claude',True)
        self.rooms.member(self.a,'claude',{'model':'persisted-model'})
        other=Store(self.path)
        try:self.assertEqual(RoomSettings(other).load(self.a)['members'][0]['model'],'persisted-model')
        finally:other.db.close()

    def test_repeat_invitations_have_independent_names_models_and_membership(self):
        self.rooms.seed(self.a,self.defaults)
        first=self.rooms.add(self.a,'chatgpt')['members'][0]
        second=self.rooms.add(self.a,'chatgpt')['members'][1]
        self.assertNotEqual(first['id'],second['id'])
        self.assertEqual((first['agent_id'],second['agent_id']),('chatgpt','chatgpt'))
        self.assertNotEqual(first['name'],second['name'])
        value=self.rooms.member(self.a,first['id'],{'name':'  方案设计  ','model':'design-model'})
        self.assertEqual((value['members'][0]['name'],value['members'][0]['model']),('方案设计','design-model'))
        self.assertEqual(value['members'][1],second)
        self.assertEqual(value['agents'],self.defaults['agents'])
        self.assertFalse(preferences.PREFS.exists())
        before=self.store.configuration(self.a)
        for name in ['', ' '*4, 'x'*41, None]:
            with self.assertRaises(ValueError):self.rooms.member(self.a,first['id'],{'name':name})
            self.assertEqual(self.store.configuration(self.a),before)
        remaining=self.rooms.membership(self.a,first['id'],False)
        self.assertEqual(remaining['members'],[second])
        other=Store(self.path)
        try:
            restored=RoomSettings(other).membership(self.a,first['id'],True)
            self.assertEqual(restored['members'][0]['name'],'方案设计')
        finally:other.db.close()

    def test_instance_executor_selection_and_atomic_limits(self):
        self.rooms.seed(self.a,self.defaults)
        first=self.rooms.add(self.a,'deepseek')['members'][0]['id']
        second=self.rooms.add(self.a,'deepseek')['members'][1]['id']
        self.assertEqual(self.rooms.load(self.a)['executor'],first)
        self.rooms.update(self.a,{'executor':second})
        self.rooms.membership(self.a,first,False)
        self.assertEqual(self.rooms.load(self.a)['executor'],second)
        for _ in range(19):self.rooms.add(self.a,'chatgpt')
        before=self.store.configuration(self.a)
        with self.assertRaises(ValueError):self.rooms.add(self.a,'chatgpt')
        self.assertEqual(self.store.configuration(self.a),before)
        with self.assertRaises(ValueError):self.rooms.update(self.a,{'team':[before['team'][-1]]*2})
        self.assertEqual(self.store.configuration(self.a),before)

    def test_instance_sessions_are_separate_while_credentials_and_account_pool_are_shared(self):
        from context import ContextTurn
        from model_catalog import connection_context
        from account_usage import team_quota
        self.rooms.seed(self.a,self.defaults)
        self.rooms.add(self.a,'chatgpt');settings=self.rooms.add(self.a,'chatgpt')
        a,b=settings['members']
        with patch('model_catalog.connection_context',return_value=('shared-account','')):
            ta=ContextTurn(self.store,self.a,a['id'],a,a['model'],settings,'test')
            tb=ContextTurn(self.store,self.a,b['id'],b,b['model'],settings,'test')
        self.assertNotEqual(ta.key,tb.key)
        self.assertEqual(connection_context(a),connection_context(b))
        quota=team_quota(settings,{'agents':{}})
        peers=[q for q in quota['participants'] if q['id'] in [a['id'],b['id']]]
        self.assertEqual(len(peers),2)
        self.assertIn(b['id'],peers[0]['shared_with'])
        self.assertIn(a['id'],peers[1]['shared_with'])
        (self.path/'credentials').mkdir()
        (self.path/'credentials/chatgpt.dpapi').write_bytes(b'fixture-encrypted')
        with patch.object(preferences,'STATE',self.path),patch('win32crypt.CryptUnprotectData',return_value=(None,b'fixture-key')) as decrypt:
            self.assertEqual(preferences.get_member_key(a),'fixture-key')
            self.assertEqual(preferences.get_member_key(b),'fixture-key')
            self.assertEqual(decrypt.call_count,2)


if __name__=='__main__':unittest.main()
