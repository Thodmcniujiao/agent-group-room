"""Run an approved Windows command without CRT re-escaping its cmd quotes."""
import asyncio
import os
import subprocess


async def run(command,root,env,timeout=600,*,capture_full=False):
    shell=os.environ.get('COMSPEC','cmd.exe')
    # Popen accepts a raw Windows command line. Passing command as an argv item
    # would apply list2cmdline's \" escaping, which cmd.exe does not understand.
    line='"'+shell+'" /d /s /c "'+command+'"'
    proc=subprocess.Popen(line,executable=shell,cwd=root,env=env,stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,creationflags=0x08000000)
    captured=bytearray();size=0
    async def consume():
        nonlocal size
        while chunk:=await asyncio.to_thread(proc.stdout.read1,4096):
            size+=len(chunk);captured.extend(chunk[:max(0,(8_000_000 if capture_full else 30000)-len(captured))])
            if size>8_000_000:raise RuntimeError('测试输出超过上限，已停止。')
        return await asyncio.to_thread(proc.wait)
    try:code=await asyncio.wait_for(consume(),timeout)
    except BaseException:
        if proc.poll() is None:
            killer=await asyncio.create_subprocess_exec('taskkill','/PID',str(proc.pid),'/T','/F',
                stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,creationflags=0x08000000)
            await killer.wait();await asyncio.to_thread(proc.wait)
        raise
    finally:proc.stdout.close()
    return code,captured.decode('utf-8',errors='replace')
