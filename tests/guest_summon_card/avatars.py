from types import SimpleNamespace

import pytest
from django.core.files.storage import FileSystemStorage
from PIL import Image

from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.inventory.use import use_inventory_item
from gameplay.services.manor.core import ensure_manor
from guests.avatar import ensure_guest_avatar_asset, pick_guest_avatar_filename, resolve_guest_avatar_url
from guests.constants import SUPER_GUEST_DISGUISE_AVATAR_KEY, SUPER_GUEST_DISGUISE_AVATAR_PATH
from guests.models import Guest, GuestTemplate


def test_guest_avatar_asset_prepares_reusable_webp(tmp_path, settings, monkeypatch) -> None:
    settings.BASE_DIR = tmp_path
    source_dir = tmp_path / "data" / "images" / "guests"
    source_dir.mkdir(parents=True)
    Image.new("RGB", (500, 300)).save(source_dir / "hero_wen_female.png")
    storage = FileSystemStorage(location=tmp_path / "media", base_url="/media/")
    monkeypatch.setattr("guests.avatar.default_storage", storage)

    asset_key = ensure_guest_avatar_asset("hero_wen_female.png")

    assert asset_key == "guests/hero_wen_female.webp"
    assert ensure_guest_avatar_asset("hero_wen_female.png") == asset_key
    with storage.open(asset_key) as stored_file, Image.open(stored_file) as image:
        assert image.format == "WEBP"
        assert image.size == (400, 240)


@pytest.mark.parametrize("asset_key", ["../private.webp", "guests/../private.webp", "guests/nested/private.webp"])
def test_guest_avatar_rejects_uncontrolled_paths(asset_key, monkeypatch) -> None:
    monkeypatch.setattr(
        "guests.avatar.default_storage.exists",
        lambda _path: pytest.fail("Invalid avatar paths must not reach storage"),
    )
    guest = SimpleNamespace(avatar_asset_key=asset_key, template=SimpleNamespace(avatar=None))

    assert resolve_guest_avatar_url(guest) == ""


def test_guest_avatar_prefers_disguise_then_asset_then_template(monkeypatch) -> None:
    monkeypatch.setattr("guests.avatar.default_storage.exists", lambda _path: True)
    monkeypatch.setattr("guests.avatar.default_storage.url", lambda path: f"/media/{path}")
    guest = SimpleNamespace(
        avatar_override_key=SUPER_GUEST_DISGUISE_AVATAR_KEY,
        avatar_asset_key="guests/generated.webp",
        template=SimpleNamespace(avatar=SimpleNamespace(url="/media/legacy.webp")),
    )

    assert resolve_guest_avatar_url(guest) == f"/media/{SUPER_GUEST_DISGUISE_AVATAR_PATH}"
    guest.avatar_override_key = ""
    assert resolve_guest_avatar_url(guest) == "/media/guests/generated.webp"
    monkeypatch.setattr("guests.avatar.default_storage.exists", lambda _path: False)
    assert resolve_guest_avatar_url(guest) == "/media/legacy.webp"


@pytest.mark.parametrize(
    ("archetype", "gender", "expected_marker"),
    [
        ("civil", "male", "civil_male"),
        ("civil", "female", "civil_female"),
        ("military", "male", "military_male"),
        ("military", "female", "military_female"),
    ],
)
def test_guest_avatar_picker_keeps_archetype_and_gender(archetype, gender, expected_marker) -> None:
    catalog = {
        ("civil", "male"): ["civil_male.png"],
        ("civil", "female"): ["civil_female.png"],
        ("military", "male"): ["military_male.png"],
        ("military", "female"): ["military_female.png"],
    }

    avatar_filename = pick_guest_avatar_filename(
        catalog,
        archetype,
        gender,
        seed_key="avatar-test",
        fallback_to_any=False,
    )

    assert avatar_filename == f"{expected_marker}.png"


@pytest.mark.django_db
def test_junior_guest_card_replaces_legacy_template_avatar_for_gray_base_guest(
    monkeypatch,
    django_user_model,
) -> None:
    user = django_user_model.objects.create_user(username="summon_gray_legacy_avatar", password="pass123")
    manor = ensure_manor(user)
    guest_template = GuestTemplate.objects.create(
        key="base_gray_civil",
        name="灰阶谋士",
        archetype="civil",
        rarity="gray",
        default_gender="male",
        default_morality=65,
        avatar="legacy_template_avatar.webp",
    )
    card_template = ItemTemplate.objects.create(
        key="junior_gray_legacy_avatar_card",
        name="初级门客卡",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "summon_guest",
            "choices": [{"template_key": guest_template.key, "weight": 100}],
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=card_template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    prepared: list[tuple[str, str]] = []

    def fake_prepare_guest_avatar_asset(*, archetype, gender, rng):
        prepared.append((archetype, gender))
        return "guests/base_gray_civil_male.webp"

    monkeypatch.setattr("guests.avatar.prepare_guest_avatar_asset", fake_prepare_guest_avatar_asset)

    use_inventory_item(item)

    guest = Guest.objects.get(manor=manor)
    assert prepared == [("civil", "male")]
    assert guest.avatar_asset_key == "guests/base_gray_civil_male.webp"


@pytest.mark.django_db
@pytest.mark.parametrize("archetype", ["civil", "military"])
@pytest.mark.parametrize("gender", ["male", "female"])
def test_junior_guest_card_assigns_avatar_using_created_guest_profile(
    monkeypatch,
    django_user_model,
    archetype,
    gender,
) -> None:
    user = django_user_model.objects.create_user(
        username=f"summon_avatar_{archetype}_{gender}",
        password="pass123",
    )
    manor = ensure_manor(user)
    guest_template = GuestTemplate.objects.create(
        key=f"junior_avatar_{archetype}_{gender}",
        name="灰阶测试门客",
        archetype=archetype,
        rarity="gray",
        default_gender=gender,
        default_morality=60,
    )
    card_template = ItemTemplate.objects.create(
        key=f"junior_avatar_card_{archetype}_{gender}",
        name="初级门客卡",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "summon_guest",
            "choices": [{"template_key": guest_template.key, "weight": 100}],
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=card_template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    prepared: list[tuple[str, str]] = []

    def fake_prepare_guest_avatar_asset(*, archetype, gender, rng):
        prepared.append((archetype, gender))
        return f"guests/{archetype}_{gender}.webp"

    monkeypatch.setattr(
        "guests.avatar.prepare_guest_avatar_asset",
        fake_prepare_guest_avatar_asset,
    )

    use_inventory_item(item)

    guest = Guest.objects.get(manor=manor)
    assert guest.gender == gender
    assert prepared == [(archetype, gender)]
    assert guest.avatar_asset_key == f"guests/{archetype}_{gender}.webp"
