"""
gameplay app models.

This module is a package to keep the codebase maintainable: the original monolithic
`gameplay/models.py` was split into multiple focused modules. We re-export the
public model classes/constants here to preserve the historical import style:

    from gameplay.models import Manor, InventoryItem, RaidRun, ...
"""

from .arena import ArenaEntry, ArenaEntryGuest, ArenaExchangeRecord, ArenaMatch, ArenaTournament
from .arena_coop import ArenaCoopContribution, ArenaCoopEntry, ArenaCoopEntryGuest, ArenaCoopEvent
from .arena_virtual import ArenaVirtualDemand, ArenaVirtualReserveMember
from .bots import BotBackfillDemand, BotInventoryDailyCounter, BotPopulationControl, BotProfile
from .chat import WorldChatSendAttempt
from .items import GlobalMailCampaign, GlobalMailDelivery, InventoryItem, ItemTemplate, Message, ResourceEvent
from .manor import (
    ACTION_POINTS_MAX,
    CITANG_BUILDING_TIME_REDUCTION_PER_LEVEL,
    CITANG_RECRUITMENT_SPEED_BONUS_PER_LEVEL,
    GUEST_CAPACITY_BASE,
    GUEST_CAPACITY_PER_LEVEL,
    PRODUCTION_SPEED_BONUS_PER_LEVEL,
    RETAINER_CAPACITY_BASE,
    RETAINER_CAPACITY_PER_LEVEL,
    SQUAD_SIZE_BASE,
    SQUAD_SIZE_MAX,
    SQUAD_SIZE_PER_LEVEL,
    TRAINING_SPEED_BONUS_PER_LEVEL,
    Building,
    BuildingCategory,
    BuildingType,
    Manor,
    ResourceType,
)
from .missions import MissionExtraAttempt, MissionRun, MissionTemplate
from .progression import (
    EquipmentProduction,
    HorseProduction,
    LivestockProduction,
    PlayerTechnology,
    PlayerTroop,
    SmeltingProduction,
    TroopBankStorage,
    TroopRecruitment,
    WorkAssignment,
    WorkTemplate,
)
from .pvp import JailInteractionLog, JailPrisoner, OathBond, RaidRun, ScoutCooldown, ScoutRecord

__all__ = [
    # manor/buildings
    "ResourceType",
    "Manor",
    "BuildingCategory",
    "BuildingType",
    "Building",
    "GUEST_CAPACITY_BASE",
    "GUEST_CAPACITY_PER_LEVEL",
    "RETAINER_CAPACITY_BASE",
    "RETAINER_CAPACITY_PER_LEVEL",
    "SQUAD_SIZE_BASE",
    "SQUAD_SIZE_PER_LEVEL",
    "SQUAD_SIZE_MAX",
    "TRAINING_SPEED_BONUS_PER_LEVEL",
    "PRODUCTION_SPEED_BONUS_PER_LEVEL",
    "CITANG_BUILDING_TIME_REDUCTION_PER_LEVEL",
    "CITANG_RECRUITMENT_SPEED_BONUS_PER_LEVEL",
    "ACTION_POINTS_MAX",
    # items/messages/resources
    "ResourceEvent",
    "ItemTemplate",
    "InventoryItem",
    "Message",
    "GlobalMailCampaign",
    "GlobalMailDelivery",
    # arena
    "ArenaTournament",
    "ArenaEntry",
    "ArenaEntryGuest",
    "ArenaMatch",
    "ArenaExchangeRecord",
    "ArenaCoopEvent",
    "ArenaCoopEntry",
    "ArenaCoopEntryGuest",
    "ArenaCoopContribution",
    "ArenaVirtualDemand",
    "ArenaVirtualReserveMember",
    # bots
    "BotBackfillDemand",
    "BotInventoryDailyCounter",
    "BotPopulationControl",
    "BotProfile",
    # chat
    "WorldChatSendAttempt",
    # missions
    "MissionTemplate",
    "MissionRun",
    "MissionExtraAttempt",
    # progression/work/production
    "PlayerTechnology",
    "WorkTemplate",
    "WorkAssignment",
    "PlayerTroop",
    "TroopBankStorage",
    "HorseProduction",
    "LivestockProduction",
    "SmeltingProduction",
    "EquipmentProduction",
    "TroopRecruitment",
    # pvp/raid/jail/oath
    "ScoutRecord",
    "ScoutCooldown",
    "RaidRun",
    "OathBond",
    "JailPrisoner",
    "JailInteractionLog",
]
