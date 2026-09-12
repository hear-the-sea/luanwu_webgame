from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

import pytest
from django.test import RequestFactory

from core.utils.network import get_client_ip


@pytest.mark.parametrize(
    "trusted,expected",
    [
        (["172.18.0.5/32"], "172.18.0.2"),
        (["172.18.0.5/32", "172.18.0.2/32"], "203.0.113.8"),
    ],
)
def test_django_requires_both_controlled_proxy_hops(settings, trusted, expected):
    settings.TRUSTED_PROXY_IPS = trusted
    request = RequestFactory().get(
        "/accounts/register/",
        REMOTE_ADDR="172.18.0.5",
        HTTP_X_FORWARDED_FOR="198.51.100.99, 203.0.113.8, 172.18.0.2",
    )
    assert get_client_ip(request, trust_proxy=True) == expected


@pytest.mark.parametrize("trusted", ["", "172.18.0.2/32", "172.18.0.2/32 192.0.2.10/32"])
def test_caddy_adapts_explicit_proxy_trust(trusted):
    binary = os.environ.get("CADDY_TEST_BINARY")
    if not binary:
        pytest.skip("Set CADDY_TEST_BINARY to a Caddy 2.10 executable")
    path = Path(__file__).resolve().parents[1] / "docker" / "caddy" / "Caddyfile"
    result = subprocess.run(
        [binary, "adapt", "--config", str(path), "--adapter", "caddyfile"],
        env={**os.environ, "CADDY_SITE_ADDRESS": "localhost", "CADDY_TRUSTED_PROXIES": trusted},
        capture_output=True,
        text=True,
        check=True,
    )
    server = json.loads(result.stdout)["apps"]["http"]["servers"]["srv0"]
    assert server["trusted_proxies"]["source"] == "static"
    assert server["trusted_proxies"].get("ranges", []) == trusted.split()
    assert server["trusted_proxies_strict"] == 1
    assert server["client_ip_headers"] == ["X-Forwarded-For"]


@pytest.mark.parametrize(
    "trusted,forwarded,expected",
    [
        ("", "203.0.113.8", "127.0.0.1"),
        ("127.0.0.1/32", "203.0.113.8", "203.0.113.8"),
        ("127.0.0.1/32", "198.51.100.99, 203.0.113.8", "203.0.113.8"),
        ("127.0.0.2/32", "203.0.113.8", "127.0.0.1"),
    ],
)
def test_caddy_preserves_only_trusted_forwarding(tmp_path, settings, trusted, forwarded, expected):
    binary = os.environ.get("CADDY_TEST_BINARY")
    if not binary or os.environ.get("CADDY_TEST_HTTP") != "1":
        pytest.skip("Set CADDY_TEST_BINARY and CADDY_TEST_HTTP=1 for loopback proxy integration checks")

    class EchoHeaders(BaseHTTPRequestHandler):
        def do_GET(self):
            payload = json.dumps(dict(self.headers)).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    backend = ThreadingHTTPServer(("127.0.0.1", 0), EchoHeaders)
    thread = threading.Thread(target=backend.serve_forever, daemon=True)
    thread.start()
    process = None
    try:
        env = {
            **os.environ,
            "CADDY_SITE_ADDRESS": "http://127.0.0.1",
            "CADDY_TRUSTED_PROXIES": trusted,
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_DATA_HOME": str(tmp_path / "data"),
        }
        caddyfile = Path(__file__).resolve().parents[1] / "docker" / "caddy" / "Caddyfile"
        adapted = subprocess.run(
            [binary, "adapt", "--config", str(caddyfile), "--adapter", "caddyfile"],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        config = json.loads(adapted.stdout)
        config["admin"] = {"disabled": True}
        server = config["apps"]["http"]["servers"]["srv0"]
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        server["listen"] = [f"127.0.0.1:{port}"]

        def redirect_backend(node):
            if isinstance(node, dict):
                if node.get("handler") == "reverse_proxy":
                    node["upstreams"] = [{"dial": f"127.0.0.1:{backend.server_port}"}]
                    node["headers"] = {"request": {"set": {"X-Test-Client-Ip": ["{http.request.client_ip}"]}}}
                for value in node.values():
                    redirect_backend(value)
            elif isinstance(node, list):
                for value in node:
                    redirect_backend(value)

        redirect_backend(config)
        path = tmp_path / "caddy.json"
        path.write_text(json.dumps(config))
        with (tmp_path / "caddy.log").open("w+") as log:
            process = subprocess.Popen([binary, "run", "--config", str(path)], env=env, stdout=log, stderr=log)
            opener = build_opener(ProxyHandler({}))
            request = Request(f"http://127.0.0.1:{port}/accounts/register/", headers={"X-Forwarded-For": forwarded})
            deadline = time.monotonic() + 10
            while True:
                try:
                    with opener.open(request, timeout=1) as response:
                        headers = {key.lower(): value for key, value in json.load(response).items()}
                    break
                except URLError:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        log.seek(0)
                        pytest.fail(log.read())
                    time.sleep(0.05)

        assert headers["x-test-client-ip"] == expected
        settings.TRUSTED_PROXY_IPS = ["127.0.0.1/32"]
        request = RequestFactory().get(
            "/accounts/register/",
            REMOTE_ADDR="127.0.0.1",
            HTTP_X_FORWARDED_FOR=headers["x-forwarded-for"],
        )
        assert get_client_ip(request, trust_proxy=True) == expected
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        backend.shutdown()
        backend.server_close()
        thread.join(timeout=5)
