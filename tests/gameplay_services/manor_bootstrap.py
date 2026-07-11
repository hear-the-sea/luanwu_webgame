import logging

import pytest
from django.db import DatabaseError, IntegrityError
from django.db.utils import ProgrammingError

from gameplay.models import InventoryItem, ItemTemplate, Manor
from gameplay.services.manor import core as manor_service
from gameplay.services.manor.core import ensure_manor
from tests.gameplay_services.support import User

SQLITE_OCCUPIED_MANOR_LOCATION_CONFLICT = (
    "UNIQUE constraint failed: gameplay_manor.occupied_region, "
    "gameplay_manor.coordinate_x, gameplay_manor.coordinate_y"
)


@pytest.mark.django_db
def test_ensure_manor_creates_new():
    user = User.objects.create_user(username="testuser", password="test123")
    manor = ensure_manor(user)

    assert manor is not None
    assert manor.user == user
    assert manor.grain >= 0
    assert manor.silver == 5000
    assert manor.newbie_protection_until is None


@pytest.mark.django_db
def test_ensure_manor_grants_initial_peace_shield_when_template_exists():
    ItemTemplate.objects.create(
        key="peace_shield_small",
        name="免战牌·小",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={"duration": 28800},
    )

    user = User.objects.create_user(username="testuser_init_shield", password="test123")
    manor = ensure_manor(user)

    shield_item = InventoryItem.objects.filter(
        manor=manor,
        template__key="peace_shield_small",
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    ).first()
    assert shield_item is not None
    assert shield_item.quantity == 1


@pytest.mark.django_db
def test_ensure_manor_does_not_duplicate_initial_peace_shield_on_repeat_call():
    ItemTemplate.objects.create(
        key="peace_shield_small",
        name="免战牌·小",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={"duration": 28800},
    )

    user = User.objects.create_user(username="testuser_init_shield_repeat", password="test123")
    first = ensure_manor(user)
    second = ensure_manor(user)

    shield_item = InventoryItem.objects.get(
        manor=second,
        template__key="peace_shield_small",
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    second.refresh_from_db(fields=["initial_peace_shield_granted_at"])

    assert first.id == second.id
    assert shield_item.quantity == 1
    assert second.initial_peace_shield_granted_at is not None


@pytest.mark.django_db
def test_ensure_manor_initial_peace_shield_runtime_marker_error_bubbles_up(monkeypatch):
    ItemTemplate.objects.create(
        key="peace_shield_small",
        name="免战牌·小",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={"duration": 28800},
    )

    from gameplay.services.inventory.core import add_item_to_inventory_locked as original_add_item

    call_state = {"count": 0}

    def flaky_add_item(*args, **kwargs):
        item_key = args[1] if len(args) > 1 else kwargs.get("item_key")
        if item_key == "peace_shield_small":
            call_state["count"] += 1
        if item_key == "peace_shield_small" and call_state["count"] == 1:
            raise RuntimeError("temporary inventory failure")
        return original_add_item(*args, **kwargs)

    monkeypatch.setattr("gameplay.services.inventory.core.add_item_to_inventory_locked", flaky_add_item)

    user = User(username="testuser_init_shield_retry")
    user.set_password("test123")
    User.objects.bulk_create([user])
    user = User.objects.get(username="testuser_init_shield_retry")

    with pytest.raises(RuntimeError, match="temporary inventory failure"):
        ensure_manor(user)

    first = Manor.objects.get(user=user)
    first.refresh_from_db(fields=["initial_peace_shield_granted_at"])
    assert first.initial_peace_shield_granted_at is None
    assert not InventoryItem.objects.filter(manor=first, template__key="peace_shield_small").exists()

    second = ensure_manor(user)
    second.refresh_from_db(fields=["initial_peace_shield_granted_at"])
    shield_item = InventoryItem.objects.get(
        manor=second,
        template__key="peace_shield_small",
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    assert call_state["count"] >= 2
    assert second.initial_peace_shield_granted_at is not None
    assert shield_item.quantity == 1


@pytest.mark.django_db
def test_ensure_manor_returns_existing():
    user = User.objects.create_user(username="testuser", password="test123")
    manor1 = ensure_manor(user)
    manor2 = ensure_manor(user)

    assert manor1.id == manor2.id


@pytest.mark.django_db
def test_ensure_manor_raises_name_conflict_for_duplicate_initial_name():
    user_a = User(username="manor_name_conflict_user_a")
    user_a.set_password("test123")
    user_b = User(username="manor_name_conflict_user_b")
    user_b.set_password("test123")
    User.objects.bulk_create([user_a, user_b])

    user_a = User.objects.get(username="manor_name_conflict_user_a")
    user_b = User.objects.get(username="manor_name_conflict_user_b")
    ensure_manor(user_a, initial_name="冲突庄园名测试")

    with pytest.raises(manor_service.ManorNameConflictError, match="该庄园名称已被使用"):
        ensure_manor(user_b, initial_name="冲突庄园名测试")


@pytest.mark.django_db
def test_deliver_active_global_mail_campaigns_skips_missing_schema(monkeypatch, caplog):
    user = User(username="global_mail_schema_missing_user")
    user.set_password("test123")
    User.objects.bulk_create([user])
    user = User.objects.get(username="global_mail_schema_missing_user")
    manor = Manor.objects.create(user=user)

    def _raise_missing_schema(_manor):
        raise ProgrammingError("Table 'webgame.gameplay_globalmailcampaign' doesn't exist")

    monkeypatch.setattr("gameplay.services.global_mail.deliver_active_global_mail_campaigns", _raise_missing_schema)

    with caplog.at_level(logging.WARNING):
        manor_service._deliver_active_global_mail_campaigns(manor)

    assert "schema is unavailable" in caplog.text


@pytest.mark.django_db
def test_deliver_active_global_mail_campaigns_runtime_marker_error_bubbles_up(monkeypatch):
    user = User(username="global_mail_runtime_user")
    user.set_password("test123")
    User.objects.bulk_create([user])
    user = User.objects.get(username="global_mail_runtime_user")
    manor = Manor.objects.create(user=user)

    monkeypatch.setattr(
        "gameplay.services.global_mail.deliver_active_global_mail_campaigns",
        lambda _manor: (_ for _ in ()).throw(RuntimeError("global mail bug")),
    )

    with pytest.raises(RuntimeError, match="global mail bug"):
        manor_service._deliver_active_global_mail_campaigns(manor)


@pytest.mark.django_db
def test_ensure_manor_shield_database_error_is_best_effort(monkeypatch):
    ItemTemplate.objects.create(
        key="peace_shield_small",
        name="免战牌·小",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={"duration": 28800},
    )

    monkeypatch.setattr(
        "gameplay.services.inventory.core.add_item_to_inventory_locked",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("db down")),
    )

    user = User.objects.create_user(username="testuser_init_shield_db_fail", password="test123")
    manor = ensure_manor(user)
    manor.refresh_from_db(fields=["initial_peace_shield_granted_at"])

    assert manor.initial_peace_shield_granted_at is None
    assert not InventoryItem.objects.filter(manor=manor, template__key="peace_shield_small").exists()


@pytest.mark.django_db
def test_ensure_manor_recovers_existing_half_initialized_manor(monkeypatch):
    user = User(username="manor_recover_user")
    user.set_password("test123")
    User.objects.bulk_create([user])
    user = User.objects.get(username="manor_recover_user")

    half_ready = Manor.objects.create(user=user, region="overseas", coordinate_x=0, coordinate_y=0)
    monkeypatch.setattr("gameplay.services.manor.core.generate_unique_coordinate", lambda _region: (456, 789))

    manor = ensure_manor(user, region="north")

    assert manor.id == half_ready.id
    assert manor.region == "north"
    assert manor.coordinate_x == 456
    assert manor.coordinate_y == 789


@pytest.mark.django_db
def test_ensure_manor_propagates_non_location_integrity_error_from_assignment(monkeypatch):
    user = User(username="manor_non_location_integrity_error_user")
    user.set_password("test123")
    User.objects.bulk_create([user])
    user = User.objects.get(username="manor_non_location_integrity_error_user")

    original_save = Manor.save
    expected_error = IntegrityError("Duplicate entry for unrelated manor constraint")

    def _patched_save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        if self.user_id == user.id and update_fields and "coordinate_x" in update_fields:
            raise expected_error
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr("gameplay.models.manor.Manor.save", _patched_save)

    with pytest.raises(IntegrityError) as exc_info:
        ensure_manor(user)

    assert exc_info.value is expected_error


@pytest.mark.django_db
def test_ensure_manor_cleans_up_half_initialized_manor_on_repeated_assignment_failure(monkeypatch):
    user = User(username="manor_cleanup_user")
    user.set_password("test123")
    User.objects.bulk_create([user])
    user = User.objects.get(username="manor_cleanup_user")

    original_save = Manor.save
    save_attempts = 0

    def _patched_save(self, *args, **kwargs):
        nonlocal save_attempts
        update_fields = kwargs.get("update_fields")
        if self.user_id == user.id and update_fields and "coordinate_x" in update_fields:
            save_attempts += 1
            raise IntegrityError(SQLITE_OCCUPIED_MANOR_LOCATION_CONFLICT)
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr("gameplay.models.manor.Manor.save", _patched_save)

    with pytest.raises(RuntimeError, match="Failed to allocate"):
        ensure_manor(user)

    assert save_attempts == 5
    assert Manor.objects.filter(user=user).exists() is False


@pytest.mark.django_db
def test_ensure_manor_cleans_up_half_initialized_manor_on_name_conflict():
    user_a = User(username="manor_cleanup_name_conflict_a")
    user_a.set_password("test123")
    user_b = User(username="manor_cleanup_name_conflict_b")
    user_b.set_password("test123")
    User.objects.bulk_create([user_a, user_b])
    user_a = User.objects.get(username="manor_cleanup_name_conflict_a")
    user_b = User.objects.get(username="manor_cleanup_name_conflict_b")

    ensure_manor(user_a, initial_name="同名冲突庄园")

    with pytest.raises(manor_service.ManorNameConflictError, match="该庄园名称已被使用"):
        ensure_manor(user_b, initial_name="同名冲突庄园")

    assert Manor.objects.filter(user=user_b).exists() is False
