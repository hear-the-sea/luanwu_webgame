from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from gameplay.models import GlobalMailCampaign, GlobalMailDelivery, Manor
from gameplay.services.utils.messages import create_message
from gameplay.services.virtual_player_core.selectors import is_bot_manor

logger = logging.getLogger(__name__)


def get_active_global_mail_campaigns(now=None):
    current_time = now or timezone.now()
    return (
        GlobalMailCampaign.objects.filter(is_active=True)
        .filter(Q(start_at__isnull=True) | Q(start_at__lte=current_time))
        .filter(Q(end_at__isnull=True) | Q(end_at__gte=current_time))
        .order_by("id")
    )


def deliver_campaign_to_manor(campaign: GlobalMailCampaign, manor: Manor, *, now=None) -> bool:
    """
    给单个庄园投递活动邮件（幂等）。

    Returns:
        True: 本次新投递成功
        False: 已投递过或活动当前无效
    """
    current_time = now or timezone.now()
    if is_bot_manor(manor):
        return False
    if not campaign.is_active_at(current_time):
        return False

    attachments = campaign.attachments if isinstance(campaign.attachments, dict) else {}

    with transaction.atomic():
        delivery, created = GlobalMailDelivery.objects.select_for_update().get_or_create(campaign=campaign, manor=manor)
        if not created:
            return False

        message = create_message(
            manor=manor,
            kind=campaign.kind,
            title=campaign.title,
            body=campaign.body,
            attachments=attachments or None,
        )
        delivery.message = message
        delivery.save(update_fields=["message"])

    return True


def deliver_active_global_mail_campaigns(manor: Manor, *, now=None) -> int:
    """给真实玩家庄园投递当前所有有效活动邮件。"""
    if is_bot_manor(manor):
        return 0

    delivered_count = 0
    current_time = now or timezone.now()
    for campaign in get_active_global_mail_campaigns(current_time):
        if deliver_campaign_to_manor(campaign, manor, now=current_time):
            delivered_count += 1
    return delivered_count


def backfill_campaign_to_existing_manors(campaign: GlobalMailCampaign, *, now=None) -> int:
    """
    为指定活动补发给现有真实玩家庄园（幂等，不会重复发）。
    """
    delivered_count = 0
    current_time = now or timezone.now()
    if not campaign.is_active_at(current_time):
        return 0

    for manor in Manor.objects.filter(bot_profile__isnull=True).only("id").iterator(chunk_size=500):
        if deliver_campaign_to_manor(campaign, manor, now=current_time):
            delivered_count += 1
    return delivered_count
