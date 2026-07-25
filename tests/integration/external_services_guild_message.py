from __future__ import annotations

import uuid

import pytest
from django.core.cache import cache

from gameplay.models import InventoryItem, ItemTemplate, Message, ResourceEvent, ResourceType
from gameplay.services.manor.core import ensure_manor
from gameplay.services.utils.cache import CacheKeys
from gameplay.services.utils.messages import claim_message_attachments
from guilds.constants import CONTRIBUTION_RATES, CONTRIBUTION_UNITS, GUILD_CREATION_COST
from guilds.models import GuildAnnouncement, GuildDonationLog, GuildMember, GuildResourceLog
from guilds.services.contribution import donate_resource
from guilds.services.guild import create_guild
from guilds.services.member import apply_to_guild, approve_application

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("load_guest_data", "load_troop_data")]


@pytest.mark.django_db(transaction=True)
def test_integration_guild_application_approval_and_donation_flow(require_env_services, django_user_model):
    founder_user = django_user_model.objects.create_user(
        username=f"intg_guild_founder_{uuid.uuid4().hex[:8]}",
        password="pass123",
    )
    applicant_user = django_user_model.objects.create_user(
        username=f"intg_guild_member_{uuid.uuid4().hex[:8]}",
        password="pass123",
    )
    founder_manor = ensure_manor(founder_user)
    applicant_manor = ensure_manor(applicant_user)
    applicant_manor.silver = 10_000
    applicant_manor.save(update_fields=["silver"])

    gold_bar_tpl, _ = ItemTemplate.objects.get_or_create(
        key="gold_bar",
        defaults={
            "name": "金条",
            "effect_type": ItemTemplate.EffectType.TOOL,
            "is_usable": False,
            "tradeable": False,
        },
    )
    InventoryItem.objects.update_or_create(
        manor=founder_manor,
        template=gold_bar_tpl,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        defaults={"quantity": GUILD_CREATION_COST["gold_bar"] + 2},
    )

    guild = create_guild(founder_user, name=f"帮测{uuid.uuid4().hex[:6]}", description="integration guild")
    application = apply_to_guild(applicant_user, guild, "求加入")
    approve_application(application, founder_user)

    applicant_member = GuildMember.objects.get(user=applicant_user)
    donation_amount = CONTRIBUTION_UNITS["silver"]
    donate_resource(applicant_member, "silver", donation_amount)

    guild.refresh_from_db()
    applicant_member.refresh_from_db()
    application.refresh_from_db()

    assert application.status == "approved"
    assert applicant_member.is_active is True
    assert applicant_member.current_contribution == CONTRIBUTION_RATES["silver"]
    assert guild.silver == donation_amount
    assert Message.objects.filter(manor=applicant_manor, title="入帮申请通过").exists()
    assert GuildAnnouncement.objects.filter(guild=guild, content__contains=applicant_manor.display_name).exists()
    assert GuildDonationLog.objects.filter(
        guild=guild,
        member=applicant_member,
        resource_type="silver",
        amount=donation_amount,
    ).exists()
    assert GuildResourceLog.objects.filter(
        guild=guild,
        action="donation",
        silver_change=donation_amount,
        related_user=applicant_user,
    ).exists()
    assert ResourceEvent.objects.filter(
        manor=applicant_manor,
        resource_type=ResourceType.SILVER,
        reason=ResourceEvent.Reason.GUILD_DONATION,
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_integration_message_attachment_claim_flow(require_env_services, django_user_model):
    user = django_user_model.objects.create_user(
        username=f"intg_mail_{uuid.uuid4().hex[:8]}",
        password="pass123",
    )
    manor = ensure_manor(user)

    item_key = f"intg_mail_item_{uuid.uuid4().hex[:8]}"
    ItemTemplate.objects.create(
        key=item_key,
        name="集成测试邮件道具",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=False,
        tradeable=False,
    )

    message = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="集成测试邮件",
        attachments={
            "resources": {ResourceType.SILVER: 50},
            "items": {item_key: 2},
        },
    )

    cache_key = CacheKeys.unread_count(manor.id)
    cache.set(cache_key, 999, timeout=30)

    claimed = claim_message_attachments(message)

    message.refresh_from_db()
    manor.refresh_from_db()
    item = InventoryItem.objects.get(
        manor=manor,
        template__key=item_key,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    assert claimed["silver"] == 50
    assert claimed[f"item_{item_key}"] == 2
    assert message.is_claimed is True
    assert message.is_read is True
    assert manor.silver >= 50
    assert item.quantity == 2
    assert cache.get(cache_key) is None

    event_exists = ResourceEvent.objects.filter(
        manor=manor,
        resource_type=ResourceType.SILVER,
        reason=ResourceEvent.Reason.ADMIN_ADJUST,
        note="邮件附件：集成测试邮件",
    ).exists()
    assert event_exists is True
