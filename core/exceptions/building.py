"""
建筑和技术相关异常
"""

from __future__ import annotations

from .base import GameError

# ============ 建筑相关异常 ============


class BuildingError(GameError):
    """建筑相关错误基类"""

    error_code = "BUILDING_ERROR"


class BuildingUpgradingError(BuildingError):
    """建筑正在升级中"""

    error_code = "BUILDING_UPGRADING"
    default_message = "建筑正在升级中"


class BuildingMaxLevelError(BuildingError):
    """建筑已达最高等级"""

    error_code = "BUILDING_MAX_LEVEL"

    def __init__(self, building_name: str, max_level: int, message: str | None = None):
        if message is None:
            message = f"{building_name}已达到最大等级（等级{max_level}）"
        super().__init__(message, building_name=building_name, max_level=max_level)


class BuildingConcurrentUpgradeLimitError(BuildingError):
    """同时升级的建筑数量超限"""

    error_code = "BUILDING_CONCURRENT_UPGRADE_LIMIT"

    def __init__(self, limit: int, message: str | None = None):
        if message is None:
            message = f"同时最多只能升级 {limit} 个建筑"
        super().__init__(message, limit=limit)


class ProductionStartError(BuildingError):
    """生产/制作发起错误"""

    error_code = "PRODUCTION_START_ERROR"


class ProductionCancelError(BuildingError):
    """生产/制作取消错误"""

    error_code = "PRODUCTION_CANCEL_ERROR"


class ForgeOperationError(BuildingError):
    """铁匠铺锻造 / 合成 / 分解错误"""

    error_code = "FORGE_OPERATION_ERROR"


class BuildingNotBuiltError(BuildingError):
    """建筑尚未建造"""

    error_code = "BUILDING_NOT_BUILT"

    def __init__(self, building_name: str, message: str | None = None):
        if message is None:
            message = f"{building_name}尚未建造"
        super().__init__(message, building_name=building_name)


class BuildingNotFoundError(BuildingError):
    """建筑不存在"""

    error_code = "BUILDING_NOT_FOUND"

    def __init__(self, building_key: str, message: str | None = None):
        building_names = {
            "treasury": "藏宝阁",
            "juxianzhuang": "聚贤庄",
            "jiadingfang": "家丁房",
        }
        building_name = building_names.get(building_key, "该建筑")
        if message is None:
            message = f"{building_name}尚未建造"
        super().__init__(message, building_key=building_key)


# ============ 技术相关异常 ============


class TechnologyError(GameError):
    """技术相关错误基类"""

    error_code = "TECHNOLOGY_ERROR"


class TechnologyNotFoundError(TechnologyError):
    """未知技术"""

    error_code = "TECHNOLOGY_NOT_FOUND"

    def __init__(self, tech_key: str, message: str | None = None):
        if message is None:
            message = "未找到对应科技"
        super().__init__(message, tech_key=tech_key)


class TechnologyUpgradeInProgressError(TechnologyError):
    """技术正在升级中"""

    error_code = "TECHNOLOGY_UPGRADE_IN_PROGRESS"

    def __init__(self, tech_key: str, tech_name: str, message: str | None = None):
        if message is None:
            message = f"{tech_name} 正在升级中"
        super().__init__(message, tech_key=tech_key, tech_name=tech_name)


class TechnologyConcurrentUpgradeLimitError(TechnologyError):
    """同时升级的技术数量超限"""

    error_code = "TECHNOLOGY_CONCURRENT_UPGRADE_LIMIT"

    def __init__(self, limit: int, message: str | None = None):
        if message is None:
            message = f"同时最多只能研究 {limit} 项科技"
        super().__init__(message, limit=limit)


class TechnologyMaxLevelError(TechnologyError):
    """技术已达最高等级"""

    error_code = "TECHNOLOGY_MAX_LEVEL"

    def __init__(self, tech_key: str, tech_name: str, max_level: int, message: str | None = None):
        if message is None:
            message = f"{tech_name} 已达到最高等级"
        super().__init__(message, tech_key=tech_key, tech_name=tech_name, max_level=max_level)
