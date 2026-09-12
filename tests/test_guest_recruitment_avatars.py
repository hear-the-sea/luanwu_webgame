from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from PIL import Image

from gameplay.services.manor.core import ensure_manor
from guests.avatar import build_guest_avatar_catalog
from guests.models import Guest, GuestTemplate, RecruitmentCandidate, RecruitmentPool
from guests.services.recruitment_guests import bulk_finalize_candidates, finalize_candidate


@pytest.fixture
def avatar_sources(tmp_path, settings, monkeypatch):
    settings.BASE_DIR = tmp_path
    settings.MEDIA_ROOT = tmp_path / "media"
    source_dir = tmp_path / "data" / "images" / "guests"
    source_dir.mkdir(parents=True)
    for tag in ("wen", "wu"):
        for gender in ("male", "female"):
            Image.new("RGB", (40, 60), "red").save(source_dir / f"test_{tag}_{gender}.png")
    monkeypatch.setattr("guests.avatar.get_guest_avatar_catalog", lambda: build_guest_avatar_catalog(source_dir))
    return source_dir


@pytest.fixture
def avatar_manor(django_user_model):
    return ensure_manor(django_user_model.objects.create_user(username="recruit_avatar_owner"))


@pytest.mark.django_db
@pytest.mark.parametrize("bulk", [False, True])
@pytest.mark.parametrize("archetype,tag", [("civil", "wen"), ("military", "wu")])
@pytest.mark.parametrize("gender", ["male", "female"])
@pytest.mark.parametrize("template_avatar", ["", "guests/legacy.webp"])
def test_recruitment_persists_matching_avatar(
    avatar_sources,
    avatar_manor,
    settings,
    bulk,
    archetype,
    tag,
    gender,
    template_avatar,
):
    template = GuestTemplate.objects.create(
        key=f"base_gray_{archetype}",
        name="avatar",
        rarity="gray",
        archetype=archetype,
        default_gender=gender,
        avatar=template_avatar,
    )
    pool = RecruitmentPool.objects.create(key="avatar_pool", name="avatar")
    candidates = [
        RecruitmentCandidate.objects.create(
            manor=avatar_manor,
            template=template,
            pool=pool,
            rarity="gray",
            archetype=archetype,
        )
        for _ in range(2 if bulk else 1)
    ]

    if bulk:
        guests, failed = bulk_finalize_candidates(candidates)
        assert failed == []
        assert len(guests) == 2
    else:
        guests = [finalize_candidate(candidates[0])]

    expected = f"guests/test_{tag}_{gender}.webp"
    for guest in guests:
        guest.refresh_from_db()
        assert guest.avatar_asset_key == expected
        assert guest.avatar_url == f"/media/{expected}"
        assert guest.gender == gender
    with Image.open(Path(settings.MEDIA_ROOT) / expected) as image:
        assert image.format == "WEBP"
    assert len(list((Path(settings.MEDIA_ROOT) / "guests").iterdir())) == 1


@pytest.mark.django_db
def test_recruitment_preserves_named_template_avatar(avatar_sources, avatar_manor):
    template = GuestTemplate.objects.create(
        key="named_guest",
        name="named",
        rarity="green",
        avatar="guests/named.webp",
    )
    pool = RecruitmentPool.objects.create(key="named_pool", name="named")
    candidate = RecruitmentCandidate.objects.create(
        manor=avatar_manor,
        template=template,
        pool=pool,
        rarity="green",
        archetype="civil",
    )
    guest = finalize_candidate(candidate)
    assert guest.avatar_asset_key == ""
    assert guest.avatar_url == "/media/guests/named.webp"


@pytest.mark.django_db
def test_backfill_previews_then_repairs_and_is_idempotent(avatar_sources, avatar_manor, settings):
    template = GuestTemplate.objects.create(key="base_gray_civil", name="gray", rarity="gray", archetype="civil")
    guest = Guest.objects.create(manor=avatar_manor, template=template, gender="female")
    preserved = Guest.objects.create(
        manor=avatar_manor,
        template=template,
        gender="male",
        avatar_asset_key="guests/existing.webp",
    )
    disguised = Guest.objects.create(
        manor=avatar_manor,
        template=template,
        gender="male",
        avatar_override_key="super_guest_disguise",
    )
    output = StringIO()

    call_command("backfill_guest_avatars", stdout=output)
    guest.refresh_from_db()
    assert guest.avatar_asset_key == ""
    assert not Path(settings.MEDIA_ROOT).exists()
    assert "mode=dry-run" in output.getvalue()
    assert "ready=1" in output.getvalue()

    call_command("backfill_guest_avatars", apply=True, stdout=StringIO())
    guest.refresh_from_db()
    assert guest.avatar_url == "/media/guests/test_wen_female.webp"
    preserved.refresh_from_db()
    disguised.refresh_from_db()
    assert preserved.avatar_asset_key == "guests/existing.webp"
    assert disguised.avatar_asset_key == ""

    output = StringIO()
    call_command("backfill_guest_avatars", apply=True, stdout=output)
    assert "updated=0" in output.getvalue()


@pytest.mark.django_db
def test_backfill_reports_missing_source_without_writes(avatar_sources, avatar_manor, settings):
    template = GuestTemplate.objects.create(key="base_gray_civil", name="gray", rarity="gray", archetype="civil")
    guest = Guest.objects.create(manor=avatar_manor, template=template, gender="unknown")
    output = StringIO()
    with pytest.raises(CommandError, match="1"):
        call_command("backfill_guest_avatars", apply=True, stdout=output)
    guest.refresh_from_db()
    assert guest.avatar_asset_key == ""
    assert "unavailable=1" in output.getvalue()
    assert not Path(settings.MEDIA_ROOT).exists()


@pytest.mark.django_db
def test_backfill_preserves_template_portrait_and_limits_scope(
    avatar_sources, avatar_manor, settings, django_user_model
):
    media_dir = Path(settings.MEDIA_ROOT) / "guests"
    media_dir.mkdir(parents=True)
    Image.new("RGB", (40, 60)).save(media_dir / "legacy.webp")
    template = GuestTemplate.objects.create(
        key="base_gray_civil",
        name="gray",
        rarity="gray",
        archetype="civil",
        avatar="guests/legacy.webp",
    )
    visible = Guest.objects.create(manor=avatar_manor, template=template, gender="female")
    other_template = GuestTemplate.objects.create(
        key="base_gray_military",
        name="gray",
        rarity="gray",
        archetype="military",
    )
    guests = [Guest.objects.create(manor=avatar_manor, template=other_template, gender="male") for _ in range(2)]
    other_manor = ensure_manor(django_user_model.objects.create_user(username="other_avatar_owner"))
    outside = Guest.objects.create(manor=other_manor, template=other_template, gender="female")
    call_command("backfill_guest_avatars", apply=True, manor_id=avatar_manor.pk, limit=2, stdout=StringIO())
    visible.refresh_from_db()
    outside.refresh_from_db()
    assert visible.avatar_asset_key == ""
    assert outside.avatar_asset_key == ""
    assert Guest.objects.filter(pk__in=[guest.pk for guest in guests]).exclude(avatar_asset_key="").count() == 1


@pytest.mark.django_db
def test_backfill_reports_storage_failure(avatar_sources, avatar_manor, monkeypatch):
    template = GuestTemplate.objects.create(key="base_gray_civil", name="gray", rarity="gray", archetype="civil")
    guest = Guest.objects.create(manor=avatar_manor, template=template, gender="male")

    def fail_save(*args, **kwargs):
        raise PermissionError("media is not writable")

    monkeypatch.setattr("guests.avatar.default_storage.save", fail_save)
    with pytest.raises(CommandError):
        call_command("backfill_guest_avatars", apply=True, stdout=StringIO())
    guest.refresh_from_db()
    assert guest.avatar_asset_key == ""


@pytest.mark.django_db
def test_backfill_does_not_overwrite_concurrent_assignment(avatar_sources, avatar_manor, monkeypatch):
    from guests.management.commands import backfill_guest_avatars

    template = GuestTemplate.objects.create(key="base_gray_civil", name="gray", rarity="gray", archetype="civil")
    guest = Guest.objects.create(manor=avatar_manor, template=template, gender="male")
    original_prepare = backfill_guest_avatars.ensure_guest_avatar_asset

    def assign_before_update(filename):
        Guest.objects.filter(pk=guest.pk).update(avatar_asset_key="guests/concurrent.webp")
        return original_prepare(filename)

    monkeypatch.setattr(backfill_guest_avatars, "ensure_guest_avatar_asset", assign_before_update)
    call_command("backfill_guest_avatars", apply=True, stdout=StringIO())
    guest.refresh_from_db()
    assert guest.avatar_asset_key == "guests/concurrent.webp"
