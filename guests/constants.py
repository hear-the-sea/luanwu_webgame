"""
门客系统相关常量
"""

from common.constants.time import TimeConstants  # noqa: F401

RARITY_CONVERSION_TEMPLATE_KEY_PREFIX = "guest_rarity_conversion_"

# 超级门客易容卡的效果配置。头像覆盖只允许使用受控的逻辑 key，避免把任意文件路径写入门客数据。
SUPER_GUEST_DISGUISE_ACTION = "disguise_guest"
SUPER_GUEST_DISGUISE_AVATAR_KEY = "lvbu_monkey"
SUPER_GUEST_DISGUISE_AVATAR_PATH = "guests/lvbu_monkey.webp"
SUPER_GUEST_DISGUISE_HP_BONUS = 800
SUPER_GUEST_DISGUISE_FORCE_BONUS = 20
SUPER_GUEST_DISGUISE_AGILITY_BONUS = 20
