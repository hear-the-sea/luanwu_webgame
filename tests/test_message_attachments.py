from dataclasses import FrozenInstanceError
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models.deletion import ProtectedError
from django.db.models.query import QuerySet
from django.utils import timezone
from django_redis.exceptions import ConnectionInterrupted

from core.exceptions import NoAttachmentError
from gameplay.models import InventoryItem, ItemTemplate, Message, ResourceEvent, ResourceType
from gameplay.services.manor.core import ensure_manor
from gameplay.services.utils import messages as message_service
from gameplay.services.utils.cache import CacheKeys
from gameplay.services.utils.messages import (
    MessageDeleteResult,
    bulk_create_messages,
    claim_message_attachments,
    cleanup_old_messages,
    create_message,
    delete_all_messages,
    delete_expired_messages,
    delete_messages,
    unread_message_count,
)
from tests.gameplay_services.support import ensure_grain_template

User = get_user_model()


def test_delete_message_queryset_preserves_non_default_database_alias(monkeypatch):
    atomic_aliases = []
    deleted_queryset_aliases = []

    class AtomicContext:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

    class TransactionProbe:
        @staticmethod
        def atomic(*, using=None):
            atomic_aliases.append(using)
            return AtomicContext()

    class MessageRow:
        id = 7
        manor_id = 11
        has_protected_attachments = False
        is_deletion_protected = False

    class SourceQuerySet:
        db = "archive"

        def __init__(self, *, locked=False, filters=None):
            self.locked = locked
            self.filters = filters or {}

        def _clone(self, **changes):
            return SourceQuerySet(
                locked=changes.get("locked", self.locked),
                filters=changes.get("filters", self.filters),
            )

        def filter(self, **filters):
            return self._clone(filters={**self.filters, **filters})

        def order_by(self, *_fields):
            return self

        def values_list(self, *_fields, **_kwargs):
            return [MessageRow.id]

        def select_for_update(self):
            return self._clone(locked=True)

        def only(self, *_fields):
            return self

        def __iter__(self):
            if self.locked and MessageRow.id in self.filters.get("id__in", ()):
                return iter([MessageRow()])
            return iter(())

    class DeleteQuerySet:
        def __init__(self, database_alias):
            self.db = database_alias

        def filter(self, **_filters):
            return self

        def order_by(self, *_fields):
            return self

        def delete(self):
            deleted_queryset_aliases.append(self.db)
            return 1, {"gameplay.Message": 1}

    class MessageManager:
        def using(self, database_alias):
            return DeleteQuerySet(database_alias)

        def filter(self, **_filters):
            return DeleteQuerySet("default")

    class MessageModel:
        objects = MessageManager()

        class _meta:
            label = "gameplay.Message"

    monkeypatch.setattr(message_service, "transaction", TransactionProbe)
    monkeypatch.setattr(message_service, "Message", MessageModel)
    monkeypatch.setattr(message_service, "_invalidate_unread_count_cache", lambda _manor_id: None)

    result = message_service.delete_message_queryset(SourceQuerySet())

    assert result == MessageDeleteResult(deleted_count=1, protected_count=0)
    assert atomic_aliases == ["archive"]
    assert deleted_queryset_aliases == ["archive"]


@pytest.mark.django_db
def test_message_deletion_protection_marker_syncs_on_create_and_save():
    protection_field = Message._meta.get_field("is_deletion_protected")
    assert protection_field.default is False
    assert protection_field.editable is False

    user = User.objects.create_user(username="mail_protection_marker", password="pass123")
    manor = ensure_manor(user)
    message = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="protected marker",
        attachments={"resources": {"silver": 10}},
    )

    assert message.is_deletion_protected is True
    assert Message.objects.get(pk=message.pk).is_deletion_protected is True

    message.attachments = {"metadata": {"source": "legacy"}}
    message.save(update_fields=["attachments"])
    message.refresh_from_db()
    assert message.is_deletion_protected is False

    message.attachments = {"items": {"marker_item": 1}}
    message.save(update_fields=["attachments"])
    message.refresh_from_db()
    assert message.is_deletion_protected is True

    message.is_claimed = True
    message.save(update_fields=["is_claimed"])
    message.refresh_from_db()
    assert message.is_deletion_protected is False


@pytest.mark.django_db
def test_message_save_recomputes_explicit_deletion_protection_flag_update():
    user = User.objects.create_user(username="mail_explicit_protection_flag", password="pass123")
    manor = ensure_manor(user)
    message = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="explicit protection flag",
        attachments={"items": {"protected_item": 1}},
    )
    assert message.is_deletion_protected is True

    message.is_deletion_protected = False
    message.save(update_fields=["is_deletion_protected"])

    message.refresh_from_db()
    assert message.is_deletion_protected is True


@pytest.mark.django_db
@pytest.mark.parametrize("updated_field", ["attachments", "is_claimed"])
def test_message_save_materializes_generator_update_fields_once(updated_field):
    user = User.objects.create_user(username=f"mail_generator_{updated_field}", password="pass123")
    manor = ensure_manor(user)
    initial_attachments = {"items": {"protected_item": 1}} if updated_field == "is_claimed" else {}
    message = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title=f"generator update {updated_field}",
        attachments=initial_attachments,
    )

    if updated_field == "attachments":
        message.attachments = {"resources": {"silver": 1}}
    else:
        message.is_claimed = True
    message.save(update_fields=(field for field in [updated_field]))

    message.refresh_from_db()
    if updated_field == "attachments":
        assert message.attachments == {"resources": {"silver": 1}}
        assert message.is_deletion_protected is True
    else:
        assert message.is_claimed is True
        assert message.is_deletion_protected is False


@pytest.mark.django_db
def test_message_creation_services_persist_deletion_protection_marker():
    user = User.objects.create_user(username="mail_service_marker", password="pass123")
    manor = ensure_manor(user)

    created = create_message(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="created protected marker",
        attachments={"items": {"created_item": 1}},
    )
    bulk_created = bulk_create_messages(
        [
            {
                "manor": manor,
                "kind": Message.Kind.REWARD,
                "title": "bulk protected marker",
                "attachments": {"resources": {"grain": 5}},
            },
            {
                "manor": manor,
                "kind": Message.Kind.REWARD,
                "title": "bulk claimed marker",
                "attachments": {"items": {"claimed_item": 1}},
                "is_claimed": True,
            },
            {
                "manor": manor,
                "kind": Message.Kind.SYSTEM,
                "title": "bulk metadata marker",
                "attachments": {"metadata": {"source": "legacy"}},
            },
        ]
    )

    created.refresh_from_db()
    assert created.is_deletion_protected is True
    assert [message.is_deletion_protected for message in bulk_created] == [True, False, False]
    assert list(
        Message.objects.filter(pk__in=[message.pk for message in bulk_created])
        .order_by("pk")
        .values_list("is_deletion_protected", flat=True)
    ) == [True, False, False]


@pytest.mark.django_db
def test_claim_message_attachments_records_actual_and_stores_claimed():
    user = User.objects.create_user(username="mail_user", password="pass123")
    manor = ensure_manor(user)

    manor.silver_capacity = 100
    manor.silver = 95
    manor.save(update_fields=["silver_capacity", "silver"])

    ItemTemplate.objects.create(
        key="mail_test_item",
        name="测试道具",
    )

    message = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="测试邮件",
        attachments={
            "resources": {"silver": 20},
            "items": {"mail_test_item": 3},
        },
    )

    claimed = claim_message_attachments(message)

    assert claimed["silver"] == 5
    assert claimed["item_mail_test_item"] == 3

    message.refresh_from_db()
    assert message.is_claimed is True
    assert message.is_deletion_protected is False
    assert message.is_read is True
    assert message.attachments["resources"]["silver"] == 20
    assert message.attachments["items"]["mail_test_item"] == 3
    assert message.attachments["claimed"]["resources"]["silver"] == 5
    assert message.attachments["claimed"]["items"]["mail_test_item"] == 3

    manor.refresh_from_db()
    assert manor.silver == 100

    event = ResourceEvent.objects.filter(
        manor=manor,
        resource_type=ResourceType.SILVER,
        reason=ResourceEvent.Reason.ADMIN_ADJUST,
        note="邮件附件：测试邮件",
    ).first()
    assert event is not None
    assert event.delta == 5

    assert message.get_attachment_summary() == "银两×5、1种道具"


@pytest.mark.django_db
def test_claim_message_attachments_routes_legacy_grain_item_to_warehouse_ledger():
    user = User.objects.create_user(username="mail_legacy_grain", password="pass123")
    manor = ensure_manor(user)
    grain_template = ensure_grain_template()
    manor.grain = 5
    manor.save(update_fields=["grain"])

    message = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="旧格式粮食附件",
        attachments={"items": {"grain": 7}},
    )

    claimed = claim_message_attachments(message)

    manor.refresh_from_db(fields=["grain"])
    warehouse_grain = InventoryItem.objects.get(
        manor=manor,
        template=grain_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    assert claimed["item_grain"] == 7
    assert manor.grain == 12
    assert warehouse_grain.quantity == 12


@pytest.mark.django_db
def test_claim_message_attachments_invalidates_unread_cache():
    user = User.objects.create_user(username="mail_user_cache", password="pass123")
    manor = ensure_manor(user)
    ItemTemplate.objects.create(key="mail_test_item_cache", name="测试道具")
    message = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="测试邮件缓存",
        attachments={"items": {"mail_test_item_cache": 1}},
    )

    cache_key = CacheKeys.unread_count(manor.id)
    cache.set(cache_key, 999, timeout=60)
    assert cache.get(cache_key) == 999

    claim_message_attachments(message)
    assert cache.get(cache_key) is None


@pytest.mark.django_db
def test_delete_messages_invalidates_unread_cache():
    user = User.objects.create_user(username="mail_user_del", password="pass123")
    manor = ensure_manor(user)
    msg = Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="t1")

    cache_key = CacheKeys.unread_count(manor.id)
    cache.set(cache_key, 999, timeout=60)
    delete_messages(manor, [msg.id])
    assert cache.get(cache_key) is None


@pytest.mark.django_db
def test_delete_all_messages_invalidates_unread_cache():
    user = User.objects.create_user(username="mail_user_del_all", password="pass123")
    manor = ensure_manor(user)
    Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="t2")
    Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="t3")

    cache_key = CacheKeys.unread_count(manor.id)
    cache.set(cache_key, 999, timeout=60)
    delete_all_messages(manor)
    assert cache.get(cache_key) is None


@pytest.mark.django_db
def test_cleanup_old_messages_invalidates_unread_cache_when_deleting():
    user = User.objects.create_user(username="mail_user_cleanup", password="pass123")
    manor = ensure_manor(user)
    message = Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="old_msg")
    Message.objects.filter(pk=message.pk).update(created_at=timezone.now() - timedelta(days=30))

    cache_key = CacheKeys.unread_count(manor.id)
    cache.set(cache_key, 999, timeout=60)
    assert cache.get(cache_key) == 999

    cleanup_old_messages(manor)

    assert Message.objects.filter(pk=message.pk).exists() is False
    assert cache.get(cache_key) is None


@pytest.mark.django_db
def test_cleanup_old_messages_protects_unclaimed_attachments_and_reports_counts(monkeypatch):
    user = User.objects.create_user(username="mail_user_cleanup_protected", password="pass123")
    manor = ensure_manor(user)
    protected = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="old protected reward",
        attachments={"resources": {"silver": 10}},
    )
    claimed = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="old claimed reward",
        attachments={"resources": {"silver": 10}},
        is_claimed=True,
    )
    plain = Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="old plain message")
    Message.objects.filter(pk__in=[protected.pk, claimed.pk, plain.pk]).update(
        created_at=timezone.now() - timedelta(days=30)
    )
    monkeypatch.setattr(message_service, "_safe_cache_add", lambda *_args, **_kwargs: True)

    result = cleanup_old_messages(manor)

    assert result == MessageDeleteResult(deleted_count=2, protected_count=1)
    assert Message.objects.filter(pk=protected.pk).exists() is True
    assert Message.objects.filter(pk__in=[claimed.pk, plain.pk]).exists() is False


@pytest.mark.django_db
def test_delete_messages_protects_unclaimed_attachments_and_reports_counts():
    user = User.objects.create_user(username="mail_user_delete_protected", password="pass123")
    manor = ensure_manor(user)
    protected = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="protected selected reward",
        attachments={"resources": {"silver": 10}},
    )
    claimed = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="claimed selected reward",
        attachments={"resources": {"silver": 10}},
        is_claimed=True,
    )
    plain = Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="plain selected message")

    result = delete_messages(manor, [protected.pk, claimed.pk, plain.pk])

    assert result == MessageDeleteResult(deleted_count=2, protected_count=1)
    assert Message.objects.filter(pk=protected.pk).exists() is True
    assert Message.objects.filter(pk__in=[claimed.pk, plain.pk]).exists() is False


@pytest.mark.django_db
def test_delete_all_messages_protects_unclaimed_attachments_and_reports_counts():
    user = User.objects.create_user(username="mail_user_delete_all_protected", password="pass123")
    manor = ensure_manor(user)
    protected = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="protected reward",
        attachments={"items": {"protected_item": 1}},
    )
    claimed = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="claimed reward",
        attachments={"items": {"claimed_item": 1}},
        is_claimed=True,
    )
    plain = Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="plain message")

    result = delete_all_messages(manor)

    assert result == MessageDeleteResult(deleted_count=2, protected_count=1)
    assert Message.objects.filter(pk=protected.pk).exists() is True
    assert Message.objects.filter(pk__in=[claimed.pk, plain.pk]).exists() is False


@pytest.mark.django_db
def test_delete_messages_does_not_invalidate_cache_when_all_selected_messages_are_protected():
    user = User.objects.create_user(username="mail_user_delete_protected_cache", password="pass123")
    manor = ensure_manor(user)
    protected = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="protected cached reward",
        attachments={"resources": {"silver": 10}},
    )
    cache_key = CacheKeys.unread_count(manor.id)
    cache.set(cache_key, 999, timeout=60)

    result = delete_messages(manor, [protected.pk])

    assert result == MessageDeleteResult(deleted_count=0, protected_count=1)
    assert cache.get(cache_key) == 999
    assert Message.objects.filter(pk=protected.pk).exists() is True


@pytest.mark.django_db
@pytest.mark.parametrize(
    "attachments",
    [
        {"metadata": {"source": "legacy"}},
        {"items": {}, "resources": {}},
        {"items": [], "resources": None},
        ["legacy-payload"],
    ],
    ids=["metadata-only", "empty-buckets", "falsey-non-dict-buckets", "top-level-list"],
)
def test_delete_messages_deletes_payloads_without_actual_attachments(attachments):
    user = User.objects.create_user(username="mail_user_non_attachment_json", password="pass123")
    manor = ensure_manor(user)
    message = Message.objects.create(
        manor=manor,
        kind=Message.Kind.SYSTEM,
        title="non attachment json",
        attachments=attachments,
    )

    result = delete_messages(manor, [message.pk])

    assert result == MessageDeleteResult(deleted_count=1, protected_count=0)
    assert Message.objects.filter(pk=message.pk).exists() is False


@pytest.mark.django_db
def test_delete_messages_classifies_claim_between_candidate_fetch_and_row_lock_once(monkeypatch):
    user = User.objects.create_user(username="mail_user_claim_delete_race", password="pass123")
    manor = ensure_manor(user)
    message = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="claim delete race",
        attachments={"resources": {"silver": 10}},
    )
    original_values_list = QuerySet.values_list
    original_select_for_update = QuerySet.select_for_update
    candidate_ids_fetched = False
    claim_injected = False

    def tracking_values_list(queryset, *fields, **kwargs):
        nonlocal candidate_ids_fetched
        if queryset.model is Message and fields == ("id",):
            candidate_ids_fetched = True
        return original_values_list(queryset, *fields, **kwargs)

    def select_for_update_after_claim(queryset, *args, **kwargs):
        nonlocal claim_injected
        if not claim_injected and queryset.model is Message:
            assert candidate_ids_fetched is True
            claim_injected = True
            Message.objects.filter(pk=message.pk).update(is_claimed=True)
        return original_select_for_update(queryset, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "values_list", tracking_values_list)
    monkeypatch.setattr(QuerySet, "select_for_update", select_for_update_after_claim)

    result = delete_messages(manor, [message.pk])

    assert claim_injected is True
    assert result == MessageDeleteResult(deleted_count=1, protected_count=0)
    assert Message.objects.filter(pk=message.pk).exists() is False


@pytest.mark.django_db
def test_delete_messages_uses_one_batch_lock_query_for_prevalidated_rows(monkeypatch):
    user = User.objects.create_user(username="mail_batch_lock_query", password="pass123")
    manor = ensure_manor(user)
    messages = [
        Message.objects.create(
            manor=manor,
            kind=Message.Kind.SYSTEM,
            title=f"batch lock message {index}",
        )
        for index in range(3)
    ]
    select_for_update_calls = []
    original_select_for_update = QuerySet.select_for_update

    def tracking_select_for_update(queryset, *args, **kwargs):
        if queryset.model is Message:
            select_for_update_calls.append(queryset.db)
        return original_select_for_update(queryset, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "select_for_update", tracking_select_for_update)

    result = delete_messages(manor, [message.pk for message in messages])

    assert result == MessageDeleteResult(deleted_count=3, protected_count=0)
    assert select_for_update_calls == ["default"]


@pytest.mark.django_db
def test_delete_message_queryset_revalidates_after_callback_before_signal_bypass():
    user = User.objects.create_user(username="mail_callback_revalidation", password="pass123")
    manor = ensure_manor(user)
    message = Message.objects.create(
        manor=manor,
        kind=Message.Kind.SYSTEM,
        title="callback revalidation target",
    )
    audit_title = "callback audit side effect"

    def protect_after_initial_review(eligible_queryset):
        eligible_queryset.update(
            attachments={"resources": {"silver": 1}},
            is_claimed=False,
            is_deletion_protected=False,
        )
        Message.objects.using(eligible_queryset.db).create(
            manor=manor,
            kind=Message.Kind.SYSTEM,
            title=audit_title,
        )

    with pytest.raises(ProtectedError, match="未领取附件消息"):
        message_service.delete_message_queryset(
            Message.objects.filter(pk=message.pk),
            before_delete_batch=protect_after_initial_review,
        )

    message.refresh_from_db()
    assert message.attachments == {}
    assert message.is_deletion_protected is False
    assert Message.objects.filter(title=audit_title).exists() is False


@pytest.mark.django_db
def test_delete_expired_messages_reconciles_stale_markers_and_counts_all_protected_rows():
    user = User.objects.create_user(username="mail_user_reconcile_protection", password="pass123")
    manor = ensure_manor(user)
    protected = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="protected marker true",
        attachments={"resources": {"silver": 1}},
    )
    stale_true_claimed = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="stale true claimed",
        attachments={"resources": {"silver": 1}},
        is_claimed=True,
    )
    stale_true_plain = Message.objects.create(
        manor=manor,
        kind=Message.Kind.SYSTEM,
        title="stale true plain",
    )
    stale_false_protected = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="stale false protected",
        attachments={"items": {"protected_item": 1}},
    )
    all_message_ids = [
        protected.pk,
        stale_true_claimed.pk,
        stale_true_plain.pk,
        stale_false_protected.pk,
    ]
    Message.objects.filter(pk__in=all_message_ids).update(created_at=timezone.now() - timedelta(days=30))
    Message.objects.filter(pk__in=[stale_true_claimed.pk, stale_true_plain.pk]).update(is_deletion_protected=True)
    Message.objects.filter(pk=stale_false_protected.pk).update(is_deletion_protected=False)

    result = delete_expired_messages(timezone.now() - timedelta(days=7), batch_size=1)

    assert result == MessageDeleteResult(deleted_count=2, protected_count=2)
    assert Message.objects.filter(pk__in=[protected.pk, stale_false_protected.pk]).count() == 2
    assert Message.objects.filter(pk__in=[stale_true_claimed.pk, stale_true_plain.pk]).exists() is False
    assert set(
        Message.objects.filter(pk__in=[protected.pk, stale_false_protected.pk]).values_list(
            "is_deletion_protected", flat=True
        )
    ) == {True}

    second_result = delete_expired_messages(timezone.now() - timedelta(days=7), batch_size=1)
    assert second_result == MessageDeleteResult(deleted_count=0, protected_count=2)


@pytest.mark.django_db
def test_delete_messages_dynamically_protects_stale_false_marker_under_row_lock(monkeypatch):
    user = User.objects.create_user(username="mail_user_stale_false_marker", password="pass123")
    manor = ensure_manor(user)
    protected = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="stale false protected reward",
        attachments={"items": {"protected_item": 1}},
    )
    Message.objects.filter(pk=protected.pk).update(is_deletion_protected=False)
    select_for_update_calls = 0
    original_select_for_update = QuerySet.select_for_update

    def _track_select_for_update(queryset, *args, **kwargs):
        nonlocal select_for_update_calls
        if queryset.model is Message:
            select_for_update_calls += 1
        return original_select_for_update(queryset, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "select_for_update", _track_select_for_update)

    result = delete_messages(manor, [protected.pk])

    protected.refresh_from_db()
    assert select_for_update_calls == 1
    assert result == MessageDeleteResult(deleted_count=0, protected_count=1)
    assert protected.is_deletion_protected is True
    assert Message.objects.filter(pk=protected.pk).exists() is True


@pytest.mark.django_db
def test_delete_expired_messages_dynamically_protects_stale_false_marker_under_row_lock(monkeypatch):
    user = User.objects.create_user(username="mail_expired_stale_false_marker", password="pass123")
    manor = ensure_manor(user)
    protected = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="expired stale false protected reward",
        attachments={"resources": {"silver": 10}},
    )
    Message.objects.filter(pk=protected.pk).update(
        created_at=timezone.now() - timedelta(days=30),
        is_deletion_protected=False,
    )
    select_for_update_calls = 0
    original_select_for_update = QuerySet.select_for_update

    def _track_select_for_update(queryset, *args, **kwargs):
        nonlocal select_for_update_calls
        if queryset.model is Message:
            select_for_update_calls += 1
        return original_select_for_update(queryset, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "select_for_update", _track_select_for_update)

    result = delete_expired_messages(timezone.now() - timedelta(days=7), batch_size=1)

    protected.refresh_from_db()
    assert select_for_update_calls == 1
    assert result == MessageDeleteResult(deleted_count=0, protected_count=1)
    assert protected.is_deletion_protected is True
    assert Message.objects.filter(pk=protected.pk).exists() is True


@pytest.mark.django_db
@pytest.mark.parametrize("attachments", [["legacy"], "legacy", 7])
def test_message_non_object_attachments_are_safe_and_empty(attachments):
    user = User.objects.create_user(username="mail_user_non_object_attachments", password="pass123")
    manor = ensure_manor(user)
    message = Message.objects.create(
        manor=manor,
        kind=Message.Kind.SYSTEM,
        title="non object attachments",
        attachments=attachments,
    )

    assert message.has_attachments is False
    assert message.get_attachment_summary() == ""


@pytest.mark.django_db
@pytest.mark.parametrize(
    "attachments",
    [
        {"items": "legacy"},
        {"resources": ["legacy"]},
    ],
)
def test_nested_non_object_attachment_buckets_are_not_assets_and_can_be_deleted(attachments):
    user = User.objects.create_user(username="mail_user_dirty_attachment_bucket", password="pass123")
    manor = ensure_manor(user)
    message = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="dirty attachment bucket",
        attachments=attachments,
    )

    assert message.has_attachments is False
    assert message.get_attachment_summary() == ""
    with pytest.raises(NoAttachmentError):
        claim_message_attachments(message)

    result = delete_messages(manor, [message.pk])

    assert result.deleted_count == 1
    assert result.protected_count == 0
    assert Message.objects.filter(pk=message.pk).exists() is False


@pytest.mark.django_db
def test_delete_messages_returns_message_delete_result_dataclass():
    user = User.objects.create_user(username="mail_user_delete_result", password="pass123")
    manor = ensure_manor(user)
    message = Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="delete result")

    result = delete_messages(manor, [message.pk])

    assert result.__class__.__name__ == "MessageDeleteResult"
    assert result.deleted_count == 1
    assert result.protected_count == 0
    with pytest.raises(FrozenInstanceError):
        result.deleted_count = 2


@pytest.mark.django_db
def test_unread_message_count_tolerates_cache_errors(monkeypatch):
    user = User.objects.create_user(username="mail_user_cache_fail", password="pass123")
    manor = ensure_manor(user)
    Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="cache fail msg")

    monkeypatch.setattr(
        "gameplay.services.utils.messages.cache.get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionInterrupted("cache get failed")),
    )
    monkeypatch.setattr(
        "gameplay.services.utils.messages.cache.set",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionInterrupted("cache set failed")),
    )

    assert unread_message_count(manor) == 1


@pytest.mark.django_db
def test_cleanup_old_messages_tolerates_cache_add_error(monkeypatch):
    user = User.objects.create_user(username="mail_user_cleanup_cache_fail", password="pass123")
    manor = ensure_manor(user)
    message = Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="old_msg_cache_fail")
    Message.objects.filter(pk=message.pk).update(created_at=timezone.now() - timedelta(days=30))

    monkeypatch.setattr(
        "gameplay.services.utils.messages.cache.add",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionInterrupted("cache add failed")),
    )
    message_service._LOCAL_CLEANUP_FALLBACK.clear()

    cleanup_old_messages(manor)
    assert Message.objects.filter(pk=message.pk).exists() is False


@pytest.mark.django_db
def test_cleanup_old_messages_cache_add_error_uses_local_fallback_gate(monkeypatch):
    user = User.objects.create_user(username="mail_user_cleanup_gate", password="pass123")
    manor = ensure_manor(user)

    first_message = Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="old_msg_first")
    Message.objects.filter(pk=first_message.pk).update(created_at=timezone.now() - timedelta(days=30))

    monkeypatch.setattr(
        "gameplay.services.utils.messages.cache.add",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionInterrupted("cache add failed")),
    )
    message_service._LOCAL_CLEANUP_FALLBACK.clear()

    cleanup_old_messages(manor)
    assert Message.objects.filter(pk=first_message.pk).exists() is False

    second_message = Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="old_msg_second")
    Message.objects.filter(pk=second_message.pk).update(created_at=timezone.now() - timedelta(days=30))

    # 第二次应被本地节流门禁拦截，避免缓存故障时每次请求都触发清理扫描
    cleanup_old_messages(manor)
    assert Message.objects.filter(pk=second_message.pk).exists() is True


@pytest.mark.django_db
def test_unread_message_count_runtime_marker_bubbles_up(monkeypatch):
    user = User.objects.create_user(username="mail_user_cache_runtime", password="pass123")
    manor = ensure_manor(user)
    Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="cache runtime msg")

    monkeypatch.setattr(
        "gameplay.services.utils.messages.cache.get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("cache get failed")),
    )

    with pytest.raises(RuntimeError, match="cache get failed"):
        unread_message_count(manor)


@pytest.mark.django_db
def test_unread_message_count_cache_set_runtime_marker_bubbles_up(monkeypatch):
    user = User.objects.create_user(username="mail_user_cache_set_runtime", password="pass123")
    manor = ensure_manor(user)
    Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="cache set runtime msg")

    monkeypatch.setattr("gameplay.services.utils.messages.cache.get", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "gameplay.services.utils.messages.cache.set",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("cache set failed")),
    )

    with pytest.raises(RuntimeError, match="cache set failed"):
        unread_message_count(manor)


@pytest.mark.django_db
def test_delete_messages_cache_delete_runtime_marker_bubbles_up(monkeypatch):
    user = User.objects.create_user(username="mail_user_delete_runtime", password="pass123")
    manor = ensure_manor(user)
    message = Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="delete runtime msg")

    monkeypatch.setattr(
        "gameplay.services.utils.messages.cache.delete",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("cache delete failed")),
    )

    with pytest.raises(RuntimeError, match="cache delete failed"):
        delete_messages(manor, [message.id])

    assert Message.objects.filter(pk=message.pk).exists() is False


@pytest.mark.django_db
def test_bulk_create_messages_cache_delete_many_runtime_marker_bubbles_up(monkeypatch):
    user = User.objects.create_user(username="mail_user_bulk_runtime", password="pass123")
    manor = ensure_manor(user)

    monkeypatch.setattr(
        "gameplay.services.utils.messages.cache.delete_many",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("cache delete_many failed")),
    )

    with pytest.raises(RuntimeError, match="cache delete_many failed"):
        bulk_create_messages(
            [
                {"manor": manor, "kind": Message.Kind.SYSTEM, "title": "bulk runtime msg 1"},
                {"manor": manor, "kind": Message.Kind.SYSTEM, "title": "bulk runtime msg 2"},
            ]
        )

    assert Message.objects.filter(manor=manor, title__startswith="bulk runtime msg").count() == 2


def test_local_cleanup_fallback_evicts_oldest_when_oversized(monkeypatch):
    message_service._LOCAL_CLEANUP_FALLBACK.clear()
    monkeypatch.setattr(message_service, "_LOCAL_CLEANUP_FALLBACK_MAX_SIZE", 3)
    monkeypatch.setattr(message_service, "_LOCAL_CLEANUP_FALLBACK_EVICT_COUNT", 2)
    monkeypatch.setattr(message_service.time, "monotonic", lambda: 100.0)

    message_service._LOCAL_CLEANUP_FALLBACK.update(
        {
            1: 90.0,
            2: 91.0,
            3: 92.0,
            4: 93.0,
        }
    )

    allowed = message_service._allow_cleanup_via_local_fallback(5, interval_seconds=120)
    assert allowed is True
    assert 5 in message_service._LOCAL_CLEANUP_FALLBACK
    assert 1 not in message_service._LOCAL_CLEANUP_FALLBACK
    assert len(message_service._LOCAL_CLEANUP_FALLBACK) <= message_service._LOCAL_CLEANUP_FALLBACK_MAX_SIZE


def test_local_cleanup_fallback_evicts_enough_to_respect_max_size(monkeypatch):
    message_service._LOCAL_CLEANUP_FALLBACK.clear()
    monkeypatch.setattr(message_service, "_LOCAL_CLEANUP_FALLBACK_MAX_SIZE", 3)
    monkeypatch.setattr(message_service, "_LOCAL_CLEANUP_FALLBACK_EVICT_COUNT", 1)
    monkeypatch.setattr(message_service.time, "monotonic", lambda: 100.0)

    message_service._LOCAL_CLEANUP_FALLBACK.update(
        {
            1: 90.0,
            2: 91.0,
            3: 92.0,
            4: 93.0,
            5: 94.0,
        }
    )

    allowed = message_service._allow_cleanup_via_local_fallback(6, interval_seconds=120)
    assert allowed is True
    assert len(message_service._LOCAL_CLEANUP_FALLBACK) <= message_service._LOCAL_CLEANUP_FALLBACK_MAX_SIZE
