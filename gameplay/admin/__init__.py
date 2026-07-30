from core.admin_i18n import apply_common_field_labels

from ..models import (
    ArenaCoopEvent,
    ArenaEntry,
    ArenaEntryGuest,
    ArenaExchangeRecord,
    ArenaMatch,
    ArenaTournament,
    ArenaVirtualDemand,
    ArenaVirtualReserveMember,
    BotProfile,
    Building,
    BuildingType,
    GlobalMailCampaign,
    GlobalMailDelivery,
    InventoryItem,
    ItemTemplate,
    Manor,
    Message,
    MissionRun,
    MissionTemplate,
    RaidRun,
    ResourceEvent,
    ScoutCooldown,
    ScoutRecord,
    WorkAssignment,
    WorkTemplate,
)

apply_common_field_labels(
    Manor,
    BuildingType,
    Building,
    ResourceEvent,
    MissionTemplate,
    MissionRun,
    ItemTemplate,
    InventoryItem,
    GlobalMailCampaign,
    GlobalMailDelivery,
    Message,
    WorkTemplate,
    WorkAssignment,
    ArenaTournament,
    ArenaEntry,
    ArenaEntryGuest,
    ArenaCoopEvent,
    ArenaMatch,
    ArenaExchangeRecord,
    ArenaVirtualDemand,
    ArenaVirtualReserveMember,
    BotProfile,
    ScoutRecord,
    ScoutCooldown,
    RaidRun,
    labels={
        "region": "地区",
        "prestige": "声望",
        "grain": "粮食",
        "silver": "银两",
        "arena_coins": "角斗币",
        "base_rate_per_hour": "基础时产",
        "rate_growth": "成长系数",
        "deliveries_count": "投递数量",
    },
)

# Import submodules to trigger admin.site.register() calls
from .arena import (  # noqa: E402, F401
    ArenaEntryAdmin,
    ArenaEntryGuestAdmin,
    ArenaExchangeRecordAdmin,
    ArenaMatchAdmin,
    ArenaTournamentAdmin,
)
from .arena_coop import ArenaCoopEventAdmin  # noqa: E402, F401
from .arena_virtual import ArenaVirtualDemandAdmin, ArenaVirtualReserveMemberAdmin  # noqa: E402, F401
from .bots import (  # noqa: E402, F401
    BotBackfillDemandAdmin,
    BotExternalStrengthReconciliationAdmin,
    BotInventoryDailyCounterAdmin,
    BotPolicyReleaseAdmin,
    BotPopulationRecomputeDemandAdmin,
    BotProfileAdmin,
    BotRuntimeRoutingStateAdmin,
)
from .buildings import BuildingAdmin, BuildingTypeAdmin, WorkAssignmentAdmin, WorkTemplateAdmin  # noqa: E402, F401
from .core import ManorAdmin, ResourceEventAdmin  # noqa: E402, F401
from .inventory import InventoryItemAdmin, ItemTemplateAdmin  # noqa: E402, F401
from .messages import (  # noqa: E402, F401
    CampaignRuntimeStatusFilter,
    GlobalMailCampaignAdmin,
    GlobalMailCampaignForm,
    GlobalMailDeliveryAdmin,
    MessageAdmin,
    SendMessageForm,
)
from .missions import MissionRunAdmin, MissionTemplateAdmin  # noqa: E402, F401
from .raids import RaidRunAdmin, ScoutCooldownAdmin, ScoutRecordAdmin  # noqa: E402, F401

__all__ = [
    # core
    "ManorAdmin",
    "ResourceEventAdmin",
    # buildings
    "BuildingTypeAdmin",
    "BuildingAdmin",
    "WorkTemplateAdmin",
    "WorkAssignmentAdmin",
    # bots
    "BotBackfillDemandAdmin",
    "BotExternalStrengthReconciliationAdmin",
    "BotInventoryDailyCounterAdmin",
    "BotPolicyReleaseAdmin",
    "BotPopulationRecomputeDemandAdmin",
    "BotProfileAdmin",
    "BotRuntimeRoutingStateAdmin",
    # missions
    "MissionTemplateAdmin",
    "MissionRunAdmin",
    # inventory
    "ItemTemplateAdmin",
    "InventoryItemAdmin",
    # arena
    "ArenaTournamentAdmin",
    "ArenaEntryAdmin",
    "ArenaEntryGuestAdmin",
    "ArenaMatchAdmin",
    "ArenaExchangeRecordAdmin",
    "ArenaCoopEventAdmin",
    "ArenaVirtualDemandAdmin",
    "ArenaVirtualReserveMemberAdmin",
    # raids
    "ScoutRecordAdmin",
    "ScoutCooldownAdmin",
    "RaidRunAdmin",
    # messages
    "SendMessageForm",
    "GlobalMailCampaignForm",
    "CampaignRuntimeStatusFilter",
    "GlobalMailCampaignAdmin",
    "GlobalMailDeliveryAdmin",
    "MessageAdmin",
]
