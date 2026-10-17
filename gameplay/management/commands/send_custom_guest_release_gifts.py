"""一次性向发布截止时已存在的真实庄园发送专属门客更新赠礼。"""

import logging

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from gameplay.models import GlobalMailCampaign, GlobalMailDelivery, InventoryItem, ItemTemplate, Manor, Message
from gameplay.services.utils.messages import create_message
from guests.models import CustomGuestProgress
from guests.services.custom_guest import CUSTOM_GUEST_CARD_KEY, CUSTOM_GUEST_TEMPLATE_KEYS

logger = logging.getLogger(__name__)
CAMPAIGN_KEY = "custom-guest-release-20261018"
TITLE = "专属门客养成更新赠礼"
BODY = (
    "专属门客养成现已开放！可在门客详情中吞噬带前缀装备、升阶与强化。"
    "药庐护经、洗髓幽谷、易筋秘境可获得修行材料。"
    "奉上易筋经500本、洗髓丹15颗，祝庄主修行顺遂！"
)
GIFTS = {"yijinjing": 500, "xisuidan": 15}


def _eligible(cutoff, snapshot):
    query = Manor.objects.filter(
        bot_profile__isnull=True,
        user__date_joined__lte=cutoff,
        created_at__lte=cutoff,
        pk__lte=snapshot["max_manor_id"],
        user_id__lte=snapshot["max_user_id"],
    )
    return query.filter(pk__in=snapshot["manor_ids"]) if "manor_ids" in snapshot else query


def _has_card_or_guest(manor, progress):
    if progress and progress.guest_id:
        return True
    if manor.guests.filter(template__key__in=CUSTOM_GUEST_TEMPLATE_KEYS).exists():
        return True
    if InventoryItem.objects.filter(manor=manor, template__key=CUSTOM_GUEST_CARD_KEY, quantity__gt=0).exists():
        return True
    # 尚未领取的旧邮件也属于已有卡，避免补发第二张。
    for attachments in Message.objects.filter(manor=manor, is_claimed=False).values_list("attachments", flat=True):
        if not isinstance(attachments, dict):
            continue
        for bucket in ("items", "bound_items"):
            items = attachments.get(bucket)
            if isinstance(items, dict):
                try:
                    if int(items.get(CUSTOM_GUEST_CARD_KEY, 0)) > 0:
                        return True
                except (TypeError, ValueError):
                    continue
    return False


def _deliver(manor_id, campaign, cutoff, snapshot):
    with transaction.atomic():
        manor = _eligible(cutoff, snapshot).select_for_update().filter(pk=manor_id).first()
        if manor is None or GlobalMailDelivery.objects.filter(campaign=campaign, manor=manor).exists():
            return False
        progress, _ = CustomGuestProgress.objects.select_for_update().get_or_create(manor=manor)
        gifts = dict(GIFTS)
        body = BODY
        if not _has_card_or_guest(manor, progress):
            gifts[CUSTOM_GUEST_CARD_KEY] = 1
            body += "另附自定义门客卡1张，可在仓库使用并设定门客姓名与形象。"
        else:
            body += "已有自定义门客卡的庄主，可在仓库使用；已有专属门客可直接开始培养。"
        # 与附件和投递记录同事务，未领取邮件时登录也不会再次自动发卡。
        if not progress.card_issued:
            progress.card_issued = True
            progress.save(update_fields=["card_issued"])
        message = create_message(manor, Message.Kind.REWARD, TITLE, body=body, attachments={"bound_items": gifts})
        GlobalMailDelivery.objects.create(campaign=campaign, manor=manor, message=message)
        return True


class Command(BaseCommand):
    help = "专属门客更新一次性赠礼；默认仅预览，--apply 才写入。"

    def add_arguments(self, parser):
        parser.add_argument("--cutoff", required=True, help="发布截止时间，ISO 8601 格式且必须带时区")
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--apply", action="store_true")
        mode.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        try:
            cutoff = parse_datetime(options["cutoff"])
        except (TypeError, ValueError):
            cutoff = None
        if cutoff is None or timezone.is_naive(cutoff) or cutoff > timezone.now():
            raise CommandError("--cutoff 必须是带时区且不晚于当前时间的 ISO 8601 日期时间")
        required = {*GIFTS, CUSTOM_GUEST_CARD_KEY}
        missing = required - set(ItemTemplate.objects.filter(key__in=required).values_list("key", flat=True))
        if missing:
            raise CommandError(f"缺少道具配置，未发放：{', '.join(sorted(missing))}")
        snapshot = {
            "cutoff": cutoff.isoformat(),
            "max_manor_id": Manor.objects.aggregate(value=Max("pk"))["value"] or 0,
            "max_user_id": Manor.objects.aggregate(value=Max("user_id"))["value"] or 0,
        }
        snapshot["manor_ids"] = list(_eligible(cutoff, snapshot).order_by("pk").values_list("pk", flat=True))
        campaign = GlobalMailCampaign.objects.filter(key=CAMPAIGN_KEY).first()
        if options["apply"]:
            with transaction.atomic():
                campaign, _ = GlobalMailCampaign.objects.get_or_create(
                    key=CAMPAIGN_KEY,
                    defaults={
                        "title": TITLE,
                        "body": BODY,
                        "attachments": {"bound_items": GIFTS, "release_snapshot": snapshot},
                        "is_active": False,
                    },
                )
        if campaign:
            saved = campaign.attachments.get("release_snapshot") if isinstance(campaign.attachments, dict) else None
            if (
                campaign.is_active
                or not isinstance(saved, dict)
                or saved.get("cutoff") != cutoff.isoformat()
                or not isinstance(saved.get("max_manor_id"), int)
                or not isinstance(saved.get("max_user_id"), int)
                or not isinstance(saved.get("manor_ids"), list)
                or any(not isinstance(pk, int) for pk in saved.get("manor_ids", []))
                or campaign.attachments.get("bound_items") != GIFTS
            ):
                raise CommandError("发布活动配置或截止时间不一致，拒绝修改既有发放范围")
            snapshot = saved
        eligible = _eligible(cutoff, snapshot).order_by("pk")
        pending = eligible.exclude(global_mail_deliveries__campaign=campaign) if campaign else eligible
        if not options["apply"]:
            self.stdout.write(f"DRY RUN: cutoff={cutoff.isoformat()}，待发庄园={pending.count()}；未写入任何数据")
            return
        sent = failed = 0
        for manor_id in pending.values_list("pk", flat=True).iterator(chunk_size=200):
            try:
                sent += int(_deliver(manor_id, campaign, cutoff, snapshot))
            except Exception:
                failed += 1
                logger.exception("Release gift failed: campaign=%s manor_id=%s", CAMPAIGN_KEY, manor_id)
                self.stderr.write(f"庄园 {manor_id} 发放失败，可用相同 cutoff 重试")
        self.stdout.write(f"APPLY: 已发={sent}，失败={failed}；活动保持停用，不会自动发给后来注册的玩家")
        if failed:
            raise CommandError(f"{failed} 个庄园未发放成功，请用相同 cutoff 重试")
