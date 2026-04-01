# guilds/urls.py

from django.urls import path

from .views.announcement import announcement_list, create_announcement
from .views.contribution import contribution_ranking, donate_resource, donation_logs, resource_logs, resource_status
from .views.core import create_guild, guild_detail, guild_hall, guild_info, guild_list, guild_search
from .views.hero_pool import hero_pool_page, hero_pool_remove, hero_pool_submit, lineup_add, lineup_remove
from .views.membership import (
    application_list,
    apply_to_guild,
    appoint_admin,
    approve_application,
    demote_admin,
    disband_guild,
    kick_member,
    leave_guild,
    member_list,
    reject_application,
    transfer_leadership,
    upgrade_guild,
)
from .views.missions import donate_troops, launch_mission, missions, retreat_mission
from .views.technology import technology_list, upgrade_technology
from .views.warehouse import exchange_item, exchange_logs, warehouse

app_name = "guilds"

urlpatterns = [
    # 帮会大厅
    path("", guild_hall, name="hall"),
    # 帮会列表与搜索
    path("list/", guild_list, name="list"),
    path("search/", guild_search, name="search"),
    # 创建帮会
    path("create/", create_guild, name="create"),
    # 帮会详情
    path("<int:guild_id>/", guild_detail, name="detail"),
    path("<int:guild_id>/info/", guild_info, name="info"),
    # 申请与审批
    path("<int:guild_id>/apply/", apply_to_guild, name="apply"),
    path("applications/", application_list, name="applications"),
    path("application/<int:app_id>/approve/", approve_application, name="approve_application"),
    path("application/<int:app_id>/reject/", reject_application, name="reject_application"),
    # 成员管理
    path("members/", member_list, name="members"),
    path("member/<int:member_id>/kick/", kick_member, name="kick_member"),
    path("member/<int:member_id>/appoint/", appoint_admin, name="appoint_admin"),
    path("member/<int:member_id>/demote/", demote_admin, name="demote_admin"),
    path("member/<int:member_id>/transfer/", transfer_leadership, name="transfer_leadership"),
    path("leave/", leave_guild, name="leave"),
    # 门客池
    path("hero-pool/", hero_pool_page, name="hero_pool"),
    path("hero-pool/submit/", hero_pool_submit, name="hero_pool_submit"),
    path("hero-pool/remove/", hero_pool_remove, name="hero_pool_remove"),
    path("hero-pool/lineup/add/", lineup_add, name="lineup_add"),
    path("hero-pool/lineup/remove/", lineup_remove, name="lineup_remove"),
    # 帮会任务与护院池
    path("missions/", missions, name="missions"),
    path("missions/launch/", launch_mission, name="mission_launch"),
    path("missions/retreat/", retreat_mission, name="mission_retreat"),
    path("missions/donate-troops/", donate_troops, name="donate_troops"),
    # 帮会升级
    path("upgrade/", upgrade_guild, name="upgrade"),
    path("disband/", disband_guild, name="disband"),
    # 贡献系统
    path("donate/", donate_resource, name="donate"),
    path("contribution/ranking/", contribution_ranking, name="contribution_ranking"),
    # 科技系统
    path("technology/", technology_list, name="technology"),
    path("technology/<str:tech_key>/upgrade/", upgrade_technology, name="upgrade_tech"),
    # 仓库与兑换
    path("warehouse/", warehouse, name="warehouse"),
    path("warehouse/<str:item_key>/exchange/", exchange_item, name="exchange_item"),
    path("warehouse/logs/", exchange_logs, name="exchange_logs"),
    # 公告
    path("announcements/", announcement_list, name="announcements"),
    path("announcement/create/", create_announcement, name="create_announcement"),
    # 资源与日志
    path("resources/", resource_status, name="resources"),
    path("logs/donation/", donation_logs, name="donation_logs"),
    path("logs/resource/", resource_logs, name="resource_logs"),
]
