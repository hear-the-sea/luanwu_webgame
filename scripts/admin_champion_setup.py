"""管理员阵容配置：默认只读预览，--apply 时事务写入并保存配置前快照。"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import random
import sys
from collections import Counter
from pathlib import Path


def initialize():
    root = Path(__file__).resolve().parent.parent
    if (root / "manage.py").exists():
        sys.path.insert(0, str(root))
    else:
        sys.path.insert(0, os.environ.get("WEBGAME_PROJECT_PATH", "/home/daniel/code/web_game_v5"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()


def seed_for(key):
    return int.from_bytes(hashlib.sha256(("admin-champion-20261004:" + key).encode()).digest()[:8], "big")


def project_guest(manor, template, profile, gear_templates, skills, existing=None):
    from guests.growth_engine import allocate_level_up_attributes, apply_training_completion
    from guests.models import GearItem
    from guests.services.equipment_stats import apply_set_bonuses, apply_template_stats_to_guest
    from guests.services.recruitment_guests import create_guest_from_template
    from guests.services.skills import SKILL_NON_EQUIPMENT_PERCENT, assert_guest_meets_skill_requirements

    rng = random.Random(seed_for(template.key))
    if existing is None:
        guest = create_guest_from_template(manor=manor, template=template, rng=rng, grant_skills=False, save=False)
    else:
        guest = copy.copy(existing)
        guest._state = copy.copy(existing._state)
        for gear in existing.gear_items.select_related("template"):
            apply_template_stats_to_guest(guest, gear.template, -1, set())
        apply_set_bonuses(guest, gear_items=[], persist=False)
    if guest.level < 100:
        apply_training_completion(
            guest,
            levels_gained=100 - guest.level,
            allocate_level_up_attributes_func=lambda current, levels, _rng: allocate_level_up_attributes(
                current, levels, rng
            ),
        )
    bare = {"force": guest.force, "intellect": guest.intellect, "defense": guest.defense_stat, "agility": guest.agility}
    gears = []
    for key in profile["gear_keys"]:
        gear = gear_templates[key]
        apply_template_stats_to_guest(guest, gear, 1, set())
        gears.append(GearItem(template=gear))
    apply_set_bonuses(guest, gear_items=gears, persist=False)
    fields = {"force": "force", "intellect": "intellect", "defense": "defense_stat", "agility": "agility"}
    allocation = dict.fromkeys(fields, 0)
    for skill_key in profile["skill_keys"]:
        skill = skills[skill_key]
        percent = SKILL_NON_EQUIPMENT_PERCENT.get(str(skill.rarity), 0)
        for attr, field in fields.items():
            required = getattr(skill, "required_" + attr)
            naked_minimum = (required * percent + 99) // 100
            allocation[attr] = max(allocation[attr], required - getattr(guest, field), naked_minimum - bare[attr], 0)
    needed = sum(allocation.values())
    if needed > guest.attribute_points:
        raise ValueError(f"{template.name} 技能门槛需要 {needed} 点，只有 {guest.attribute_points} 点")
    allocation[profile["leftover_attribute"]] += guest.attribute_points - needed
    for attr, points in allocation.items():
        setattr(guest, fields[attr], getattr(guest, fields[attr]) + points)
        bare[attr] += points
    guest.attribute_points = 0
    for key in profile["skill_keys"]:
        assert_guest_meets_skill_requirements(guest, skills[key], non_equipment_attributes=bare)
    return {
        "template_key": template.key,
        "name": template.name,
        "existing_id": existing.pk if existing else None,
        "role": profile["role"],
        "gear_keys": profile["gear_keys"],
        "skill_keys": profile["skill_keys"],
        "allocation": allocation,
        "level": guest.level,
        "stats": guest.stat_block(),
        "force": guest.force,
        "intellect": guest.intellect,
        "defense_stat": guest.defense_stat,
        "agility": guest.agility,
        "luck": guest.luck,
        "max_hp": guest.max_hp,
        "bare_attributes": bare,
    }


def build_plan(config, username="admin"):
    from django.contrib.auth import get_user_model

    from core.game_data.technology import build_uniform_tech_levels
    from gameplay.constants import BUILDING_MAX_LEVELS
    from gameplay.models import BuildingType, ItemTemplate, Manor
    from guests.models import GearTemplate, Guest, GuestStatus, GuestTemplate, Skill
    from guests.services.equipment_stats import slot_capacity

    manor = Manor.objects.select_related("user").get(user=get_user_model().objects.get(username=username))
    if not manor.user.is_superuser:
        raise ValueError("目标必须为已核对的超级管理员账号")
    roster = config["roster"]
    if len(roster) != 20 or len({x["template_key"] for x in roster}) != 20:
        raise ValueError("必须明确配置20名不同门客")
    fast_count = sum(config["profiles"][x["profile"]]["leftover_attribute"] == "agility" for x in roster)
    if fast_count < config.get("minimum_fast_guests", 0):
        raise ValueError("敏捷先锋人数不足")
    templates = {t.key: t for t in GuestTemplate.objects.filter(key__in=[x["template_key"] for x in roster])}
    all_gear_keys = {k for x in roster for k in config["profiles"][x["profile"]]["gear_keys"]}
    all_skill_keys = {k for x in roster for k in config["profiles"][x["profile"]]["skill_keys"]}
    gear_templates = {t.key: t for t in GearTemplate.objects.filter(key__in=all_gear_keys)}
    skills = {s.key: s for s in Skill.objects.filter(key__in=all_skill_keys)}
    if (
        set(templates) != {x["template_key"] for x in roster}
        or set(gear_templates) != all_gear_keys
        or set(skills) != all_skill_keys
    ):
        raise ValueError("目标环境缺少配置中的模板")
    books = {}
    for item in ItemTemplate.objects.filter(effect_type=ItemTemplate.EffectType.SKILL_BOOK):
        if isinstance(item.effect_payload, dict) and item.effect_payload.get("skill_key") in all_skill_keys:
            books[item.effect_payload["skill_key"]] = item.key
    if set(books) != all_skill_keys:
        raise ValueError("配置技能缺少合法技能书，拒绝授予NPC专属技能")
    for profile in config["profiles"].values():
        if len(profile["skill_keys"]) != 3 or len(set(profile["skill_keys"])) != 3:
            raise ValueError("技能组合必须恰好3种")
        if not any(skills[key].kind == "active" for key in profile["skill_keys"]):
            raise ValueError("每名门客至少需要一个主动技能")
        keys = profile["gear_keys"]
        if len(set(keys)) != len(keys):
            raise ValueError("装备不允许重复")
        counts = Counter(gear_templates[k].slot for k in keys)
        if any(n != slot_capacity(slot) for slot, n in counts.items()) or len(keys) != 11:
            raise ValueError("装备必须正确填满7种槽位，共11件")
    current_guests = list(Guest.objects.filter(manor=manor).select_related("template").order_by("id"))
    chosen = {}
    for guest in current_guests:
        if guest.template.key in templates and guest.template.key not in chosen:
            chosen[guest.template.key] = guest
    rows = []
    allowed = {GuestStatus.IDLE, GuestStatus.INJURED, GuestStatus.VACATION}
    for entry in roster:
        t = templates[entry["template_key"]]
        if not t.recruitable or t.is_world_unique:
            raise ValueError("拒绝生成不可招募或全服唯一门客")
        existing = chosen.get(t.key)
        if existing and existing.status not in allowed:
            raise ValueError(f"{t.name} 正在{existing.get_status_display()}，需先完成现有活动")
        row = project_guest(manor, t, config["profiles"][entry["profile"]], gear_templates, skills, existing)
        rows.append(row)
    kept_ids = {g.pk for g in chosen.values()}
    extras = [g for g in current_guests if g.pk not in kept_ids]
    if len(extras) > 10 or any(
        g.status not in {GuestStatus.IDLE, GuestStatus.INJURED, GuestStatus.VACATION} or g.template.is_world_unique
        for g in extras
    ):
        raise ValueError("原有门客无法全部安全保留到10位逍遥山庄，请先明确旧阵容处理方式")
    buildings = []
    for t in BuildingType.objects.order_by("key"):
        if t.key not in BUILDING_MAX_LEVELS:
            raise ValueError(f"{t.key} 未定义等级上限")
        buildings.append({"key": t.key, "name": t.name, "level": BUILDING_MAX_LEVELS[t.key]})
    plan = {
        "username": username,
        "manor_id": manor.pk,
        "buildings": buildings,
        "technologies": build_uniform_tech_levels(999999),
        "roster": rows,
        "book_keys": books,
        "retreat_guest_ids": [g.pk for g in extras],
        "state_fingerprint": hashlib.sha256(
            json.dumps(
                {
                    "guests": list(manor.guests.values()),
                    "gear": list(manor.gears.values()),
                    "skills": list(manor.guests.values("id", "skills")),
                    "buildings": list(manor.buildings.values()),
                    "tech": list(manor.technologies.values()),
                },
                default=str,
                sort_keys=True,
            ).encode()
        ).hexdigest(),
    }
    plan["digest"] = hashlib.sha256(json.dumps(plan, default=str, sort_keys=True).encode()).hexdigest()
    return plan


def apply_plan(plan, config, backup_path):
    from django.core import serializers
    from django.db import transaction
    from django.utils import timezone

    from gameplay.models import Building, BuildingType, InventoryItem, ItemTemplate, Manor, PlayerTechnology
    from gameplay.services.city_defense import city_defense_max_hp, is_city_defense_key
    from gameplay.services.inventory.core import add_item_to_inventory_locked
    from gameplay.services.utils.cache import (
        invalidate_home_stats_cache,
        invalidate_manor_cache,
        invalidate_recruitment_hall_cache,
    )
    from guests.growth_engine import allocate_level_up_attributes, apply_training_completion
    from guests.models import GearTemplate, Guest, GuestSkill, GuestStatus, GuestTemplate
    from guests.services.equipment import equip_guest, give_gear, unequip_guest_item
    from guests.services.recruitment_guests import allocate_attribute_points, create_guest_from_template
    from guests.services.skills import forget_guest_skill, learn_guest_skill_locked
    from guests.services.vacation import move_guest_to_xiaoyao, return_guest_from_xiaoyao

    with transaction.atomic():
        manor = Manor.objects.select_for_update().get(pk=plan["manor_id"])
        list(Guest.objects.select_for_update().filter(manor=manor))
        fresh = build_plan(config, plan["username"])
        if fresh["digest"] != plan["digest"]:
            raise ValueError("预览之后目标玩家状态已变化，请重新预览")
        objects = (
            [manor]
            + list(manor.buildings.all())
            + list(manor.technologies.all())
            + list(manor.guests.all())
            + list(manor.gears.all())
            + list(GuestSkill.objects.filter(guest__manor=manor))
            + list(InventoryItem.objects.filter(manor=manor))
        )
        with Path(backup_path).open("x") as f:
            f.write(serializers.serialize("json", objects, indent=2))
        now = timezone.now()
        for b in plan["buildings"]:
            t = BuildingType.objects.get(key=b["key"])
            defaults = {"level": b["level"], "is_upgrading": False, "upgrade_complete_at": None}
            if is_city_defense_key(t.key):
                defaults.update(current_hp=city_defense_max_hp(t.key, b["level"]), hp_updated_at=now)
            Building.objects.update_or_create(manor=manor, building_type=t, defaults=defaults)
        for key, level in plan["technologies"].items():
            PlayerTechnology.objects.update_or_create(
                manor=manor, tech_key=key, defaults={"level": level, "is_upgrading": False, "upgrade_complete_at": None}
            )
        for guest_id in plan["retreat_guest_ids"]:
            guest = Guest.objects.get(pk=guest_id)
            if guest.status == GuestStatus.INJURED:
                guest.status = GuestStatus.IDLE
                guest.current_hp = guest.max_hp
                guest.save(update_fields=["status", "current_hp"])
            if guest.status != GuestStatus.VACATION:
                move_guest_to_xiaoyao(manor, guest_id)
        for row in plan["roster"]:
            template = GuestTemplate.objects.get(key=row["template_key"])
            rng = random.Random(seed_for(template.key))
            if row["existing_id"] is None:
                guest = create_guest_from_template(manor=manor, template=template, rng=rng, grant_skills=False)
            else:
                guest = Guest.objects.get(pk=row["existing_id"])
                if guest.status == GuestStatus.VACATION:
                    return_guest_from_xiaoyao(manor, guest.pk)
                    guest.refresh_from_db()
            guest.status = GuestStatus.IDLE
            guest.training_target_level = 0
            guest.training_complete_at = None
            guest.training_remaining_seconds = None
            guest.save(
                update_fields=["status", "training_target_level", "training_complete_at", "training_remaining_seconds"]
            )
            gear_matches = Counter(guest.gear_items.values_list("template__key", flat=True)) == Counter(
                row["gear_keys"]
            )
            if not gear_matches:
                for gear in list(guest.gear_items.select_related("template")):
                    unequip_guest_item(gear, guest)
            guest.refresh_from_db()
            if guest.level < 100:
                apply_training_completion(
                    guest,
                    levels_gained=100 - guest.level,
                    allocate_level_up_attributes_func=lambda current, levels, _rng: allocate_level_up_attributes(
                        current, levels, rng
                    ),
                )
                guest.save(
                    update_fields=[
                        "level",
                        "force",
                        "intellect",
                        "defense_stat",
                        "agility",
                        "attribute_points",
                        "experience",
                        "current_hp",
                    ]
                )
            for attr, points in row["allocation"].items():
                if points:
                    allocate_attribute_points(guest, attr, points)
            guest.refresh_from_db()
            if not gear_matches:
                for key in row["gear_keys"]:
                    equip_guest(give_gear(manor, GearTemplate.objects.get(key=key), is_bound=True), guest)
            guest.refresh_from_db()
            wanted = set(row["skill_keys"])
            for gs in list(guest.guest_skills.select_related("skill")):
                if gs.skill.key not in wanted:
                    forget_guest_skill(guest, gs.pk)
            learned = set(guest.skills.values_list("key", flat=True))
            for key in row["skill_keys"]:
                if key not in learned:
                    book = ItemTemplate.objects.get(key=plan["book_keys"][key])
                    item = add_item_to_inventory_locked(manor, book.key, template=book, is_bound=True)
                    learn_guest_skill_locked(manor, guest, item.pk)
            guest.refresh_from_db()
            guest.current_hp = guest.max_hp
            guest.loyalty = 100
            guest.save(update_fields=["current_hp", "loyalty"])
            assert guest.level == 100 and guest.attribute_points == 0
            assert set(guest.skills.values_list("key", flat=True)) == wanted
            assert Counter(guest.gear_items.values_list("template__key", flat=True)) == Counter(row["gear_keys"])
            for field in ("force", "intellect", "defense_stat", "agility", "luck", "max_hp"):
                if getattr(guest, field) != row[field]:
                    raise ValueError(f"{guest.display_name} {field} 执行结果与预览不一致")
        manor.refresh_from_db()
        assert manor.active_guest_count == 20 and manor.guest_capacity == 20
        transaction.on_commit(lambda: invalidate_manor_cache(manor.pk))
        transaction.on_commit(lambda: invalidate_home_stats_cache(manor.pk))
        transaction.on_commit(lambda: invalidate_recruitment_hall_cache(manor.pk))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--username", default="admin")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-digest")
    parser.add_argument("--backup")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    initialize()
    config = json.loads(Path(args.config).read_text())
    plan = build_plan(config, args.username)
    if args.apply:
        if not args.backup or args.expected_digest != plan["digest"]:
            parser.error("--apply 需要备份路径和匹配预览的 --expected-digest")
        apply_plan(plan, config, args.backup)
    Path(args.output).write_text(json.dumps({"applied": args.apply, "plan": plan}, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {
                "applied": args.apply,
                "username": plan["username"],
                "manor_id": plan["manor_id"],
                "digest": plan["digest"],
                "buildings": len(plan["buildings"]),
                "technologies": len(plan["technologies"]),
                "guests": len(plan["roster"]),
                "output": args.output,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
