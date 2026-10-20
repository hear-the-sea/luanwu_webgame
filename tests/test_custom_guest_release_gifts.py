from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from gameplay.management.commands import send_custom_guest_release_gifts as release
from gameplay.models import (
    BotProfile,
    GlobalMailCampaign,
    GlobalMailDelivery,
    InventoryItem,
    ItemTemplate,
    Manor,
    Message,
)
from gameplay.services.global_mail import deliver_active_global_mail_campaigns
from gameplay.services.utils.messages import claim_message_attachments
from guests.models import CustomGuestProgress, Guest, GuestTemplate
from guests.services.custom_guest import ensure_custom_guest_card

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def release_items():
    for key in (*release.GIFTS, release.CUSTOM_GUEST_CARD_KEY):
        ItemTemplate.objects.get_or_create(key=key, defaults={"name": key})


@pytest.fixture
def historical_manor_factory(manor_factory):
    def create():
        manor, user = manor_factory()
        # 上线礼包针对历史玩家；移除新版初始化赠送，保留真实发放逻辑。
        InventoryItem.objects.filter(manor=manor, template__key="custom_guest_card").delete()
        CustomGuestProgress.objects.filter(manor=manor).delete()
        return manor, user

    return create


def run(cutoff, *, apply=False):
    output = StringIO()
    call_command("send_custom_guest_release_gifts", cutoff=cutoff.isoformat(), apply=apply, stdout=output)
    return output.getvalue()


def test_default_dry_run_has_no_writes_and_preflights_all_items(historical_manor_factory):
    manor, _ = historical_manor_factory()
    cutoff = timezone.now()
    assert "DRY RUN" in run(cutoff)
    assert not GlobalMailCampaign.objects.filter(key=release.CAMPAIGN_KEY).exists()
    assert not CustomGuestProgress.objects.filter(manor=manor).exists()
    ItemTemplate.objects.filter(key="yijinjing").delete()
    with pytest.raises(CommandError, match="缺少道具配置"):
        run(cutoff, apply=True)
    assert not GlobalMailCampaign.objects.filter(key=release.CAMPAIGN_KEY).exists()


def test_missing_card_gets_bound_gifts_once_and_login_does_not_duplicate(historical_manor_factory):
    manor, _ = historical_manor_factory()
    cutoff = timezone.now()
    assert "已发=1" in run(cutoff, apply=True)
    message = Message.objects.get(manor=manor, title=release.TITLE)
    assert message.attachments == {"bound_items": {"yijinjing": 500, "xisuidan": 15, "custom_guest_card": 1}}
    assert message.is_deletion_protected
    assert CustomGuestProgress.objects.get(manor=manor).card_issued
    assert not ensure_custom_guest_card(manor)
    assert not InventoryItem.objects.filter(manor=manor, template__key="custom_guest_card").exists()
    assert "已发=0" in run(cutoff, apply=True)
    claim_message_attachments(message)
    message.refresh_from_db()
    for key, quantity in message.attachments["claimed"]["bound_items"].items():
        item = InventoryItem.objects.get(manor=manor, template__key=key)
        assert item.quantity == item.bound_quantity == quantity
    assert "已发=0" in run(cutoff, apply=True)
    assert GlobalMailDelivery.objects.filter(manor=manor).count() == 1


@pytest.mark.parametrize("ownership", ["inventory", "guest", "pending_bound", "pending_unbound"])
def test_existing_card_guest_or_unclaimed_card_mail_does_not_get_another(historical_manor_factory, ownership):
    manor, _ = historical_manor_factory()
    if ownership == "inventory":
        InventoryItem.objects.create(
            manor=manor, template=ItemTemplate.objects.get(key="custom_guest_card"), quantity=1, bound_quantity=1
        )
    elif ownership == "guest":
        Guest.objects.create(manor=manor, template=GuestTemplate.objects.get(key="custom_guest_green"))
    else:
        bucket = "bound_items" if ownership == "pending_bound" else "items"
        Message.objects.create(manor=manor, title="未领卡片", attachments={bucket: {"custom_guest_card": 1}})
    run(timezone.now(), apply=True)
    message = Message.objects.get(manor=manor, title=release.TITLE)
    assert message.attachments == {"bound_items": release.GIFTS}
    assert CustomGuestProgress.objects.get(manor=manor).card_issued
    assert not ensure_custom_guest_card(manor)


def test_new_player_initial_card_is_not_duplicated_by_release_gifts(manor_factory):
    manor, _ = manor_factory()
    card = InventoryItem.objects.get(manor=manor, template__key="custom_guest_card")
    assert (card.quantity, card.bound_quantity) == (1, 1)
    run(timezone.now(), apply=True)
    message = Message.objects.get(manor=manor, title=release.TITLE)
    assert message.attachments == {"bound_items": release.GIFTS}
    claim_message_attachments(message)
    assert not ensure_custom_guest_card(manor)
    card.refresh_from_db()
    assert (card.quantity, card.bound_quantity) == (1, 1)
    assert InventoryItem.objects.filter(manor=manor, template__key="custom_guest_card").count() == 1


def test_cutoff_virtual_and_later_accounts_are_excluded_and_campaign_stays_inactive(manor_factory):
    real, _ = manor_factory()
    virtual, _ = manor_factory()
    now = timezone.now()
    BotProfile.objects.create(
        manor=virtual,
        prestige_band="newbie",
        growth_seed=992,
        next_growth_at=now,
        abandon_at=now + timedelta(days=1),
        retire_at=now + timedelta(days=2),
    )
    cutoff = timezone.now()
    later, _ = manor_factory()
    run(cutoff, apply=True)
    assert list(GlobalMailDelivery.objects.values_list("manor_id", flat=True)) == [real.pk]
    campaign = GlobalMailCampaign.objects.get(key=release.CAMPAIGN_KEY)
    assert not campaign.is_active
    assert deliver_active_global_mail_campaigns(later) == 0
    # 即使之后人为回填了创建时间，首次执行的 ID 快照也不会扩大发送范围。
    later.user.date_joined = cutoff - timedelta(days=1)
    later.user.save(update_fields=["date_joined"])
    later.created_at = cutoff - timedelta(days=1)
    later.save(update_fields=["created_at"])
    assert "已发=0" in run(cutoff, apply=True)
    assert not GlobalMailDelivery.objects.filter(manor=later).exists()
    with pytest.raises(CommandError, match="截止时间不一致"):
        run(cutoff + timedelta(microseconds=1), apply=True)


def test_failed_manor_rolls_back_card_marker_and_retries_without_duplicates(historical_manor_factory, monkeypatch):
    first, _ = historical_manor_factory()
    second, _ = historical_manor_factory()
    cutoff = timezone.now()
    original = release.create_message

    def fail_first(manor, *args, **kwargs):
        if manor.pk == first.pk:
            raise RuntimeError("injected mail failure")
        return original(manor, *args, **kwargs)

    monkeypatch.setattr(release, "create_message", fail_first)
    with pytest.raises(CommandError, match="1 个庄园未发放成功"):
        run(cutoff, apply=True)
    assert not CustomGuestProgress.objects.filter(manor=first).exists()
    assert not Message.objects.filter(manor=first, title=release.TITLE).exists()
    assert GlobalMailDelivery.objects.filter(manor=second).count() == 1
    monkeypatch.setattr(release, "create_message", original)
    assert "已发=1" in run(cutoff, apply=True)
    assert GlobalMailDelivery.objects.count() == 2
    assert Message.objects.filter(title=release.TITLE).count() == 2


def test_account_without_manor_is_not_bootstrapped(django_user_model):
    # 绕过注册信号，构造历史遗留的无庄园账号。
    user = django_user_model.objects.bulk_create([django_user_model(username="release_without_manor")])[0]
    assert not Manor.objects.filter(user=user).exists()
    assert "已发=0" in run(timezone.now(), apply=True)
    assert not Manor.objects.filter(user=user).exists()
    assert not GlobalMailDelivery.objects.exists()


@pytest.mark.parametrize("cutoff", ["invalid", "2026-10-18T12:00:00", "9999-01-01T00:00:00Z"])
def test_invalid_cutoff_refuses_without_writes(cutoff):
    with pytest.raises(CommandError, match="--cutoff"):
        call_command("send_custom_guest_release_gifts", cutoff=cutoff, apply=True)
    assert not GlobalMailCampaign.objects.filter(key=release.CAMPAIGN_KEY).exists()
