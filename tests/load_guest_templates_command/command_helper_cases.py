from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from django.core.management.base import CommandError

from guests.management.commands.load_guest_templates import Command
from guests.models import GuestTemplate
from tests.load_guest_templates_command.support import _Recorder


def test_finish_sync_clears_recruitment_and_battle_template_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    cleared: list[str] = []

    monkeypatch.setattr(
        "guests.management.commands.load_guest_templates.clear_template_cache",
        lambda: cleared.append("recruitment"),
    )
    monkeypatch.setattr(
        "guests.management.commands.load_guest_templates.clear_guest_template_cache",
        lambda: cleared.append("battle"),
    )

    command = Command()
    command.stdout = cast(Any, _Recorder())

    command._finish_sync(verbosity=0, fallback_avatar_count=0)

    assert cleared == ["recruitment", "battle"]


@pytest.mark.django_db
def test_sync_template_avatar_programming_error_bubbles_up(tmp_path: Path, monkeypatch) -> None:
    command = Command()
    command.stdout = cast(Any, _Recorder())
    template = GuestTemplate.objects.create(
        key="avatar_bug_tpl",
        name="头像契约错误模板",
        archetype="civil",
        rarity="gray",
    )
    avatar_path = tmp_path / "avatar.png"
    avatar_path.write_bytes(b"not-used")

    monkeypatch.setattr(
        command,
        "_ensure_avatar_saved",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken avatar contract")),
    )

    with pytest.raises(AssertionError, match="broken avatar contract"):
        command._sync_template_avatar(
            template,
            {"avatar": "avatar.png"},
            False,
            tmp_path,
            {},
            {},
            1,
        )


def test_load_heroes_from_dir_recoverable_command_error_degrades(tmp_path: Path) -> None:
    command = Command()
    messages: list[str] = []
    command.stdout = cast(Any, _Recorder(messages))

    (tmp_path / "good.json").write_text(json.dumps({"heroes": {"green": [{"key": "hero_ok"}]}}), encoding="utf-8")
    (tmp_path / "bad.json").write_text("{}", encoding="utf-8")

    def _load_payload(path: Path) -> dict:
        if path.name == "bad.json":
            raise CommandError("broken hero roster file")
        return json.loads(path.read_text(encoding="utf-8"))

    merged = command._load_heroes_from_dir(tmp_path, _load_payload)

    assert merged == {"green": [{"key": "hero_ok"}]}
    assert any("Failed to load bad.json: broken hero roster file" in message for message in messages)


def test_load_heroes_from_dir_programming_error_bubbles_up(tmp_path: Path) -> None:
    command = Command()
    command.stdout = cast(Any, _Recorder())

    (tmp_path / "broken.json").write_text("{}", encoding="utf-8")

    def _load_payload(_path: Path) -> dict:
        raise AssertionError("broken hero roster contract")

    with pytest.raises(AssertionError, match="broken hero roster contract"):
        command._load_heroes_from_dir(tmp_path, _load_payload)
