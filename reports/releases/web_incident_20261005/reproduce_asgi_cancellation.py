"""Bounded subprocess reproduction; no network, database, or app imports."""

import json
import pathlib
import subprocess
import sys
import time

CHILD = r"""
import asyncio,time,faulthandler,sys
import django,asgiref,daphne
from django.conf import settings
from django.http import HttpResponse
from django.urls import path
from django.core.handlers.asgi import ASGIHandler
print('VERSIONS',django.get_version(),asgiref.__version__,daphne.__version__,flush=True)
faulthandler.dump_traceback_later(.8,file=sys.stdout)
class SlowSyncMiddleware:
    sync_capable=True
    async_capable=False
    def __init__(self,get_response): self.get_response=get_response
    def __call__(self,request):
        print('SYNC_MIDDLEWARE_STARTED',flush=True)
        time.sleep(.2)
        print('SYNC_MIDDLEWARE_CALLING_ASYNC_GET_RESPONSE',flush=True)
        return self.get_response(request)
def view(request): return HttpResponse('ok')
urlpatterns=[path('',view)]
settings.configure(DEBUG=False,SECRET_KEY='test',ROOT_URLCONF=__name__,ALLOWED_HOSTS=['testserver'],MIDDLEWARE=[__name__+'.SlowSyncMiddleware'])
django.setup()
async def heartbeat():
    while True:
        await asyncio.sleep(.02)
async def main():
    asyncio.create_task(heartbeat())
    app=ASGIHandler()
    queue=asyncio.Queue()
    await queue.put({'type':'http.request','body':b'','more_body':False})
    async def receive(): return await queue.get()
    async def send(message): print('SEND',message['type'],flush=True)
    scope={'type':'http','asgi':{'version':'3.0'},'method':'GET','path':'/','raw_path':b'/','query_string':b'','headers':[(b'host',b'testserver')],'server':('testserver',80),'client':('127.0.0.1',1),'scheme':'http','http_version':'1.1'}
    async def probe():
        await asyncio.sleep(.35)
        q=asyncio.Queue()
        await q.put({'type':'http.request','body':b'','more_body':False})
        async def probe_receive(): return await q.get()
        async def probe_send(message):
            if message['type']=='http.response.start':
                print('UNRELATED_REQUEST_STATUS',message['status'],flush=True)
        await app(scope,probe_receive,probe_send)
        print('UNRELATED_REQUEST_FINISHED',flush=True)
    probe_task = asyncio.create_task(probe())
    task=asyncio.create_task(app(scope,receive,send))
    await asyncio.sleep(.1)
    print('EXTERNAL_ASGI_CANCEL',flush=True)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        print("CANCELLED_REQUEST_HANDLED_SERVER_LOOP_CONTINUES",flush=True)
    await probe_task
    await asyncio.sleep(.1)
asyncio.run(main())
"""
if __name__ == "__main__":
    child_code = (
        CHILD
        if "--cancel" in sys.argv
        else CHILD.replace("    task.cancel()", '    print("NO_CANCEL_BASELINE",flush=True)')
    )
    p = subprocess.Popen([sys.executable, "-c", child_code], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        time.sleep(1.3)
        wchans = (
            {d.name: (d / "wchan").read_text().strip() for d in pathlib.Path(f"/proc/{p.pid}/task").iterdir()}
            if p.poll() is None
            else {}
        )
        print(json.dumps({"still_running": p.poll() is None, "thread_wchans": wchans}))
    finally:
        if p.poll() is None:
            p.kill()
        out, err = p.communicate()
        print(out.decode())
        print(err.decode())
