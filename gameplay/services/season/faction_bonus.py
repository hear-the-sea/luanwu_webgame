"""赛季阵营加成及临时生命与真实生命之间的换算。"""

from types import SimpleNamespace

from guests.constants import RARITY_CONVERSION_TEMPLATE_KEY_PREFIX, UPGRADE_TEMPLATE_FAMILIES
from guests.guest_rules import compute_guest_attack_attribute_component, compute_guest_max_hp
from guests.models import GuestTemplate

from .catalog import ensure_faction_roster_snapshot_locked, faction_template_keys


def source_key(template):
    key = template.key
    if key.startswith(RARITY_CONVERSION_TEMPLATE_KEY_PREFIX):
        source_id = key.rsplit("_", 1)[-1]
        if source_id.isdigit():
            key = GuestTemplate.objects.filter(pk=int(source_id)).values_list("key", flat=True).first() or key
    for family in UPGRADE_TEMPLATE_FAMILIES:
        if key in family:
            return family[0]
    return key


def apply_faction_bonus(guest, snapshot, faction_keys):
    if source_key(guest.template) not in faction_keys:
        return
    boosted = {name: snapshot[name] * 110 // 100 for name in ("force", "intellect", "defense_stat", "agility", "luck")}
    attack_component = compute_guest_attack_attribute_component(
        SimpleNamespace(force=boosted["force"], intellect=boosted["intellect"], archetype=guest.archetype)
    )
    defense_gain = boosted["defense_stat"] - snapshot["defense_stat"]
    hp = compute_guest_max_hp(
        SimpleNamespace(
            template=guest.template, hp_bonus=guest.hp_bonus, defense_stat=boosted["defense_stat"], level=guest.level
        )
    )
    base_hp = snapshot["max_hp"]
    snapshot.update(boosted)
    snapshot["attack"] += attack_component - snapshot["attack_attribute_component"]
    snapshot["attack_attribute_component"] = attack_component
    snapshot["defense"] += defense_gain
    snapshot["max_hp"] += hp - compute_guest_max_hp(guest)
    snapshot["faction_bonus_base_hp"] = base_hp
    snapshot["current_hp"] = battle_hp(snapshot, snapshot["current_hp"])
    snapshot["faction_bonus_percent"] = 10


def battle_hp(snapshot, hp):
    base = snapshot.get("faction_bonus_base_hp")
    if not base:
        return hp
    return min(snapshot["max_hp"], (max(0, hp) * snapshot["max_hp"] + base - 1) // base)


def real_hp(snapshot, hp):
    base = snapshot.get("faction_bonus_base_hp")
    if not base:
        return hp
    if hp <= 0:
        return 0
    return max(1, min(base, hp * base // snapshot["max_hp"]))


def apply_lineup_bonus(member, guests, snapshots):
    if member.faction not in ("fiction", "history"):
        return
    ensure_faction_roster_snapshot_locked(member.season)
    keys = faction_template_keys(member.faction, rules=member.season.rules)
    for guest, snapshot in zip(guests, snapshots):
        apply_faction_bonus(guest, snapshot, keys)
