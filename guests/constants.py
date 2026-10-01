"""
门客系统相关常量
"""

from common.constants.time import TimeConstants  # noqa: F401

RARITY_CONVERSION_TEMPLATE_KEY_PREFIX = "guest_rarity_conversion_"

# 这些升阶模板代表同一名门客；招募和收编时应按人物判重。
UPGRADE_TEMPLATE_FAMILIES = (
    ("hist_sljnbc_0589", "hist_sljnbc_0589_blue", "hist_sljnbc_0589_purple"),
    ("hist_sljnbc_0590", "hist_sljnbc_0590_blue", "hist_sljnbc_0590_purple"),
)

# 超级门客易容卡的效果配置。头像覆盖只允许使用受控的逻辑 key，避免把任意文件路径写入门客数据。
SUPER_GUEST_DISGUISE_ACTION = "disguise_guest"
SUPER_GUEST_DISGUISE_AVATAR_KEY = "lvbu_monkey"
SUPER_GUEST_DISGUISE_AVATAR_PATH = "guests/lvbu_monkey.webp"
SUPER_GUEST_DISGUISE_HP_BONUS = 800
SUPER_GUEST_DISGUISE_FORCE_BONUS = 20
SUPER_GUEST_DISGUISE_AGILITY_BONUS = 20

# 超级门客召唤卡只从可通过招募获得的未拥有橙色门客中随机选择。
SUPER_GUEST_SUMMON_ACTION = "summon_unobtained_orange_guest"

# 普通门客易容卡只允许使用素材目录中这三类头像，服务端会再次校验文件名，
# 不把客户端提交的路径直接交给存储层。
GUEST_DISGUISE_AVATAR_ACTION = "disguise_guest_avatar"
GUEST_DISGUISE_AVATAR_PREFIXES = ("monkey", "pig", "duck")
