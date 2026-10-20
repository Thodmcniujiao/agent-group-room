"""Behavioral checks for the local integration; no external model calls."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'plugins/tiered-dev/scripts'
sys.path.insert(0, str(SCRIPTS))
from policy import validate_contract
import review_server

VALID = '# T-01\n## 目标\nImplement X.\n## 允许修改\na.py only.\n## 验收命令\npython -m unittest\n## 回传格式\nSTATUS and TESTS.'


class PolicyTests(unittest.TestCase):
    def test_snapshot_contains_actual_diff_and_rejects_path_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'repo'
            root.mkdir()
            outside = Path(folder)/'outside.txt'
            outside.write_text('must not be included', encoding='utf-8')
            def git(*args):
                subprocess.run(['git', *args], cwd=root, check=True, capture_output=True)
            git('init')
            (root/'sample.txt').write_text('before\n', encoding='utf-8')
            git('add', 'sample.txt')
            git('-c', 'user.name=Tiered Test', '-c', 'user.email=tiered@example.invalid', 'commit', '-m', 'fixture')
            (root/'sample.txt').write_text('after\n', encoding='utf-8')
            (root/'new.txt').write_text('untracked data', encoding='utf-8')
            snapshot, evidence, truncated = review_server.build_snapshot(root, ['sample.txt'])
            self.assertIn('-before', snapshot)
            self.assertIn('+after', snapshot)
            self.assertIn('new.txt', snapshot)
            self.assertNotIn('untracked data', snapshot)
            self.assertFalse(truncated)
            with self.assertRaises(ValueError):
                review_server.build_snapshot(root, ['../outside.txt'])

    def test_incomplete_or_empty_contract_is_rejected(self):
        for invalid in ['fix it', VALID.replace('Implement X.', ''), VALID.replace('## 验收命令', '## Something else')]:
            with self.assertRaises(ValueError):
                validate_contract(invalid)
        validate_contract(VALID)

    def test_hook_blocks_destructive_command_only_in_tiered_workspace(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            payload = {'cwd': folder, 'tool_name': 'Bash', 'tool_input': {'command': 'git reset --hard'}}
            def invoke():
                return subprocess.run([sys.executable, str(SCRIPTS/'hook.py'), 'guard'],
                    input=json.dumps(payload), text=True, capture_output=True)
            self.assertEqual(invoke().returncode, 0)
            (root/'.tiered').mkdir()
            self.assertEqual(invoke().returncode, 2)
            payload['tool_input']['command'] = 'git diff --stat'
            self.assertEqual(invoke().returncode, 0)

    def test_contract_hook_validates_without_calling_a_model(self):
        for task, code in [('fix it', 2), (VALID, 0)]:
            result = subprocess.run([sys.executable, str(SCRIPTS/'hook.py'), 'contract'],
                input=json.dumps({'tool_input': {'task': task}}), text=True, capture_output=True)
            self.assertEqual(result.returncode, code)


class ReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_astra_has_fixed_readonly_flags_and_stdin_prompt(self):
        seen = {}
        class Process:
            returncode = 0
            async def communicate(self, data):
                seen['stdin'] = data
                return None, None
        async def launch(*args, **kwargs):
            seen['args'] = args
            seen['env'] = kwargs['env']
            Path(args[args.index('--output-last-message')+1]).write_text('VERDICT: PASS', encoding='utf-8')
            return Process()
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(review_server.asyncio, 'create_subprocess_exec', launch), \
                 patch.object(review_server.shutil, 'which', return_value='codex.exe'), \
                 patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'sk-test-secret'}):
                response = json.loads(await review_server.run_review('Read plan. $(untrusted)', directory, 'astra'))
        self.assertEqual(response['status'], 'DONE')
        self.assertEqual(seen['args'][seen['args'].index('--sandbox')+1], 'read-only')
        self.assertEqual(seen['args'][seen['args'].index('--model')+1], review_server.load_preferences()['models']['chatgpt'])
        self.assertIn('--ignore-user-config', seen['args'])
        self.assertNotIn('sk-test-secret', str(seen))
        self.assertNotIn('Read plan. $(untrusted)', seen['args'])
        self.assertIn(b'$(untrusted)', seen['stdin'])

    async def test_runtime_or_home_cannot_be_review_project(self):
        for folder in [Path.home(), Path.home()/'.codex', Path.home()/'.local/share/tiered-dev']:
            with self.assertRaises(ValueError):
                review_server.project_path(str(folder))


if __name__ == '__main__':
    unittest.main()
