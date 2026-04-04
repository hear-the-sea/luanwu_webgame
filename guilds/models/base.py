from django.contrib.auth import get_user_model
from django.core.validators import MaxLengthValidator, MinLengthValidator
from django.db import models
from django.db.models import Count, Q
from django.utils import timezone

User = get_user_model()


class GuildManager(models.Manager):
    """Guild 自定义管理器，提供性能优化查询方法"""

    def with_member_count(self):
        """
        预加载活跃成员数（优化 N+1 查询）

        返回带有 _member_count 注解的 QuerySet。
        注意：使用此方法后，current_member_count property 会自动使用预加载的值。

        示例：
            guilds = Guild.objects.with_member_count().filter(is_active=True)[:20]
            for guild in guilds:
                print(guild.current_member_count)  # 无额外查询
        """
        return self.annotate(_member_count=Count("members", filter=Q(members__is_active=True)))


class Guild(models.Model):
    """帮会主体"""

    # 基础信息
    name = models.CharField(
        max_length=50,
        unique=True,
        validators=[MinLengthValidator(2), MaxLengthValidator(12)],
        verbose_name="帮会名称",
        help_text="2-12个字符",
    )
    description = models.TextField(max_length=200, blank=True, verbose_name="帮会简介")
    emblem = models.CharField(max_length=50, default="default", verbose_name="帮会徽章", help_text="徽章图标key")

    # 创建信息
    founder = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name="founded_guilds", verbose_name="创建者"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    # 等级与容量
    level = models.PositiveIntegerField(default=1, verbose_name="帮会等级")

    # 资源池
    silver = models.PositiveIntegerField(default=0, verbose_name="银两")
    grain = models.PositiveIntegerField(default=0, verbose_name="粮食")
    gold_bar = models.PositiveIntegerField(default=0, verbose_name="金条")

    # 状态
    is_active = models.BooleanField(default=True, verbose_name="是否活跃")
    auto_accept = models.BooleanField(default=False, verbose_name="自动接受申请")
    newbie_protection_until = models.DateTimeField(null=True, blank=True, verbose_name="新帮保护截止时间")
    defeat_protection_until = models.DateTimeField(null=True, blank=True, verbose_name="战败保护截止时间")
    pvp_attack_count_today = models.PositiveSmallIntegerField(default=0, verbose_name="今日主动进攻次数")
    pvp_attack_count_reset_at = models.DateField(default=timezone.localdate, verbose_name="PVP 主动进攻重置时间")
    pvp_defense_count_today = models.PositiveSmallIntegerField(default=0, verbose_name="今日被攻击次数")
    pvp_defense_count_reset_at = models.DateField(default=timezone.localdate, verbose_name="PVP 被攻击重置时间")

    # 自定义管理器
    objects = GuildManager()

    class Meta:
        db_table = "guilds"
        verbose_name = "帮会"
        verbose_name_plural = "帮会"
        ordering = ["-level", "-created_at"]

    def __str__(self):
        return f"{self.name} (Lv.{self.level})"

    @property
    def member_capacity(self):
        """成员容量: 10 + (等级-1) * 2"""
        return 10 + (self.level - 1) * 2

    @property
    def current_member_count(self):
        """
        当前成员数

        优化说明：
        - 如果使用 Guild.objects.with_member_count() 查询，会使用预加载的值
        - 否则会执行一次 count() 查询（用于单个对象或未预加载的情况）
        """
        # 优先使用预加载的注解值
        if hasattr(self, "_member_count"):
            return self._member_count
        # 降级为直接查询
        return self.members.filter(is_active=True).count()

    @property
    def is_full(self):
        """是否已满员"""
        return self.current_member_count >= self.member_capacity

    def get_leader(self):
        """获取帮主"""
        return self.members.filter(is_active=True, position="leader").select_related("user__manor").first()

    def get_admins(self):
        """获取管理员列表"""
        return self.members.filter(is_active=True, position="admin").select_related("user__manor")

    def can_appoint_admin(self):
        """是否可以任命管理员"""
        return self.get_admins().count() < 2

    @property
    def is_under_newbie_protection(self) -> bool:
        if not self.newbie_protection_until:
            return False
        return self.newbie_protection_until > timezone.now()

    @property
    def is_under_defeat_protection(self) -> bool:
        if not self.defeat_protection_until:
            return False
        return self.defeat_protection_until > timezone.now()
