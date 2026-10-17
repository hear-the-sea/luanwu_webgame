from io import StringIO

import pytest
import yaml
from django.core.files.storage import FileSystemStorage
from django.core.management import call_command
from django.core.management.base import CommandError
from PIL import Image

from guests.models import Guest, GuestTemplate


@pytest.fixture
def portrait_sync(tmp_path, monkeypatch):
    images = tmp_path / "images"
    images.mkdir()
    heroes = tmp_path / "heroes"
    heroes.mkdir()
    media = tmp_path / "media"
    monkeypatch.setattr(GuestTemplate._meta.get_field("avatar"), "storage", FileSystemStorage(location=media))
    main = tmp_path / "main.yaml"
    main.write_text("{}", encoding="utf-8")

    def run(entries, **kwargs):
        (heroes / "test.yaml").write_text(yaml.safe_dump({"heroes": {"blue": entries}}), encoding="utf-8")
        call_command(
            "sync_guest_portraits",
            file=str(main),
            heroes_dir=str(heroes),
            images_dir=str(images),
            stdout=StringIO(),
            **kwargs,
        )

    return run, images, media


def make_template(key="hero", **kwargs):
    return GuestTemplate.objects.create(key=key, name=key, rarity="blue", archetype="military", **kwargs)


@pytest.mark.django_db
def test_dry_run_writes_neither_storage_nor_database(portrait_sync):
    run, images, media = portrait_sync
    template = make_template(flavor="original")
    Image.new("RGB", (40, 40), "red").save(images / "face.png")
    run([{"key": "hero", "avatar": "face.png", "flavor": "new"}], dry_run=True)
    template.refresh_from_db()
    assert template.flavor == "original"
    assert not template.avatar
    assert not media.exists()


@pytest.mark.django_db
def test_all_paths_validated_before_any_writes(portrait_sync):
    run, images, media = portrait_sync
    template = make_template(flavor="original")
    make_template("missing")
    Image.new("RGB", (40, 40)).save(images / "valid.png")
    with pytest.raises(CommandError, match="Missing avatar"):
        run([{"key": "hero", "avatar": "valid.png", "flavor": "new"}, {"key": "missing", "avatar": "missing.png"}])
    template.refresh_from_db()
    assert template.flavor == "original"
    assert not media.exists()


@pytest.mark.django_db
def test_same_image_shared_changed_bytes_versioned_and_old_asset_retained(portrait_sync):
    run, images, media = portrait_sync
    a = make_template(base_attack=987, flavor="old")
    b = make_template("second")
    source = images / "face.png"
    Image.new("RGB", (40, 40), "red").save(source)
    entries = [
        {"key": "hero", "avatar": "face.png", "flavor": "new", "base_attack": 1},
        {"key": "second", "avatar": "face.png"},
    ]
    run(entries)
    a.refresh_from_db()
    b.refresh_from_db()
    old = a.avatar.name
    assert old == b.avatar.name
    assert a.flavor == "new" and a.base_attack == 987
    run(entries)
    assert len(list(media.rglob("*.webp"))) == 1
    Image.new("RGB", (40, 40), "blue").save(source)
    run(entries)
    a.refresh_from_db()
    assert a.avatar.name != old
    assert (media / old).is_file()
    assert len(list(media.rglob("*.webp"))) == 2


@pytest.mark.parametrize("avatar", ["../outside.png", "/outside.png", "folder\\outside.png"])
def test_path_traversal_rejected_before_storage(portrait_sync, avatar):
    from guests.management.commands.sync_guest_portraits import Command

    _, images, _ = portrait_sync
    with pytest.raises(CommandError, match="Unsafe avatar path"):
        Command()._validate([{"key": "hero", "avatar": avatar}], images)


@pytest.mark.django_db
def test_missing_database_template_aborts_instead_of_partial_sync(portrait_sync):
    run, _, media = portrait_sync
    template = make_template(flavor="old")
    with pytest.raises(CommandError, match="Templates missing"):
        run([{"key": "hero", "flavor": "new"}, {"key": "not_imported"}])
    template.refresh_from_db()
    assert template.flavor == "old"
    assert not media.exists()


@pytest.mark.django_db
def test_player_disguise_and_selected_original_unchanged(portrait_sync, manor_factory):
    run, images, _ = portrait_sync
    template = make_template()
    manor, _ = manor_factory()
    guest = Guest.objects.create(
        manor=manor,
        template=template,
        avatar_asset_key="guests/disguise.webp",
        original_avatar_asset_key="guests/original.webp",
        super_disguise_applied=True,
        use_original_avatar=True,
        force=999,
    )
    before = Guest.objects.filter(pk=guest.pk).values().get()
    Image.new("RGB", (40, 40)).save(images / "face.png")
    run([{"key": "hero", "avatar": "face.png", "flavor": "new"}])
    assert Guest.objects.filter(pk=guest.pk).values().get() == before


@pytest.mark.django_db
def test_database_changes_rollback_together(portrait_sync, monkeypatch):
    from django.db.models.query import QuerySet

    run, _, _ = portrait_sync
    a = make_template(flavor="old")
    b = make_template("second", flavor="old")
    original_update = QuerySet.update
    calls = []

    def failing_update(queryset, **kwargs):
        if queryset.model is GuestTemplate:
            calls.append(kwargs)
            if len(calls) == 2:
                raise RuntimeError("simulated write failure")
        return original_update(queryset, **kwargs)

    monkeypatch.setattr(QuerySet, "update", failing_update)
    with pytest.raises(RuntimeError, match="simulated write failure"):
        run([{"key": "hero", "flavor": "new"}, {"key": "second", "flavor": "new"}])
    a.refresh_from_db()
    b.refresh_from_db()
    assert a.flavor == b.flavor == "old"
