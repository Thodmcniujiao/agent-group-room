"""Desktop PATH discovery and visible sign-in outcomes without real OAuth requests."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock,Mock,patch

import cli_runtime
import logins


class CliDiscoveryTests(unittest.TestCase):
    def test_desktop_without_codex_on_path_finds_current_bundled_binary(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);local=root/'local';roaming=root/'roaming';home=root/'home'
            first=local/'OpenAI/Codex/bin/older/codex.exe'
            latest=local/'OpenAI/Codex/bin/current/codex.exe'
            for path in [first,latest]:path.parent.mkdir(parents=True);path.write_bytes(b'fixture')
            os.utime(first,(100,100));os.utime(latest,(200,200))
            with patch.dict(os.environ,{'PATH':'','LOCALAPPDATA':str(local),'APPDATA':str(roaming)}),\
                 patch.object(cli_runtime,'registered_path',return_value=''),patch.object(cli_runtime.Path,'home',return_value=home):
                self.assertEqual(cli_runtime.find_cli('codex'),str(latest))

    def test_registered_install_path_is_used_when_inherited_path_is_stale(self):
        with patch.object(cli_runtime.shutil,'which',side_effect=[None,'C:/installed/codex.exe']) as which,\
             patch.object(cli_runtime,'registered_path',return_value='C:/installed'):
            self.assertEqual(cli_runtime.find_cli('codex'),'C:/installed/codex.exe')
            self.assertEqual(which.call_args.kwargs,{'path':'C:/installed'})

    def test_existing_path_selection_is_preserved(self):
        with patch.object(cli_runtime.shutil,'which',return_value='C:/chosen/claude.exe'),\
             patch.object(cli_runtime,'registered_path') as registry:
            self.assertEqual(cli_runtime.find_cli('claude'),'C:/chosen/claude.exe')
            registry.assert_not_called()


class LoginTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_cli_is_immediate_error_without_claiming_a_browser_opened(self):
        manager=logins.Logins(Mock())
        with patch.object(logins,'find_cli',return_value=None),\
             patch.object(logins.asyncio,'create_subprocess_exec',new_callable=AsyncMock) as launch:
            with self.assertRaisesRegex(RuntimeError,'未找到 ChatGPT CLI'):await manager.start('chatgpt')
            launch.assert_not_called()
        self.assertEqual(manager.snapshot()['chatgpt']['status'],'error')

    async def test_spawn_failure_is_returned_before_waiting_status(self):
        manager=logins.Logins(Mock())
        with patch.object(logins,'find_cli',return_value='C:/codex.exe'),\
             patch.object(logins.asyncio,'create_subprocess_exec',side_effect=OSError('private internal detail')):
            with self.assertRaisesRegex(RuntimeError,'无法启动'):await manager.start('chatgpt')
        self.assertNotIn('private internal detail',json.dumps(manager.snapshot()))
        self.assertFalse(manager.tasks)

    async def test_success_checks_auth_and_duplicate_click_does_not_launch_twice(self):
        release=asyncio.Event();success=Mock();manager=logins.Logins(success)
        async def communicate():await release.wait();return b'oauth-private',b''
        proc=Mock(returncode=0,communicate=communicate)
        with patch.object(logins,'find_cli',return_value='C:/codex.exe'),\
             patch.object(logins.asyncio,'create_subprocess_exec',new_callable=AsyncMock,return_value=proc) as launch,\
             patch.object(logins,'auth_status',new_callable=AsyncMock,return_value={'chatgpt':{'ready':True}}):
            self.assertEqual((await manager.start('chatgpt'))['status'],'waiting')
            await manager.start('chatgpt');launch.assert_awaited_once()
            self.assertEqual(launch.call_args.args,('C:/codex.exe','login'))
            release.set();await manager.tasks['chatgpt']
        self.assertEqual(manager.snapshot()['chatgpt']['status'],'complete')
        self.assertNotIn('oauth-private',json.dumps(manager.snapshot()))
        success.assert_called_once()

    async def test_exit_failure_is_visible_and_does_not_leak_oauth_output(self):
        success=Mock();manager=logins.Logins(success)
        proc=Mock(returncode=7,communicate=AsyncMock(return_value=(b'',b'https://secret?token=private')))
        with patch.object(logins,'find_cli',return_value='C:/codex.exe'),\
             patch.object(logins.asyncio,'create_subprocess_exec',new_callable=AsyncMock,return_value=proc):
            await manager.start('chatgpt');await manager.tasks['chatgpt']
        state=manager.snapshot()['chatgpt']
        self.assertEqual(state['status'],'error');self.assertIn('7',state['message'])
        self.assertNotIn('private',json.dumps(state));success.assert_not_called()

    async def test_exit_zero_without_authenticated_account_is_not_success(self):
        manager=logins.Logins(Mock());proc=Mock(returncode=0,communicate=AsyncMock(return_value=(b'',b'')))
        with patch.object(logins,'find_cli',return_value='C:/claude.exe'),\
             patch.object(logins.asyncio,'create_subprocess_exec',new_callable=AsyncMock,return_value=proc),\
             patch.object(logins,'auth_status',new_callable=AsyncMock,return_value={'claude':{'ready':False}}):
            await manager.start('claude');await manager.tasks['claude']
        self.assertEqual(manager.snapshot()['claude']['status'],'error')

    async def test_timeout_kills_process_and_surfaces_retry_message(self):
        manager=logins.Logins(Mock());proc=Mock(communicate=AsyncMock(side_effect=asyncio.TimeoutError))
        with patch.object(logins,'kill_tree',new_callable=AsyncMock) as kill:
            await manager.finish('chatgpt','ChatGPT',proc)
            kill.assert_awaited_once_with(proc)
        self.assertIn('超时',manager.snapshot()['chatgpt']['message'])

    async def test_shutdown_cancels_and_cleans_up_pending_authorization(self):
        manager=logins.Logins(Mock());started=asyncio.Event()
        async def communicate():started.set();await asyncio.Event().wait()
        proc=Mock(communicate=communicate)
        with patch.object(logins,'find_cli',return_value='C:/codex.exe'),\
             patch.object(logins.asyncio,'create_subprocess_exec',new_callable=AsyncMock,return_value=proc),\
             patch.object(logins,'kill_tree',new_callable=AsyncMock) as kill:
            await manager.start('chatgpt');await started.wait();await manager.close()
            self.assertTrue(manager.tasks['chatgpt'].cancelled());kill.assert_awaited_once_with(proc)


if __name__=='__main__':unittest.main()
