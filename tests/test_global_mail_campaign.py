from datetime import timedelta

import pytest
from django.utils import timezone

from gameplay.models import BotProfile, GlobalMailCampaign, GlobalMailDelivery, Message
from gameplay.services.global_mail import backfill_campaign_to_existing_manors, deliver_active_global_mail_campaigns
from gameplay.services.manor.core import ensure_manor


@pytest.mark.django_db
def test_backfill_campaign_to_existing_manors_is_idempotent(django_user_model):
    user_a = django_user_model.objects.create_user(username="global_mail_user_a", password="pass123")
    user_b = django_user_model.objects.create_user(username="global_mail_user_b", password="pass123")
    manor_a = ensure_manor(user_a)
    manor_b = ensure_manor(user_b)

    campaign = GlobalMailCampaign.objects.create(
        key="new_year_mail",
        kind=Message.Kind.REWARD,
        title="新年奖励",
        body="全服发放新年奖励",
        attachments={"resources": {"silver": 1000}, "items": {"peace_shield_small": 1}},
        is_active=True,
    )

    first_delivered = backfill_campaign_to_existing_manors(campaign)
    second_delivered = backfill_campaign_to_existing_manors(campaign)

    assert first_delivered == 2
    assert second_delivered == 0
    assert GlobalMailDelivery.objects.filter(campaign=campaign).count() == 2
    assert GlobalMailDelivery.objects.filter(campaign=campaign, manor=manor_a).exists()
    assert GlobalMailDelivery.objects.filter(campaign=campaign, manor=manor_b).exists()
    assert Message.objects.filter(title="新年奖励").count() == 2


@pytest.mark.django_db
def test_backfill_campaign_skips_virtual_player_manors(django_user_model):
    real_user = django_user_model.objects.create_user(username="global_mail_real_user", password="pass123")
    virtual_user = django_user_model.objects.create_user(username="global_mail_virtual_user", password="pass123")
    real_manor = ensure_manor(real_user)
    virtual_manor = ensure_manor(virtual_user)
    now = timezone.now()
    BotProfile.objects.create(
        manor=virtual_manor,
        prestige_band="newbie",
        growth_seed=992,
        next_growth_at=now,
        abandon_at=now + timedelta(days=1),
        retire_at=now + timedelta(days=2),
    )
    campaign = GlobalMailCampaign.objects.create(
        key="real_player_only_mail",
        kind=Message.Kind.REWARD,
        title="真实玩家活动奖励",
        body="不发送给虚拟玩家",
        attachments={"resources": {"silver": 100}},
        is_active=True,
    )

    assert backfill_campaign_to_existing_manors(campaign) == 1
    assert GlobalMailDelivery.objects.filter(campaign=campaign, manor=real_manor).exists()
    assert GlobalMailDelivery.objects.filter(campaign=campaign, manor=virtual_manor).exists() is False
    assert Message.objects.filter(title="真实玩家活动奖励").count() == 1


@pytest.mark.django_db
def test_active_campaign_skips_virtual_player_manor(django_user_model):
    virtual_user = django_user_model.objects.create_user(username="global_mail_active_virtual", password="pass123")
    virtual_manor = ensure_manor(virtual_user)
    now = timezone.now()
    BotProfile.objects.create(
        manor=virtual_manor,
        prestige_band="newbie",
        growth_seed=993,
        next_growth_at=now,
        abandon_at=now + timedelta(days=1),
        retire_at=now + timedelta(days=2),
    )
    campaign = GlobalMailCampaign.objects.create(
        key="active_real_player_only_mail",
        kind=Message.Kind.REWARD,
        title="真实玩家即时活动奖励",
        body="虚拟玩家不接收",
        attachments={"resources": {"grain": 100}},
        is_active=True,
    )

    assert deliver_active_global_mail_campaigns(virtual_manor) == 0
    assert GlobalMailDelivery.objects.filter(campaign=campaign, manor=virtual_manor).exists() is False


@pytest.mark.django_db
def test_active_campaign_auto_delivered_for_new_manor(django_user_model):
    now = timezone.now()
    campaign = GlobalMailCampaign.objects.create(
        key="spring_mail",
        kind=Message.Kind.REWARD,
        title="春季福利",
        body="活动期间新用户也可领取",
        attachments={"resources": {"grain": 500}},
        is_active=True,
        start_at=now - timedelta(hours=1),
        end_at=now + timedelta(days=1),
    )

    user = django_user_model.objects.create_user(username="global_mail_new_user", password="pass123")
    manor = ensure_manor(user)

    assert GlobalMailDelivery.objects.filter(campaign=campaign, manor=manor).exists()
    assert Message.objects.filter(manor=manor, title="春季福利").exists()


@pytest.mark.django_db
def test_future_campaign_not_delivered_before_start(django_user_model):
    now = timezone.now()
    campaign = GlobalMailCampaign.objects.create(
        key="future_mail",
        kind=Message.Kind.SYSTEM,
        title="未来活动奖励",
        body="尚未开始",
        attachments={"resources": {"silver": 200}},
        is_active=True,
        start_at=now + timedelta(hours=6),
        end_at=now + timedelta(days=2),
    )

    user = django_user_model.objects.create_user(username="global_mail_future_user", password="pass123")
    manor = ensure_manor(user)

    assert GlobalMailDelivery.objects.filter(campaign=campaign, manor=manor).exists() is False
    assert Message.objects.filter(manor=manor, title="未来活动奖励").exists() is False
