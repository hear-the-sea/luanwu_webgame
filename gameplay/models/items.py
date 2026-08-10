from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from django.core.exceptions import ValidationError
from django.db import models, router, transaction
from django.db.models.deletion import ProtectedError
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from .manor import ResourceType

_PREVALIDATED_MESSAGE_DELETIONS: ContextVar[frozenset[tuple[str, int]]] = ContextVar(
    "prevalidated_message_deletions",
    default=frozenset(),
)


@contextmanager
def _allow_prevalidated_message_deletions(
    database_alias: str,
    message_ids: Iterable[int],
) -> Iterator[None]:
    allowed_deletions = _PREVALIDATED_MESSAGE_DELETIONS.get() | frozenset(
        (database_alias, message_id) for message_id in message_ids
    )
    token = _PREVALIDATED_MESSAGE_DELETIONS.set(allowed_deletions)
    try:
        yield
    finally:
        _PREVALIDATED_MESSAGE_DELETIONS.reset(token)


def _message_deletion_is_prevalidated(database_alias: str, message_id: int | None) -> bool:
    return message_id is not None and (database_alias, message_id) in _PREVALIDATED_MESSAGE_DELETIONS.get()


class ResourceEvent(models.Model):
    class Reason(models.TextChoices):
        PRODUCE = "produce", "自动产出"
        UPGRADE_COST = "upgrade_cost", "建筑升级"
        TASK_REWARD = "task_reward", "任务奖励"
        BATTLE_REWARD = "battle_reward", "战斗掉落"
        ADMIN_ADJUST = "admin_adjust", "运营调整"
        RECRUIT_COST = "recruit_cost", "门客招募"
        TRAINING_COST = "training_cost", "门客培养"
        ITEM_USE = "item_use", "道具使用"
        BANK_EXCHANGE = "bank_exchange", "钱庄兑换"
        SHOP_PURCHASE = "shop_purchase", "商铺购买"
        SHOP_SELL = "shop_sell", "商铺出售"
        WORK_REWARD = "work_reward", "打工报酬"
        GUILD_DONATION = "guild_donation", "帮会捐献"
        MARKET_LISTING_FEE = "market_listing_fee", "交易行挂单手续费"
        MARKET_PURCHASE = "market_purchase", "交易行购买"
        MARKET_SOLD = "market_sold", "交易行出售"
        ITEM_SOLD = "item_sold", "物品出售"
        TECH_UPGRADE = "tech_upgrade", "科技升级"
        CITY_DEFENSE_REPAIR = "city_defense_repair", "城防修复"

    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="resource_events")
    resource_type = models.CharField(max_length=16, choices=ResourceType.choices)
    delta = models.IntegerField()
    reason = models.CharField(max_length=32, choices=Reason.choices)
    note = models.CharField(max_length=128, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "资源流水"
        verbose_name_plural = "资源流水"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["manor", "-created_at"]),
            models.Index(fields=["manor", "reason", "-created_at"]),
        ]


class ItemTemplate(models.Model):
    class EffectType(models.TextChoices):
        RESOURCE_PACK = "resource_pack", "资源补给"
        RESOURCE = "resource", "资源"
        SKILL_BOOK = "skill_book", "技能书"
        EXPERIENCE_ITEM = "experience_items", "经验道具"
        MEDICINE = "medicine", "药品"
        TOOL = "tool", "道具"
        LOOT_BOX = "loot_box", "宝箱"

    key = models.SlugField(unique=True)
    name = models.CharField(max_length=64)
    description = models.TextField(blank=True)
    effect_type = models.CharField(max_length=32, choices=EffectType.choices, default=EffectType.RESOURCE_PACK)
    effect_payload = models.JSONField(default=dict, blank=True)
    icon = models.CharField(max_length=32, blank=True)
    image = models.ImageField(upload_to="items/", blank=True, null=True, verbose_name="物品图片")
    rarity = models.CharField(max_length=16, default="gray")
    tradeable = models.BooleanField(default=False)
    price = models.PositiveIntegerField(default=0)
    storage_space = models.PositiveIntegerField(default=1, verbose_name="占用空间")
    is_usable = models.BooleanField(default=False, verbose_name="可在仓库使用")

    class Meta:
        verbose_name = "物品模板"
        verbose_name_plural = "物品模板"

    def __str__(self) -> str:
        return self.name


LEGACY_TOOL_EFFECT_TYPES = {
    "tool",
    "loot_box",
    "magnifying_glass",
    "peace_shield",
    "manor_rename",
    ItemTemplate.EffectType.RESOURCE_PACK,
}

ITEM_EFFECT_TYPE_LABELS = {
    ItemTemplate.EffectType.RESOURCE_PACK: "资源包",
    ItemTemplate.EffectType.RESOURCE: "资源",
    ItemTemplate.EffectType.SKILL_BOOK: "技能书",
    ItemTemplate.EffectType.EXPERIENCE_ITEM: "经验",
    ItemTemplate.EffectType.MEDICINE: "药品",
    ItemTemplate.EffectType.TOOL: "道具",
    "equip_helmet": "头盔",
    "equip_armor": "衣服",
    "equip_shoes": "鞋子",
    "equip_weapon": "武器",
    "equip_mount": "坐骑",
    "equip_ornament": "饰品",
    "equip_device": "器械",
}


def normalize_item_effect_type(effect_type: str | None) -> str:
    normalized = str(effect_type or "").strip()
    if normalized in LEGACY_TOOL_EFFECT_TYPES:
        return ItemTemplate.EffectType.TOOL
    return normalized


def get_item_effect_type_label(effect_type: str | None) -> str:
    normalized = normalize_item_effect_type(effect_type)
    if normalized.startswith("equip_"):
        return ITEM_EFFECT_TYPE_LABELS.get(normalized, "装备")
    return ITEM_EFFECT_TYPE_LABELS.get(normalized, "其他")


_ITEM_EFFECT_STAT_LABELS = {
    "hp": "生命",
    "force": "武力",
    "intellect": "智力",
    "defense": "防御",
    "agility": "敏捷",
    "luck": "运势",
    "troop_capacity": "可携带护院人数",
    "attack": "攻击",
    "defense_bonus": "防御",
}


def _resource_pack_summary(payload: dict) -> str:
    labels = dict(ResourceType.choices)
    parts = [f"{labels.get(key, '未知资源')} +{amount}" for key, amount in payload.items()]
    return "、".join(parts)


def _iter_set_bonus_entries(set_bonus) -> list[tuple[int | None, dict]]:
    if isinstance(set_bonus, list):
        entries = []
        for entry in set_bonus:
            entries.extend(_iter_set_bonus_entries(entry))
        return entries
    if not isinstance(set_bonus, dict):
        return []
    bonus_map = set_bonus.get("bonus") if "bonus" in set_bonus else set_bonus.get("bonuses")
    if bonus_map is None:
        bonus_map = set_bonus
    if not isinstance(bonus_map, dict):
        return []
    return [(set_bonus.get("pieces"), bonus_map)]


def _equipment_set_summary(payload: dict) -> str:
    set_desc = payload.get("set_description")
    set_bonus = payload.get("set_bonus") or {}
    if not (set_desc or set_bonus):
        return ""

    desc_text = set_desc or "套装"
    tier_summaries = []
    for pieces, bonus_map in _iter_set_bonus_entries(set_bonus):
        bonus_parts = []
        for key, value in bonus_map.items():
            if key in {"pieces", "bonus", "bonuses"}:
                continue
            if value is None:
                continue
            bonus_parts.append(f"{_ITEM_EFFECT_STAT_LABELS.get(key, '未知属性')}+{value}")
        piece_text = f"（{pieces}件）" if pieces else ""
        if bonus_parts:
            tier_summaries.append(f"{desc_text}{piece_text}：" + "、".join(bonus_parts))
        else:
            tier_summaries.append(f"{desc_text}{piece_text}")
    if tier_summaries:
        return "；".join(tier_summaries)
    return desc_text


def _equipment_summary(payload: dict) -> str:
    parts = []
    for key, value in payload.items():
        if value is None or key in {"set_key", "set_bonus", "set_description"}:
            continue
        parts.append(f"{_ITEM_EFFECT_STAT_LABELS.get(key, '未知属性')}+{value}")

    set_text = _equipment_set_summary(payload)
    if set_text:
        return ("、".join(parts) + "；" if parts else "") + set_text
    return "、".join(parts) or "提升属性"


def _tool_summary(template_key: str, payload: dict) -> str:
    if template_key == "fangdajing":
        return "显现候选稀有度"
    if template_key == "recruitment_card":
        return "指定卡池今日招募额度 +1"
    if template_key.startswith("peace_shield_"):
        duration = payload.get("duration")
        if not duration:
            return "免战保护"
        hours = duration // 3600
        if hours % 24 == 0:
            return f"免战保护 {hours // 24} 天"
        return f"免战保护 {hours} 小时"
    if template_key == "manor_rename_card":
        return "更换庄园名称"
    return "道具"


class InventoryItem(models.Model):
    class StorageLocation(models.TextChoices):
        WAREHOUSE = "warehouse", "仓库"
        TREASURY = "treasury", "藏宝阁"

    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="inventory_items")
    template = models.ForeignKey(ItemTemplate, on_delete=models.CASCADE, related_name="inventory_entries")
    quantity = models.PositiveIntegerField(default=0)
    storage_location = models.CharField(
        max_length=16,
        choices=StorageLocation.choices,
        default=StorageLocation.WAREHOUSE,
        verbose_name="存储位置",
    )
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "仓库物品"
        verbose_name_plural = "仓库物品"
        unique_together = ("manor", "template", "storage_location")
        indexes = [
            models.Index(fields=["manor", "storage_location", "quantity"], name="inventory_manor_loc_qty_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.manor} - {self.template.name} x{self.quantity}"

    @property
    def effect_summary(self) -> str:
        payload = self.template.effect_payload or {}
        effect_type = self.template.effect_type
        if effect_type == ItemTemplate.EffectType.RESOURCE_PACK and payload:
            return _resource_pack_summary(payload)
        if effect_type == ItemTemplate.EffectType.SKILL_BOOK:
            skill_name = payload.get("skill_name") or payload.get("skill_key", "技能")
            return f"学习 {skill_name}"
        if effect_type and effect_type.startswith("equip_"):
            return _equipment_summary(payload)
        if effect_type == ItemTemplate.EffectType.MEDICINE:
            hp = payload.get("hp")
            if hp:
                return f"恢复生命 +{hp}"
            return "恢复生命"
        if effect_type == ItemTemplate.EffectType.TOOL:
            return _tool_summary(self.template.key or "", payload)
        return "无特殊效果"

    @property
    def can_use_in_warehouse(self) -> bool:
        return self.template.is_usable

    @property
    def warehouse_use_hint(self) -> str:
        if self.can_use_in_warehouse:
            return ""
        return "此物品不可在仓库使用"

    @property
    def category_display(self) -> str:
        """获取物品种类显示名称"""
        return get_item_effect_type_label(self.template.effect_type)


AttachmentBucket = dict[str, Any]


def normalize_message_attachment_buckets(attachments: object) -> tuple[AttachmentBucket, AttachmentBucket]:
    """安全提取资源和道具附件分组，非字典分组按空处理。"""
    if not isinstance(attachments, dict):
        return {}, {}
    resources = attachments.get("resources")
    items = attachments.get("items")
    return (
        resources if isinstance(resources, dict) else {},
        items if isinstance(items, dict) else {},
    )


def message_has_attachments(attachments: object) -> bool:
    """仅将非空资源或道具字典视为真实附件。"""
    resources, items = normalize_message_attachment_buckets(attachments)
    return bool(resources or items)


def message_has_protected_attachments(attachments: object, *, is_claimed: bool) -> bool:
    """判断消息是否含有尚未领取、必须保护的附件资产。"""
    return not is_claimed and message_has_attachments(attachments)


class Message(models.Model):
    class Kind(models.TextChoices):
        BATTLE = "battle", "战报"
        SYSTEM = "system", "系统"
        REWARD = "reward", "奖励"

    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="messages")
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.SYSTEM)
    title = models.CharField(max_length=128)
    body = models.TextField(blank=True)
    battle_report = models.ForeignKey(
        "battle.BattleReport",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages",
    )
    attachments = models.JSONField("附件数据", default=dict, blank=True)
    is_claimed = models.BooleanField("已领取", default=False)
    is_deletion_protected = models.BooleanField("禁止删除", default=False, editable=False)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "消息"
        verbose_name_plural = "消息"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["manor", "is_read", "-created_at"]),
            models.Index(fields=["manor", "is_claimed"]),
            models.Index(fields=["manor", "kind", "-created_at"]),
            models.Index(
                fields=["is_deletion_protected", "created_at", "id"],
                name="message_protected_cleanup_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"[{self.get_kind_display()}] {self.title}"

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = set(update_fields)
            kwargs["update_fields"] = update_fields
        protection_inputs_are_saved = update_fields is None or bool(
            {"attachments", "is_claimed", "is_deletion_protected"}.intersection(update_fields)
        )
        if protection_inputs_are_saved:
            self.is_deletion_protected = message_has_protected_attachments(
                self.attachments,
                is_claimed=self.is_claimed,
            )
            if update_fields is not None:
                update_fields.add("is_deletion_protected")
        super().save(*args, **kwargs)

    def delete(self, using=None, keep_parents=False):
        using = using or router.db_for_write(self.__class__, instance=self)
        with transaction.atomic(using=using):
            locked_message = self.__class__._base_manager.using(using).select_for_update().filter(pk=self.pk).first()
            if locked_message is None:
                return models.Model.delete(self, using=using, keep_parents=keep_parents)
            if locked_message.has_protected_attachments:
                raise ProtectedError("未领取附件消息禁止删除", {locked_message})
            result = models.Model.delete(locked_message, using=using, keep_parents=keep_parents)

        self.pk = locked_message.pk
        return result

    @property
    def has_attachments(self) -> bool:
        """检查是否有附件"""
        return message_has_attachments(self.attachments)

    @property
    def has_protected_attachments(self) -> bool:
        """检查是否含有禁止删除的未领取附件资产。"""
        return message_has_protected_attachments(self.attachments, is_claimed=self.is_claimed)

    def get_attachment_summary(self) -> str:
        """获取附件摘要，用于列表显示"""
        if not self.has_attachments:
            return ""

        parts = []
        attachments = self.attachments
        resources, items = normalize_message_attachment_buckets(attachments)

        if self.is_claimed and isinstance(attachments, dict):
            claimed = attachments.get("claimed")
            if isinstance(claimed, dict):
                resources, items = normalize_message_attachment_buckets(claimed)

        resource_labels = dict(ResourceType.choices)
        for key, amount in resources.items():
            label = resource_labels.get(key, "未知资源")
            parts.append(f"{label}×{amount}")

        # 物品数量统计
        if items:
            parts.append(f"{len(items)}种道具")

        return "、".join(parts) if parts else "附件"


@receiver(pre_delete, sender=Message, dispatch_uid="protect_unclaimed_message_attachments")
def protect_unclaimed_message_attachments(sender, instance: Message, using: str, **kwargs) -> None:
    """阻止任何 ORM 删除路径丢弃尚未领取的附件资产。"""
    if _message_deletion_is_prevalidated(using, instance.pk):
        return
    locked_message = (
        sender._base_manager.using(using)
        .select_for_update()
        .only("attachments", "is_claimed", "kind", "title")
        .filter(pk=instance.pk)
        .first()
    )
    if locked_message is not None and locked_message.has_protected_attachments:
        raise ProtectedError("未领取附件消息禁止删除", {locked_message})


def _validate_attachment_bucket(value, bucket_name: str) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValidationError({bucket_name: f"{bucket_name} 必须为对象"})

    for key, amount in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ValidationError({bucket_name: f"{bucket_name} 的 key 必须为非空字符串"})
        try:
            normalized_amount = int(amount)
        except (TypeError, ValueError):
            raise ValidationError({bucket_name: f"{bucket_name}.{key} 必须为整数"}) from None
        if normalized_amount <= 0:
            raise ValidationError({bucket_name: f"{bucket_name}.{key} 必须大于 0"})


class GlobalMailCampaign(models.Model):
    key = models.SlugField("活动标识", max_length=64, unique=True)
    kind = models.CharField(max_length=16, choices=Message.Kind.choices, default=Message.Kind.REWARD)
    title = models.CharField("标题", max_length=128)
    body = models.TextField("正文", blank=True)
    attachments = models.JSONField("附件数据", default=dict, blank=True)
    is_active = models.BooleanField("启用", default=True, db_index=True)
    start_at = models.DateTimeField("开始时间", null=True, blank=True, db_index=True)
    end_at = models.DateTimeField("结束时间", null=True, blank=True, db_index=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "全服邮件活动"
        verbose_name_plural = "全服邮件活动"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["is_active", "start_at", "end_at"], name="global_mail_active_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.key} - {self.title}"

    def clean(self) -> None:
        super().clean()

        if self.start_at and self.end_at and self.end_at < self.start_at:
            raise ValidationError({"end_at": "结束时间不能早于开始时间"})

        attachments = self.attachments or {}
        if not isinstance(attachments, dict):
            raise ValidationError({"attachments": "附件数据必须为对象"})

        resources = attachments.get("resources", {})
        items = attachments.get("items", {})
        _validate_attachment_bucket(resources, "resources")
        _validate_attachment_bucket(items, "items")

    def is_active_at(self, dt=None) -> bool:
        if not self.is_active:
            return False
        if dt is None:
            from django.utils import timezone

            dt = timezone.now()
        if self.start_at and dt < self.start_at:
            return False
        if self.end_at and dt > self.end_at:
            return False
        return True


class GlobalMailDelivery(models.Model):
    campaign = models.ForeignKey(
        "gameplay.GlobalMailCampaign",
        on_delete=models.CASCADE,
        related_name="deliveries",
        verbose_name="活动",
    )
    manor = models.ForeignKey(
        "gameplay.Manor",
        on_delete=models.CASCADE,
        related_name="global_mail_deliveries",
        verbose_name="庄园",
    )
    message = models.ForeignKey(
        "gameplay.Message",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="global_mail_deliveries",
        verbose_name="消息",
    )
    created_at = models.DateTimeField("投递时间", auto_now_add=True)

    class Meta:
        verbose_name = "全服邮件投递记录"
        verbose_name_plural = "全服邮件投递记录"
        constraints = [
            models.UniqueConstraint(fields=["campaign", "manor"], name="uniq_global_mail_campaign_manor"),
        ]
        indexes = [
            models.Index(fields=["campaign", "-created_at"], name="global_mail_deliver_c_idx"),
            models.Index(fields=["manor", "-created_at"], name="global_mail_deliver_m_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.campaign.key} -> {self.manor.display_name}"
