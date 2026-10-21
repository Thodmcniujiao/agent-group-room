"""Observable CLI sign-in lifecycle; browser authorization stays with the CLI."""
import asyncio
import time

from cli_runtime import find_cli
from engines import HIDDEN,auth_status,environment,kill_tree


class Logins:
    def __init__(self,on_success):
        self.on_success=on_success
        self.states={}
        self.tasks={}
        self.lock=asyncio.Lock()

    def snapshot(self):
        return {provider:dict(state) for provider,state in self.states.items()}

    def state(self,provider,status,message):
        value={'status':status,'message':message,'updated':time.time()}
        self.states[provider]=value
        return dict(value)

    async def start(self,provider):
        if provider not in ['chatgpt','claude']:raise ValueError('请选择有效提供方。')
        label='ChatGPT' if provider=='chatgpt' else 'Claude'
        async with self.lock:
            if provider in self.tasks and not self.tasks[provider].done():
                return dict(self.states[provider])
            binary=find_cli('codex' if provider=='chatgpt' else 'claude')
            if not binary:
                message=f'未找到 {label} CLI，无法启动登录。请安装对应 CLI 后重试。'
                self.state(provider,'error',message)
                raise RuntimeError(message)
            args=[binary,'login'] if provider=='chatgpt' else [binary,'auth','login','--claudeai']
            try:
                proc=await asyncio.create_subprocess_exec(*args,stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
                    creationflags=HIDDEN,env=environment())
            except OSError:
                message=f'无法启动 {label} 登录程序，请检查 CLI 安装与运行权限。'
                self.state(provider,'error',message)
                raise RuntimeError(message) from None
            result=self.state(provider,'waiting',f'已启动 {label} 登录。请在系统浏览器完成授权，状态将自动更新。')
            self.tasks[provider]=asyncio.create_task(self.finish(provider,label,proc))
            return result

    async def finish(self,provider,label,proc):
        try:
            # OAuth output can contain temporary secrets. Never send it to the UI or logs.
            await asyncio.wait_for(proc.communicate(),300)
            if proc.returncode:
                self.state(provider,'error',f'{label} 登录失败（退出码 {proc.returncode}）。请检查网络或 CLI 后重试。')
                return
            status=await auth_status()
            if not status.get(provider,{}).get('ready'):
                self.state(provider,'error',f'{label} 授权尚未完成，请重新点击登录。')
                return
            self.state(provider,'complete',f'{label} 登录成功，正在更新模型目录与账户用量。')
            self.on_success()
        except asyncio.TimeoutError:
            await kill_tree(proc)
            self.state(provider,'error',f'{label} 登录已超时，请重新点击登录。')
        except asyncio.CancelledError:
            await kill_tree(proc)
            raise
        except Exception:
            await kill_tree(proc)
            self.state(provider,'error',f'{label} 登录未完成，请检查 CLI 与网络后重试。')

    async def close(self):
        pending=[task for task in self.tasks.values() if not task.done()]
        for task in pending:task.cancel()
        if pending:await asyncio.gather(*pending,return_exceptions=True)
