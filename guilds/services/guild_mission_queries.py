from __future__ import annotations

from typing import Any

from django.db.models import Q
from django.utils import timezone

from gameplay.utils.template_loader import get_troop_templates_by_keys
from guests.models import GuestTemplate

from ..models import GuildBattleLineupEntry, GuildMember, GuildMissionRun, GuildMissionTemplate, GuildTroopStorage
from .technology import get_guild_dispatch_capacity, get_guild_lineup_capacity


def _extract_enemy_guest_key(entry: Any) -> str:
    if isinstance(entry, str):
        return entry.strip()
    if isinstance(entry, dict):
        return str(entry.get("key") or entry.get("template_key") or "").strip()
    return ""


def _collect_mission_enemy_asset_keys(selected_mission: GuildMissionTemplate | None) -> tuple[set[str], set[str]]:
    if selected_mission is None:
        return set(), set()

    guest_keys = {
        key for key in (_extract_enemy_guest_key(entry) for entry in (selected_mission.enemy_guests or [])) if key
    }
    troop_keys = {str(key).strip() for key in (selected_mission.enemy_troops or {}).keys() if str(key).strip()}
    return guest_keys, troop_keys


def _normalize_enemy_guest_entries(
    raw_enemy_guests: Any,
    guest_templates: dict[str, GuestTemplate],
) -> list[dict[str, str]]:
    normalized_entries: list[dict[str, str]] = []
    if not isinstance(raw_enemy_guests, list):
        return normalized_entries

    for entry in raw_enemy_guests:
        key = _extract_enemy_guest_key(entry)
        if not key:
            continue

        label = ""
        if isinstance(entry, dict):
            label = str(entry.get("label") or "").strip()
        if not label:
            template = guest_templates.get(key)
            label = template.name if template is not None else key

        normalized_entries.append({"key": key, "label": label})

    return normalized_entries


def get_guild_mission_page_context(
    member: GuildMember,
    *,
    selected_mission_key: str = "",
    now=None,
) -> dict[str, Any]:
    guild = member.guild
    resolved_now = now or timezone.now()
    active_run = (
        GuildMissionRun.objects.select_related("template", "started_by__user__manor")
        .filter(guild=guild, status=GuildMissionRun.Status.ACTIVE)
        .filter(Q(return_at__isnull=True) | Q(return_at__gt=resolved_now))
        .order_by("-started_at")
        .first()
    )
    mission_templates = list(GuildMissionTemplate.objects.filter(is_active=True).order_by("sort_weight", "id"))
    lineup_entries = list(
        GuildBattleLineupEntry.objects.filter(guild=guild)
        .select_related("pool_entry__source_guest__template", "pool_entry__owner_member__user__manor")
        .order_by("slot_index", "id")
    )
    troop_storages = list(
        GuildTroopStorage.objects.filter(guild=guild, count__gt=0)
        .select_related("troop_template")
        .order_by("troop_template__priority", "troop_template__id")
    )

    mission_groups = {
        "junior": [mission for mission in mission_templates if mission.difficulty == "junior"],
        "intermediate": [mission for mission in mission_templates if mission.difficulty == "intermediate"],
        "advanced": [mission for mission in mission_templates if mission.difficulty == "advanced"],
    }
    selected_mission = next(
        (mission for mission in mission_templates if mission.key == selected_mission_key),
        None,
    )
    active_tab = selected_mission.difficulty if selected_mission else "junior"
    enemy_guest_keys, enemy_troop_keys = _collect_mission_enemy_asset_keys(selected_mission)
    guest_templates = {
        tpl.key: tpl
        for tpl in GuestTemplate.objects.filter(key__in=enemy_guest_keys).only("key", "name", "avatar", "rarity")
    }
    troop_templates_objs = get_troop_templates_by_keys(enemy_troop_keys)
    normalized_enemy_guests = _normalize_enemy_guest_entries(
        getattr(selected_mission, "enemy_guests", None),
        guest_templates,
    )
    return {
        "guild": guild,
        "member": member,
        "active_run": active_run,
        "mission_templates": mission_templates,
        "mission_groups": mission_groups,
        "selected_mission": selected_mission,
        "active_tab": active_tab,
        "lineup_entries": lineup_entries,
        "troop_storages": troop_storages,
        "dispatch_limit": get_guild_dispatch_capacity(guild),
        "lineup_limit": get_guild_lineup_capacity(guild),
        "selected_enemy_guests": normalized_enemy_guests,
        "guest_templates": guest_templates,
        "guest_labels": {key: tpl.name for key, tpl in guest_templates.items()},
        "troop_templates_objs": troop_templates_objs,
        "troop_labels": {key: troop.name for key, troop in troop_templates_objs.items()},
    }
