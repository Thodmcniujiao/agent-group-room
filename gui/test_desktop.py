"""Desktop startup and lifecycle checks. No inference or real user state."""
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

import desktop
import launch


class LaunchTests(unittest.TestCase):
    def test_existing_backend_is_reused_without_browser_or_process_spawn(self):
        with tempfile.TemporaryDirectory() as folder:
            state=Path(folder)
            (state/'service.json').write_text(json.dumps({'port':49318}))
            with patch.object(launch,'STATE',state),patch.object(launch,'healthy',return_value=True),\
                 patch.object(launch.subprocess,'Popen') as spawn,patch.object(launch.webbrowser,'open') as browser:
                self.assertEqual(launch.ensure_service(),'http://127.0.0.1:49318')
                spawn.assert_not_called();browser.assert_not_called()

    def test_default_entrypoint_uses_desktop_and_browser_is_explicit(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(launch,'STATE',Path(folder)),\
             patch.object(launch,'ensure_service',return_value='http://127.0.0.1:49315') as backend,\
             patch.object(desktop,'run') as run,patch.object(launch.webbrowser,'open') as browser:
            with patch.object(sys,'argv',['launch.py']):launch.main()
            run.assert_called_once_with(backend,Path(folder));browser.assert_not_called()
            with patch.object(sys,'argv',['launch.py','--browser']):launch.main()
            browser.assert_called_once_with('http://127.0.0.1:49315')


class DesktopTests(unittest.TestCase):
    def test_remote_file_and_script_navigation_cannot_replace_local_app(self):
        origin='http://127.0.0.1:49315'
        self.assertTrue(desktop.navigation_allowed(origin+'/',origin))
        self.assertTrue(desktop.navigation_allowed(origin+'/api/rooms/abc/export',origin))
        for url in ['https://example.com','file:///C:/secret','javascript:alert(1)',
                    origin+'.evil/','http://user@127.0.0.1:49315/','http://127.0.0.1:49316/']:
            self.assertFalse(desktop.navigation_allowed(url,origin),url)

    def test_size_restore_handles_corrupt_state_and_smaller_monitor(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'window.json';screens=[SimpleNamespace(width=1280,height=800)]
            for content in ['invalid','[]','null','{"width":"bad","height":false}']:
                path.write_text(content)
                self.assertEqual(desktop.window_options(path,screens)['width'],1240)
            path.write_text('{"width":5000,"height":3,"maximized":true}')
            options=desktop.window_options(path,screens)
            self.assertEqual((options['width'],options['height']),(1240,720))
            self.assertTrue(options['maximized'])

    @unittest.skipUnless(sys.platform=='win32','Windows single-instance contract')
    def test_repeated_launch_signals_primary_and_releasing_allows_reopen(self):
        with tempfile.TemporaryDirectory() as folder:
            first=desktop.DesktopInstance(Path(folder))
            second=desktop.DesktopInstance(Path(folder))
            try:
                self.assertTrue(first.primary);self.assertFalse(second.primary)
                second.activate()
                self.assertEqual(first.events.WaitForSingleObject(first.activation,1000),0)
            finally:
                second.close();first.close()
            reopened=desktop.DesktopInstance(Path(folder))
            try:self.assertTrue(reopened.primary)
            finally:reopened.close()

    def test_secondary_launch_never_starts_backend_or_another_window(self):
        instance=Mock(primary=False);backend=Mock()
        with patch.object(desktop,'DesktopInstance',return_value=instance):
            desktop.run(backend,Path('unused'))
        backend.assert_not_called();instance.activate.assert_called_once();instance.close.assert_called_once()

    def test_native_close_persists_size_without_stopping_backend(self):
        class Event:
            def __init__(self):self.handlers=[]
            def __iadd__(self,handler):self.handlers.append(handler);return self
            def fire(self,*args):
                for handler in self.handlers:handler(*args)
        events=SimpleNamespace(**{name:Event() for name in
            ['before_show','maximized','restored','resized','closing','shown']})
        native=SimpleNamespace(webview=SimpleNamespace(NavigationStarting=Event()))
        window=SimpleNamespace(events=events,native=native)
        webview=SimpleNamespace(settings={},screens=[SimpleNamespace(width=1920,height=1200)],
            create_window=Mock(return_value=window))
        instance=Mock(primary=True);backend=Mock(return_value='http://127.0.0.1:49315')
        def start(**kwargs):
            self.assertEqual(kwargs['gui'],'edgechromium');self.assertFalse(kwargs['debug'])
            events.before_show.fire();events.resized.fire(1250,850)
            blocked=SimpleNamespace(Uri='file:///C:/private',Cancel=False)
            native.webview.NavigationStarting.fire(None,blocked)
            self.assertTrue(blocked.Cancel)
            events.closing.fire()
        webview.start=start
        with tempfile.TemporaryDirectory() as folder,patch.object(desktop,'DesktopInstance',return_value=instance),\
             patch.dict(sys.modules,{'webview':webview}),patch.dict(desktop.os.environ,{'TIERED_DESKTOP_DEBUG_PORT':''}),\
             patch.object(desktop.ctypes,'windll',create=True):
            desktop.run(backend,Path(folder))
            saved=json.loads((Path(folder)/'desktop-window.json').read_text())
            self.assertEqual(saved,{'width':1250,'height':850,'maximized':False})
            self.assertFalse(webview.settings['ALLOW_FILE_URLS'])
            self.assertNotIn('js_api',webview.create_window.call_args.kwargs)
            backend.assert_called_once();instance.close.assert_called_once()


if __name__=='__main__':unittest.main()
