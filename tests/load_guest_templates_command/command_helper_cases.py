from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from django.core.management.base import CommandError

from guests.management.commands.load_guest_templates import Command
from guests.models import Guest, GuestTemplate
from tests.load_guest_templates_command.support import _Recorder


@pytest.mark.parametrize("blue_luck_override", [None, 137])
def test_hermits_inherit_blue_luck_without_changing_other_attributes(blue_luck_override) -> None:
    command = Command()
    data_dir = Path(__file__).resolve().parents[2] / "data"
    profiles = command._load_payload(data_dir / "guest_templates.yaml")["attribute_profiles"]
    roster = command._load_payload(data_dir / "guests" / "hermits.yaml")["heroes"]
    if blue_luck_override is not None:
        for profile in profiles["blue"].values():
            profile["luck"] = blue_luck_override

    templates: list[dict] = []
    command._append_hero_roster_templates(templates, roster, profiles, verbosity=0)

    assert templates
    assert {template["archetype"] for template in templates} == {"military", "civil"}
    for template in templates:
        assert template["is_hermit"] is True
        assert template["base_luck"] == profiles["blue"][template["archetype"]]["luck"]
        hero = next(hero for hero in roster[template["rarity"]] if hero["key"] == template["key"])
        original = command._build_template_from_stats(
            hero, template["rarity"], template["archetype"], profiles[template["rarity"]][template["archetype"]]
        )
        assert template == {**original, "base_luck": template["base_luck"]}

    ordinary_roster = {
        "black": [
            {"key": "ordinary_military", "name": "Ordinary military", "archetype": "military"},
            {"key": "ordinary_civil", "name": "Ordinary civil", "archetype": "civil"},
        ]
    }
    ordinary_templates: list[dict] = []
    command._append_hero_roster_templates(ordinary_templates, ordinary_roster, profiles, verbosity=0)
    assert [template["base_luck"] for template in ordinary_templates] == [25, 80]


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


@pytest.mark.django_db
@pytest.mark.parametrize("skip_images", [False, True])
def test_instance_avatar_template_preserves_legacy_template_avatar(skip_images) -> None:
    command = Command()
    GuestTemplate.objects.create(
        key="base_gray_civil",
        name="灰阶谋士",
        archetype="civil",
        rarity="gray",
        avatar="legacy_instance_avatar.webp",
    )

    command._sync_templates(
        [{"key": "base_gray_civil", "name": "灰阶谋士", "archetype": "civil", "rarity": "gray"}],
        assign_missing_avatars=True,
        skip_images=skip_images,
        verbosity=0,
    )

    template = GuestTemplate.objects.get(key="base_gray_civil")
    template.refresh_from_db()
    assert template.avatar.name == "legacy_instance_avatar.webp"


@pytest.mark.django_db
def test_old_template_avatar_is_preserved_while_guest_uses_asset(django_user_model, monkeypatch) -> None:
    user = django_user_model.objects.create_user(username="shared_avatar_owner")
    template = GuestTemplate.objects.create(key="shared_avatar_template", name="共享头像")
    guest = Guest.objects.create(
        manor=user.manor,
        template=template,
        avatar_asset_key="guests/shared_avatar.webp",
    )
    deleted: list[str] = []
    monkeypatch.setattr(template.avatar.storage, "delete", deleted.append)

    Command()._safe_delete_old_avatar(template, "guests/shared_avatar.webp", "guests/new.webp")

    assert deleted == []

    guest.avatar_asset_key = ""
    guest.save(update_fields=["avatar_asset_key"])
    Command()._safe_delete_old_avatar(template, "guests/shared_avatar.webp", "guests/new.webp")

    assert deleted == ["guests/shared_avatar.webp"]


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
