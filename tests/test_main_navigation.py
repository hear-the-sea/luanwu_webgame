from html.parser import HTMLParser
from types import SimpleNamespace

import pytest
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import resolve, reverse


class MainNavigationParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_main_navigation = False
        self.active_links = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "nav" and attributes.get("id") == "main-nav":
            self.in_main_navigation = True
        if tag == "a" and self.in_main_navigation and "active" in attributes.get("class", "").split():
            self.active_links.append(attributes.get("href"))

    def handle_endtag(self, tag):
        if tag == "nav":
            self.in_main_navigation = False


@pytest.mark.parametrize(
    "page, active_navigation",
    [
        ("guilds:warehouse", "guilds:hall"),
        ("guilds:technology", "guilds:hall"),
        ("guilds:exchange_logs", "guilds:hall"),
        ("gameplay:warehouse", "gameplay:warehouse"),
        ("gameplay:technology", "gameplay:technology"),
        ("guests:roster", "guests:roster"),
    ],
)
def test_main_navigation_highlights_only_the_current_application(page, active_navigation):
    request = RequestFactory().get(reverse(page))
    request.resolver_match = resolve(request.path)
    html = render_to_string(
        "base.html",
        {"request": request, "user": SimpleNamespace(is_authenticated=True, id=1), "csrf_token": "test-token"},
    )
    parser = MainNavigationParser()
    parser.feed(html)
    assert parser.active_links == [reverse(active_navigation)]
