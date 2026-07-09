from django.db import models
from django.utils import timezone


class ShopStock(models.Model):
    """商铺库存跟踪（仅记录有限库存的商品）"""

    item_key = models.CharField(max_length=64, unique=True)
    current_stock = models.IntegerField()
    # Tracks the last daily refresh date. Must be writable so that
    # refresh tasks can set it deterministically.
    last_refresh = models.DateField(default=timezone.localdate)

    class Meta:
        db_table = "trade_shop_stock"

    def __str__(self):
        return f"{self.item_key} ({self.current_stock})"


class ShopPurchaseLog(models.Model):
    """商铺购买记录"""

    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="shop_purchases")
    item_key = models.CharField(max_length=64)
    quantity = models.PositiveIntegerField()
    unit_price = models.PositiveIntegerField()
    total_cost = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "trade_shop_purchase_log"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.manor} 购买 {self.item_key} x{self.quantity}"


class ShopSellLog(models.Model):
    """商铺出售记录"""

    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="shop_sells")
    item_key = models.CharField(max_length=64)
    quantity = models.PositiveIntegerField()
    unit_price = models.PositiveIntegerField()
    total_income = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "trade_shop_sell_log"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.manor} 出售 {self.item_key} x{self.quantity}"


class GoldBarExchangeLog(models.Model):
    """金条兑换记录"""

    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="gold_bar_exchanges")
    quantity = models.PositiveIntegerField(verbose_name="兑换数量")
    silver_cost = models.PositiveIntegerField(verbose_name="消耗银两")
    exchange_date = models.DateField(auto_now_add=True, verbose_name="兑换日期")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        db_table = "trade_gold_bar_exchange_log"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["manor", "exchange_date"]),
        ]

    def __str__(self):
        return f"{self.manor} 兑换金条 x{self.quantity} ({self.exchange_date})"


class MarketListing(models.Model):
    """交易行挂单"""

    class Status(models.TextChoices):
        ACTIVE = "active", "在售"
        SOLD = "sold", "已售出"
        EXPIRED = "expired", "已过期"
        CANCELLED = "cancelled", "已取消"

    class Duration(models.IntegerChoices):
        SHORT = 7200, "2小时"  # 2 * 3600 秒
        MEDIUM = 28800, "8小时"  # 8 * 3600 秒
        LONG = 86400, "24小时"  # 24 * 3600 秒

    # 基本信息
    seller = models.ForeignKey(
        "gameplay.Manor",
        on_delete=models.CASCADE,
        related_name="market_listings",
        verbose_name="卖家",
    )
    item_template = models.ForeignKey("gameplay.ItemTemplate", on_delete=models.CASCADE, verbose_name="物品模板")
    quantity = models.PositiveIntegerField(verbose_name="数量")

    # 定价信息
    unit_price = models.PositiveIntegerField(verbose_name="单价")
    total_price = models.PositiveIntegerField(verbose_name="总价", help_text="单价 × 数量")

    # 时间信息
    duration = models.IntegerField(choices=Duration.choices, verbose_name="上架时长（秒）")
    listing_fee = models.PositiveIntegerField(verbose_name="手续费")
    listed_at = models.DateTimeField(auto_now_add=True, verbose_name="上架时间")
    expires_at = models.DateTimeField(verbose_name="过期时间")

    # 交易状态
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
        verbose_name="状态",
    )
    buyer = models.ForeignKey(
        "gameplay.Manor",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="market_purchases",
        verbose_name="买家",
    )
    sold_at = models.DateTimeField(null=True, blank=True, verbose_name="成交时间")

    class Meta:
        db_table = "trade_market_listing"
        verbose_name = "交易行挂单"
        verbose_name_plural = "交易行挂单"
        ordering = ["-listed_at"]
        indexes = [
            models.Index(fields=["status", "-listed_at"]),
            models.Index(fields=["item_template", "status"]),
            models.Index(fields=["seller", "status"]),
            models.Index(fields=["expires_at", "status"]),
        ]

    def __str__(self):
        return f"{self.seller.user.username} - {self.item_template.name} x{self.quantity}"

    @property
    def time_remaining(self) -> int:
        """剩余时间（秒）"""
        if self.status != self.Status.ACTIVE:
            return 0
        delta = self.expires_at - timezone.now()
        return max(0, int(delta.total_seconds()))

    @property
    def is_expired(self) -> bool:
        """是否已过期"""
        return self.expires_at <= timezone.now()


class MarketTransaction(models.Model):
    """交易行成交记录"""

    listing = models.OneToOneField(
        MarketListing,
        on_delete=models.CASCADE,
        related_name="transaction",
        verbose_name="挂单",
    )
    buyer = models.ForeignKey(
        "gameplay.Manor",
        on_delete=models.CASCADE,
        related_name="market_transactions",
        verbose_name="买家",
    )

    # 交易金额
    total_price = models.PositiveIntegerField(verbose_name="成交价")
    tax_amount = models.PositiveIntegerField(verbose_name="税费", help_text="卖家支付的10%税费")
    seller_received = models.PositiveIntegerField(verbose_name="卖家实收", help_text="成交价 - 税费")

    # 时间
    transaction_at = models.DateTimeField(auto_now_add=True, verbose_name="成交时间")

    # 邮件发送状态
    buyer_mail_sent = models.BooleanField(default=False, verbose_name="买家邮件已发送")
    seller_mail_sent = models.BooleanField(default=False, verbose_name="卖家邮件已发送")

    class Meta:
        db_table = "trade_market_transaction"
        verbose_name = "交易记录"
        verbose_name_plural = "交易记录"
        ordering = ["-transaction_at"]
        indexes = [
            models.Index(fields=["-transaction_at"]),
        ]

    def __str__(self):
        return f"{self.listing.item_template.name} - {self.total_price}银两"


# ============ 拍卖行模型 ============


class AuctionRound(models.Model):
    """拍卖轮次 - 每3天为一个拍卖周期"""

    class Status(models.TextChoices):
        ACTIVE = "active", "进行中"
        SETTLING = "settling", "结算中"
        COMPLETED = "completed", "已完成"

    round_number = models.PositiveIntegerField("轮次编号", unique=True)
    status = models.CharField(
        "状态",
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    start_at = models.DateTimeField("开始时间")
    end_at = models.DateTimeField("结束时间", db_index=True)
    settled_at = models.DateTimeField("结算时间", null=True, blank=True)
    status_singleton = models.CharField(
        "状态单例锁",
        max_length=16,
        null=True,
        blank=True,
        unique=True,
        editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "trade_auction_round"
        verbose_name = "拍卖轮次"
        verbose_name_plural = "拍卖轮次"
        ordering = ["-round_number"]
        indexes = [
            models.Index(fields=["status", "end_at"]),
        ]

    @staticmethod
    def _status_singleton_for(status: str) -> str | None:
        if status in {AuctionRound.Status.ACTIVE, AuctionRound.Status.SETTLING}:
            return status
        return None

    def save(self, *args, **kwargs):
        self.status_singleton = self._status_singleton_for(self.status)

        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = set(update_fields)
            if "status" in update_fields or "status_singleton" in update_fields:
                update_fields.add("status_singleton")
                kwargs["update_fields"] = list(update_fields)

        super().save(*args, **kwargs)

    def __str__(self):
        return f"第{self.round_number}轮拍卖 ({self.get_status_display()})"

    @property
    def time_remaining(self) -> int:
        """剩余时间（秒）"""
        if self.status != self.Status.ACTIVE:
            return 0
        delta = self.end_at - timezone.now()
        return max(0, int(delta.total_seconds()))


class AuctionSlot(models.Model):
    """拍卖位 - 分单拍卖的独立拍卖位"""

    class Status(models.TextChoices):
        ACTIVE = "active", "竞拍中"
        SOLD = "sold", "已售出"
        UNSOLD = "unsold", "流拍"

    round = models.ForeignKey(
        AuctionRound,
        on_delete=models.CASCADE,
        related_name="slots",
        verbose_name="拍卖轮次",
    )
    item_template = models.ForeignKey(
        "gameplay.ItemTemplate",
        on_delete=models.CASCADE,
        related_name="auction_slots",
        verbose_name="物品模板",
    )
    quantity = models.PositiveIntegerField("数量", default=1)
    starting_price = models.PositiveIntegerField("起拍价（金条）")
    current_price = models.PositiveIntegerField("当前最高价（金条）")
    min_increment = models.PositiveIntegerField("最小加价幅度", default=1)

    # 当前最高出价者
    highest_bidder = models.ForeignKey(
        "gameplay.Manor",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="highest_bids",
        verbose_name="最高出价者",
    )
    bid_count = models.PositiveIntegerField("出价次数", default=0)

    status = models.CharField(
        "状态",
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
    )

    # 配置来源追踪
    config_key = models.CharField("配置key", max_length=64, blank=True)
    slot_index = models.PositiveSmallIntegerField("同种商品的序号", default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "trade_auction_slot"
        verbose_name = "拍卖位"
        verbose_name_plural = "拍卖位"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["round", "status"]),
            models.Index(fields=["item_template", "status"]),
        ]

    def __str__(self):
        return f"{self.item_template.name} #{self.slot_index + 1} (当前价: {self.current_price}金条)"


class AuctionBid(models.Model):
    """出价记录"""

    class Status(models.TextChoices):
        ACTIVE = "active", "有效（领先）"
        OUTBID = "outbid", "已被超越"
        WON = "won", "中标"
        REFUNDED = "refunded", "已退款"

    slot = models.ForeignKey(
        AuctionSlot,
        on_delete=models.CASCADE,
        related_name="bids",
        verbose_name="拍卖位",
    )
    manor = models.ForeignKey(
        "gameplay.Manor",
        on_delete=models.CASCADE,
        related_name="auction_bids",
        verbose_name="出价者",
    )
    amount = models.PositiveIntegerField("出价金额（金条）")
    status = models.CharField(
        "状态",
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
    )

    # 金条冻结追踪
    frozen_gold_bars = models.PositiveIntegerField("冻结金条数", default=0)
    refunded_at = models.DateTimeField("退款时间", null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "trade_auction_bid"
        verbose_name = "出价记录"
        verbose_name_plural = "出价记录"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["slot", "manor"]),
            models.Index(fields=["manor", "status"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self):
        return f"{self.manor.name} 出价 {self.amount}金条 ({self.get_status_display()})"


class FrozenGoldBar(models.Model):
    """冻结金条记录 - 用于追踪拍卖冻结的金条"""

    class Reason(models.TextChoices):
        AUCTION_BID = "auction_bid", "拍卖出价"

    manor = models.ForeignKey(
        "gameplay.Manor",
        on_delete=models.CASCADE,
        related_name="frozen_gold_bars",
        verbose_name="庄园",
    )
    amount = models.PositiveIntegerField("冻结数量")
    reason = models.CharField(
        "冻结原因",
        max_length=32,
        choices=Reason.choices,
    )
    # 关联出价记录
    auction_bid = models.OneToOneField(
        AuctionBid,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="frozen_record",
        verbose_name="关联出价",
    )

    frozen_at = models.DateTimeField("冻结时间", auto_now_add=True)
    unfrozen_at = models.DateTimeField("解冻时间", null=True, blank=True)
    is_frozen = models.BooleanField("是否冻结中", default=True, db_index=True)

    class Meta:
        db_table = "trade_frozen_gold_bar"
        verbose_name = "冻结金条记录"
        verbose_name_plural = "冻结金条记录"
        ordering = ["-frozen_at"]
        indexes = [
            models.Index(fields=["manor", "is_frozen"]),
        ]

    def __str__(self):
        status = "冻结中" if self.is_frozen else "已解冻"
        return f"{self.manor.name} {self.amount}金条 ({status})"


class AuctionDelivery(models.Model):
    """拍卖中标交付 outbox，用于补偿提交后回调丢失的奖励发放。"""

    class Status(models.TextChoices):
        PENDING = "pending", "待交付"
        DELIVERED = "delivered", "已交付"

    class Method(models.TextChoices):
        MESSAGE_ATTACHMENT = "message_attachment", "消息附件"
        DIRECT_INVENTORY = "direct_inventory", "直接入库"

    slot = models.ForeignKey(
        AuctionSlot,
        on_delete=models.CASCADE,
        related_name="deliveries",
        verbose_name="拍卖位",
    )
    bid = models.OneToOneField(
        AuctionBid,
        on_delete=models.CASCADE,
        related_name="delivery",
        verbose_name="中标出价",
    )
    manor = models.ForeignKey(
        "gameplay.Manor",
        on_delete=models.CASCADE,
        related_name="auction_deliveries",
        verbose_name="庄园",
    )
    item_template = models.ForeignKey(
        "gameplay.ItemTemplate",
        on_delete=models.PROTECT,
        related_name="auction_deliveries",
        verbose_name="物品模板",
    )
    message = models.ForeignKey(
        "gameplay.Message",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="auction_deliveries",
        verbose_name="交付消息",
    )
    quantity = models.PositiveIntegerField("数量", default=1)
    settlement_price = models.PositiveIntegerField("结算价（金条）")
    total_winners = models.PositiveIntegerField("中标人数", default=1)
    status = models.CharField(
        "状态",
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    delivery_method = models.CharField(
        "交付方式",
        max_length=32,
        choices=Method.choices,
        blank=True,
    )
    attempts = models.PositiveIntegerField("尝试次数", default=0)
    last_error = models.TextField("最近错误", blank=True)
    delivered_at = models.DateTimeField("交付时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        db_table = "trade_auction_delivery"
        verbose_name = "拍卖交付"
        verbose_name_plural = "拍卖交付"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["manor", "status"]),
            models.Index(fields=["slot", "status"]),
        ]

    def __str__(self):
        return f"{self.manor.name} {self.item_template.name} x{self.quantity} ({self.get_status_display()})"
