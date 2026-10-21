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


if __name__=='__main__':unittest.main()
