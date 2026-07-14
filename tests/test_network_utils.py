from __future__ import annotations

from django.test import RequestFactory, TestCase, override_settings

from core.utils.network import get_asgi_client_ip, get_client_ip


class NetworkUtilsTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_get_client_ip_without_proxy_returns_remote_addr(self):
        request = self.factory.get("/")
        request.META["REMOTE_ADDR"] = "10.0.0.2"
        request.META["HTTP_X_FORWARDED_FOR"] = "203.0.113.8"

        self.assertEqual(get_client_ip(request, trust_proxy=False), "10.0.0.2")

    @override_settings(TRUSTED_PROXY_IPS=["10.0.0.0/24"])
    def test_get_client_ip_uses_xff_for_trusted_proxy(self):
        request = self.factory.get("/")
        request.META["REMOTE_ADDR"] = "10.0.0.2"
        request.META["HTTP_X_FORWARDED_FOR"] = "203.0.113.8, 10.0.0.2"

        self.assertEqual(get_client_ip(request, trust_proxy=True), "203.0.113.8")

    @override_settings(TRUSTED_PROXY_IPS=["10.0.0.0/24"])
    def test_get_client_ip_ignores_spoofed_leftmost_xff_hops(self):
        request = self.factory.get("/")
        request.META["REMOTE_ADDR"] = "10.0.0.2"
        request.META["HTTP_X_FORWARDED_FOR"] = "127.0.0.1, 203.0.113.8"

        self.assertEqual(get_client_ip(request, trust_proxy=True), "203.0.113.8")

    @override_settings(TRUSTED_PROXY_IPS=["10.0.0.5"])
    def test_get_client_ip_ignores_xff_for_untrusted_proxy(self):
        request = self.factory.get("/")
        request.META["REMOTE_ADDR"] = "10.0.0.2"
        request.META["HTTP_X_FORWARDED_FOR"] = "203.0.113.8"

        self.assertEqual(get_client_ip(request, trust_proxy=True), "10.0.0.2")

    @override_settings(TRUSTED_PROXY_IPS=["172.16.0.0/12"])
    def test_get_asgi_client_ip_uses_forwarded_chain_from_trusted_proxy(self):
        scope = {
            "client": ("172.18.0.4", 53100),
            "headers": [(b"x-forwarded-for", b"203.0.113.8")],
        }

        self.assertEqual(get_asgi_client_ip(scope, trust_proxy=True), "203.0.113.8")

    @override_settings(TRUSTED_PROXY_IPS=["172.16.0.0/12"])
    def test_get_asgi_client_ip_ignores_spoofed_leftmost_forwarded_hop(self):
        scope = {
            "client": ("172.18.0.4", 53100),
            "headers": [(b"x-forwarded-for", b"127.0.0.1, 203.0.113.8")],
        }

        self.assertEqual(get_asgi_client_ip(scope, trust_proxy=True), "203.0.113.8")

    @override_settings(TRUSTED_PROXY_IPS=["172.16.0.0/12"])
    def test_get_asgi_client_ip_ignores_forwarding_from_untrusted_peer(self):
        scope = {
            "client": ("198.51.100.4", 53100),
            "headers": [(b"x-forwarded-for", b"127.0.0.1")],
        }

        self.assertEqual(get_asgi_client_ip(scope, trust_proxy=True), "198.51.100.4")

    @override_settings(TRUSTED_PROXY_IPS=["172.16.0.0/12"])
    def test_get_asgi_client_ip_handles_malformed_forwarding_and_ipv6(self):
        malformed_scope = {
            "client": ("172.18.0.4", 53100),
            "headers": [(b"x-forwarded-for", b"not-an-ip")],
        }
        ipv6_scope = {
            "client": ("172.18.0.4", 53100),
            "headers": [(b"x-forwarded-for", b"2001:db8::5")],
        }

        self.assertEqual(get_asgi_client_ip(malformed_scope, trust_proxy=True), "172.18.0.4")
        self.assertEqual(get_asgi_client_ip(ipv6_scope, trust_proxy=True), "2001:db8::5")

    def test_get_asgi_client_ip_without_proxy_uses_direct_peer(self):
        scope = {
            "client": ("203.0.113.9", 53100),
            "headers": [(b"x-forwarded-for", b"127.0.0.1")],
        }

        self.assertEqual(get_asgi_client_ip(scope, trust_proxy=False), "203.0.113.9")
