"""Exercise real ASGI request cleanup with retained database wrappers.

An isolated process keeps ASGI startup changes out of the pytest process and
uses file-backed SQLite connections without touching an external database.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest


@pytest.mark.parametrize("max_age", ["60", "None"])
def test_asgi_releases_connections_after_bursts_errors_and_disconnects(tmp_path, max_age):
    script = textwrap.dedent(
        """
        import asyncio
        import sys
        from threading import Lock

        import pytest  # Select the project's hermetic settings.
        import django
        from asgiref.sync import sync_to_async
        from asgiref.testing import ApplicationCommunicator
        from django.db import connections
        from django.db.backends.signals import connection_created
        from django.http import HttpResponse
        from django.test import override_settings
        from django.urls import path

        django.setup()
        max_age = None if sys.argv[1] == 'None' else int(sys.argv[1])
        databases = {
            alias: {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': sys.argv[2] + '/' + alias + '.sqlite3',
                'CONN_MAX_AGE': max_age,
            }
            for alias in ('default', 'secondary')
        }
        wrappers = []
        lock = Lock()

        def record_connection(sender, connection, **kwargs):
            # Retain wrappers so garbage collection cannot hide a missing close.
            with lock:
                wrappers.append(connection)

        connection_created.connect(record_connection, weak=False)

        def query():
            for alias in databases:
                with connections[alias].cursor() as cursor:
                    cursor.execute('SELECT 1')
                    assert cursor.fetchone() == (1,)

        def success(request):
            query()
            return HttpResponse('ok')

        def failure(request):
            query()
            raise RuntimeError('intentional cleanup regression test')

        connected = asyncio.Event()

        async def disconnected(request):
            await sync_to_async(query)()
            connected.set()
            await asyncio.Event().wait()

        urlpatterns = [path('ok', success), path('error', failure), path('disconnect', disconnected)]

        async def request(application, url, expected_status):
            communicator = ApplicationCommunicator(application, {
                'type': 'http', 'asgi': {'version': '3.0'},
                'http_version': '1.1', 'method': 'GET', 'path': url,
                'raw_path': url.encode(), 'query_string': b'',
                'headers': [(b'host', b'testserver')],
            })
            await communicator.send_input({'type': 'http.request', 'body': b''})
            if url == '/disconnect':
                await asyncio.wait_for(connected.wait(), timeout=5)
                await communicator.send_input({'type': 'http.disconnect'})
            else:
                start = await communicator.receive_output(timeout=5)
                assert start['status'] == expected_status, start
                body = await communicator.receive_output(timeout=5)
                assert body['type'] == 'http.response.body'
            await communicator.wait(timeout=5)

        async def exercise(application):
            for _ in range(3):
                await asyncio.gather(*(request(application, '/ok', 200) for _ in range(12)))
                assert wrappers
                assert all(wrapper.connection is None for wrapper in wrappers), 'burst leaked connections'
            await request(application, '/error', 500)
            assert all(wrapper.connection is None for wrapper in wrappers), 'error leaked connections'
            await request(application, '/disconnect', None)
            assert all(wrapper.connection is None for wrapper in wrappers), 'disconnect leaked connections'
            assert len(wrappers) == 76, len(wrappers)

        with override_settings(
            DATABASES=databases, DEBUG=False, MIDDLEWARE=[],
            ROOT_URLCONF=__name__, ALLOWED_HOSTS=['testserver'],
        ):
            # DATABASES overrides don't invalidate the connection handler cache.
            for alias in list(connections):
                del connections[alias]
            connections.settings = connections.configure_settings(databases)
            # Initialize wrappers before ASGI startup to cover the cached case.
            for alias in databases:
                assert connections[alias].settings_dict['CONN_MAX_AGE'] == max_age
            from config.asgi import application
            assert all(connections[alias].settings_dict['CONN_MAX_AGE'] == 0 for alias in databases)
            asyncio.run(exercise(application))
        print('ASGI connection cleanup verified')
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script, max_age, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ASGI connection cleanup verified" in result.stdout
