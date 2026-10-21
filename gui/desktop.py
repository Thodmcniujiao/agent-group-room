"""Windows desktop host. Backend, credentials and task lifetime stay independent."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import threading
from urllib.parse import urlsplit
import webbrowser

TITLE='代理协作室 · Agent Group Room'
APP_ID='AgentGroupRoom.Desktop'


def navigation_allowed(url,origin):
    """Only this backend may replace the application document."""
    try:
        target,local=urlsplit(url),urlsplit(origin)
        return (target.scheme,target.netloc)==(local.scheme,local.netloc) and not target.username
    except ValueError:
        return False


def window_options(path,screens):
    """Restore size within the current primary display, including after monitor changes."""
    screen=screens[0]
    width=max(800,int(screen.width)-40);height=max(600,int(screen.height)-80)
    options={'width':min(1440,width),'height':min(960,height),'maximized':False,
             'min_size':(min(1024,width),min(720,height))}
    try:
        saved=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(saved,dict):return options
        for key,limit in [('width',width),('height',height)]:
            if type(saved.get(key)) is int:
                options[key]=max(options['min_size'][0 if key=='width' else 1],min(saved[key],limit))
        options['maximized']=saved.get('maximized') is True
    except (OSError,ValueError,TypeError):
        pass
    return options


class DesktopInstance:
    """One window per Windows session; subsequent launches raise the existing one."""
    def __init__(self,state):
        import win32api
        import win32event
        self.api,self.events=win32api,win32event
        name='Local\\AgentGroupRoom-'+hashlib.sha256(str(state.resolve()).lower().encode()).hexdigest()[:20]
        self.activation=win32event.CreateEvent(None,False,False,name+'-activate')
        self.mutex=win32event.CreateMutex(None,False,name)
        self.primary=win32api.GetLastError()!=183
        self.stopped=threading.Event()

    def activate(self):
        self.events.SetEvent(self.activation)

    def watch(self,window):
        import win32con
        import win32gui
        while not self.stopped.is_set():
            if self.events.WaitForSingleObject(self.activation,400)!=0:continue
            if self.stopped.is_set():break
            try:
                handle=window.native.Handle.ToInt64()
                if win32gui.IsIconic(handle):win32gui.ShowWindow(handle,win32con.SW_RESTORE)
                win32gui.ShowWindow(handle,win32con.SW_SHOW)
                win32gui.SetForegroundWindow(handle)
            except Exception:
                # Windows may deny stealing focus; signal the taskbar in that case.
                try:win32gui.FlashWindow(handle,True)
                except Exception:pass

    def close(self):
        self.stopped.set()
        self.events.SetEvent(self.activation)
        self.api.CloseHandle(self.activation)
        self.api.CloseHandle(self.mutex)


def run(get_url,state):
    instance=DesktopInstance(state)
    worker=None
    try:
        if not instance.primary:
            instance.activate()
            return
        try:
            import webview
        except ImportError as exc:
            raise RuntimeError('桌面组件尚未安装，请在项目目录运行 Install.ps1。') from exc
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        origin=get_url()
        settings_path=state/'desktop-window.json'
        options=window_options(settings_path,webview.screens)
        webview.settings['ALLOW_FILE_URLS']=False
        webview.settings['ALLOW_DOWNLOADS']=True
        webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER']=True
        # Explicit opt-in for local QA only. Normal shortcut launches expose no CDP port.
        debug_port=os.environ.get('TIERED_DESKTOP_DEBUG_PORT','')
        if debug_port:
            if not debug_port.isdecimal() or not 1024<=int(debug_port)<=65535:
                raise RuntimeError('桌面调试端口无效。')
            webview.settings['REMOTE_DEBUGGING_PORT']=int(debug_port)
        window=webview.create_window(TITLE,origin,**options,text_select=True,zoomable=True,
            background_color='#f5f7fa')
        maximized=options['maximized']
        size={'width':options['width'],'height':options['height']}

        def on_maximized():
            nonlocal maximized
            maximized=True

        def on_restored():
            nonlocal maximized
            maximized=False

        def on_resized(width,height):
            if not maximized and width>=options['min_size'][0] and height>=options['min_size'][1]:
                size.update(width=width,height=height)

        def on_closing():
            temporary=settings_path.with_suffix('.new')
            try:
                temporary.write_text(json.dumps({**size,'maximized':maximized}),encoding='utf-8')
                temporary.replace(settings_path)
            except OSError:
                pass
            # The existing backend keeps running tasks and encrypted state intact.
            return True

        def on_navigation(sender,args):
            url=str(args.Uri)
            if navigation_allowed(url,origin):return
            args.Cancel=True
            if urlsplit(url).scheme=='https':
                threading.Thread(target=webbrowser.open,args=(url,),daemon=True).start()

        def before_show():
            # WebView2's native navigation event also covers redirects and same-window links.
            window.native.webview.NavigationStarting+=on_navigation

        window.events.before_show+=before_show
        window.events.maximized+=on_maximized
        window.events.restored+=on_restored
        window.events.resized+=on_resized
        window.events.closing+=on_closing

        def shown():
            nonlocal worker
            worker=threading.Thread(target=instance.watch,args=(window,),daemon=True)
            worker.start()

        window.events.shown+=shown
        webview.start(gui='edgechromium',debug=False,private_mode=False,
            storage_path=str(state/'desktop-webview'),icon=str(Path(__file__).with_name('icon.ico')))
    finally:
        instance.stopped.set()
        if worker is not None:worker.join(timeout=1)
        instance.close()
