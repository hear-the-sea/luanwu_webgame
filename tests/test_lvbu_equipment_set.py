from __future__ import annotations

import random
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import strip_tags
from PIL import Image

from battle.combatants_pkg.core import Combatant
from battle.combatants_pkg.guest_builder import build_guest_combatants
from battle.modifier_lifecycle import clear_modifier_scope
from battle.passives import run_passives_for_timing
from battle.simulation.attack_execution import perform_attack
from battle.simulation.battle_flow import simulate_battle
from battle.view_helpers import build_rounds_display
from core.game_data.equipment_value import item_power
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.equipment_affixes import get_or_create_equipment_affix_template
from gameplay.services.manor.core import ensure_manor
from guests.models import GearItem, GearSlot, GearTemplate, Guest, GuestArchetype, GuestRarity, GuestTemplate
from guests.services.equipment import equip_guest, unequip_guest_item
from guests.services.equipment_payloads import build_gear_template_defaults
from guests.templatetags import guest_extras
from guests.utils.equipment_utils import EQUIP_SLOT_MAP, compute_set_bonus, compute_set_combat_effects

DATA = Path(__file__).resolve().parents[1] / "data"
lvbu_276 = import_module("gameplay.migrations.0276_lvbu_equipment_set")
lvbu_277 = import_module("gameplay.migrations.0277_lvbu_set_concise_effect_description")

LVBU_SET_KEYS = (
    "equip_shufazijinkuan",
    "equip_longlinzhanjia",
    "equip_lvbuzhanxue",
    "equip_fangtianhuaji",
    "equip_chituma",
)


class _SequenceRng:
    def __init__(self, values):
        self._values = list(values)
        self._idx = 0

    def random(self):
        if self._idx < len(self._values):
            val = self._values[self._idx]
            self._idx += 1
            return val
        return 0.99

    def uniform(self, low, _high):
        return low

    def choice(self, seq):
        return seq[0]

    def randint(self, low, _high):
        return low

    def shuffle(self, _seq):
        return None


def _load_yaml_items() -> dict[str, dict]:
    return {row["key"]: row for row in yaml.safe_load((DATA / "item_templates.yaml").read_text())["items"]}


def _setup_lvbu_templates():
    yaml_items = _load_yaml_items()
    item_templates = {}
    gear_templates = {}
    for key in LVBU_SET_KEYS:
        row = yaml_items[key]
        item = ItemTemplate.objects.update_or_create(
            key=key,
            defaults={
                "name": row["name"],
                "description": row["description"],
                "effect_type": row["effect_type"],
                "effect_payload": row["effect_payload"],
                "rarity": row["rarity"],
                "tradeable": row["tradeable"],
                "price": row["price"],
                "storage_space": row["storage_space"],
                "image": row["image"],
            },
        )[0]
        item_templates[key] = item
        slot = EQUIP_SLOT_MAP[row["effect_type"]]
        gear_templates[key] = GearTemplate.objects.update_or_create(
            key=key,
            defaults=build_gear_template_defaults(item, slot=slot),
        )[0]
    return item_templates, gear_templates


def test_lvbu_set_stats_images_and_restrained_purple_comparison():
    items = _load_yaml_items()
    for key in LVBU_SET_KEYS:
        payload = items[key]["effect_payload"]
        assert payload["set_key"] == "lvbu_set"
        assert payload["set_description"] == "吕布套装"
        assert payload["set_bonus"]["pieces"] == 4
        assert payload["set_bonus"]["bonus"] == {}
        assert len(payload["set_bonus"]["combat_effects"]) == 1

    # 新冠、新靴图片为 412x412 浅黄底手绘 PNG，非空白占位图。
    for key in ("equip_shufazijinkuan", "equip_lvbuzhanxue"):
        image_path = DATA / "images" / "items" / items[key]["image"]
        assert image_path.is_file()
        with Image.open(image_path) as im:
            assert im.size == (412, 412)
            bg = im.getpixel((0, 0))
            assert bg[0] >= 240 and bg[1] >= 230 and bg[2] >= 195

    # 新加入的冠靴白值保持克制，与紫色套装单件处于温和递进区间，且 4 件套无额外属性堆叠。
    qilin_shoes = item_power(items["equip_qilinxue"]["effect_payload"], archetype="military", stage="late")
    qilin_helm = item_power(items["equip_qilinkui"]["effect_payload"], archetype="military", stage="late")
    lvbu_helm = item_power(items["equip_shufazijinkuan"]["effect_payload"], archetype="military", stage="late")
    lvbu_shoes = item_power(items["equip_lvbuzhanxue"]["effect_payload"], archetype="military", stage="late")
    assert qilin_helm < lvbu_helm < qilin_helm * 1.35
    assert qilin_shoes < lvbu_shoes < qilin_shoes * 1.15


def test_lvbu_set_summary_tooltip_and_shop_render_combat_effect_text():
    items = _load_yaml_items()
    item_tpl = ItemTemplate(
        key="equip_lvbuzhanxue",
        name="吕布战靴",
        effect_type="equip_shoes",
        effect_payload=items["equip_lvbuzhanxue"]["effect_payload"],
    )
    inv_item = InventoryItem(template=item_tpl)
    assert "吕布套装（4件）：近战受击有40%概率以6.5倍武力反击" in inv_item.effect_summary

    gear_tpl = GearTemplate(
        key="equip_lvbuzhanxue",
        **build_gear_template_defaults(item_tpl, slot=GearSlot.SHOES),
    )
    assert "吕布套装（4件）：近战受击有40%概率以6.5倍武力反击" in guest_extras.gear_summary(gear_tpl)

    html = str(
        guest_extras.gear_tooltip(
            gear_tpl,
            {
                "lvbu_set": {
                    "description": "吕布套装",
                    "members": [{"slot": "鞋子", "name": "吕布战靴", "equipped": True}],
                    "bonus": gear_tpl.set_bonus,
                }
            },
        )
    )
    assert "4件套属性：" in html
    assert "近战受击有40%概率以6.5倍武力反击" in html

    shop_html = render_to_string(
        "trade/partials/_shop.html",
        {
            "shop_items": [
                SimpleNamespace(
                    key="equip_lvbuzhanxue",
                    name="吕布战靴",
                    description="战靴",
                    price=48000,
                    stock_display="1",
                    available=True,
                    image_url="",
                    category="装备",
                    rarity="orange",
                    effect_payload=items["equip_lvbuzhanxue"]["effect_payload"],
                    troop_stat_bonus_summary="",
                )
            ],
            "sell_items": [],
        },
    )
    assert "4 件套" in shop_html
    assert "近战受击有40%概率以6.5倍武力反击" in shop_html


@pytest.mark.django_db
def test_lvbu_5_choose_4_set_activates_only_at_4_pieces_and_supports_affix_and_chiyoujia():
    User = get_user_model()
    user = User.objects.create_user(username="lvbu_set_owner", password="pw")
    manor = ensure_manor(user)
    guest_tpl = GuestTemplate.objects.create(
        key="lvbu_set_test_hero",
        name="测试猛将",
        rarity=GuestRarity.ORANGE,
        archetype=GuestArchetype.MILITARY,
        base_hp=1200,
        base_attack=120,
        base_intellect=40,
        base_defense=80,
        base_agility=70,
        base_luck=50,
    )
    guest = Guest.objects.create(
        manor=manor,
        template=guest_tpl,
        level=80,
        force=420,
        intellect=120,
        defense_stat=320,
        agility=150,
        luck=90,
        current_hp=18000,
    )
    item_templates, gear_templates = _setup_lvbu_templates()

    # 3 件不触发任何战斗被动，也不增加平白四维套装属性。
    for key in ("equip_shufazijinkuan", "equip_lvbuzhanxue", "equip_fangtianhuaji"):
        gear = GearItem.objects.create(manor=manor, template=gear_templates[key])
        equip_guest(gear, guest)
    guest.refresh_from_db()
    assert guest.gear_set_bonus == {}
    assert compute_set_combat_effects(guest.gear_items.select_related("template")) == []
    assert build_guest_combatants([guest], "defender")[0].skills == []

    # 穿赤兔马凑齐 5 选 4（故意留空护甲给蚩尤甲），激活 4 件套战斗反击被动且仍无白值堆叠。
    chitu = GearItem.objects.create(manor=manor, template=gear_templates["equip_chituma"])
    equip_guest(chitu, guest)
    guest.refresh_from_db()
    assert guest.gear_set_bonus == {}
    combatant = build_guest_combatants([guest], "defender")[0]
    assert len(combatant.skills) == 1
    assert combatant.skills[0]["key"] == "set_bonus:lvbu_set:4"

    # 卸下一件换词缀版方天画戟仍应识别为 lvbu_set 4 件套。
    unequip_guest_item(guest.gear_items.get(template__key="equip_fangtianhuaji"), guest)
    affix_item = get_or_create_equipment_affix_template(item_templates["equip_fangtianhuaji"], "force")
    assert affix_item is not None
    affix_gear_tpl = GearTemplate.objects.create(
        key=affix_item.key,
        **build_gear_template_defaults(affix_item, slot=GearSlot.WEAPON),
    )
    affix_gear = GearItem.objects.create(manor=manor, template=affix_gear_tpl)
    equip_guest(affix_gear, guest)
    guest.refresh_from_db()
    assert compute_set_bonus(guest.gear_items.select_related("template")) == {}
    assert len(compute_set_combat_effects(guest.gear_items.select_related("template"))) == 1


@pytest.mark.django_db
def test_lvbu_set_melee_force_counter_mechanics_ranged_block_defense_and_single_action_cap():
    User = get_user_model()
    user = User.objects.create_user(username="lvbu_battle_user", password="pw")
    manor = ensure_manor(user)
    _setup_lvbu_templates()
    warrior_tpl = GuestTemplate.objects.create(
        key="lvbu_warrior_tpl",
        name="高武测试将",
        rarity=GuestRarity.ORANGE,
        archetype=GuestArchetype.MILITARY,
        base_hp=1200,
    )
    civil_tpl = GuestTemplate.objects.create(
        key="lvbu_civil_tpl",
        name="文官测试将",
        rarity=GuestRarity.ORANGE,
        archetype=GuestArchetype.CIVIL,
        base_hp=1200,
    )

    def _equipped_guest(tpl, force, intellect):
        g = Guest.objects.create(
            manor=manor,
            template=tpl,
            level=80,
            force=force,
            intellect=intellect,
            defense_stat=320,
            agility=150,
            luck=90,
            current_hp=18000,
        )
        for key in ("equip_shufazijinkuan", "equip_lvbuzhanxue", "equip_fangtianhuaji", "equip_chituma"):
            gt = GearTemplate.objects.get(key=key)
            equip_guest(GearItem.objects.create(manor=manor, template=gt), g)
        g.refresh_from_db()
        return g

    warrior = build_guest_combatants([_equipped_guest(warrior_tpl, force=420, intellect=100)], "defender")[0]
    scholar = build_guest_combatants([_equipped_guest(civil_tpl, force=100, intellect=500)], "defender")[0]
    for unit in (warrior, scholar):
        run_passives_for_timing(
            "battle_start",
            actor=unit,
            target=None,
            attacker_team=[],
            defender_team=[unit],
            round_no=1,
            event_sink=[],
            rng=_SequenceRng([]),
        )

    attacker = Combatant(
        name="敌方先锋",
        attack=300,
        defense=420,
        hp=20000,
        max_hp=20000,
        side="attacker",
        rarity="orange",
        luck=80,
        agility=100,
        priority=0,
        kind="guest",
        troop_strength=0,
        template_key="enemy_vanguard",
        force_attr=350,
        defense_attr=420,
    )
    # 概率 40%：0.55 未触发 -> 无反击；0.20 触发 -> 走武力与对方防御减伤，且显式标记无双反击。
    # attack_execution 调用 rng 顺序：crit(>=0.05) + dodge + crit + counter_roll
    missed_event = perform_attack(attacker, [attacker], [warrior], _SequenceRng([0.99, 0.99, 0.99, 0.55]))
    assert missed_event["counter_damage"] == 0

    hp_before = attacker.hp
    # rng: dodge=0.99, variance=0.9, crit=0.99, counter_roll=0.20
    hit_event = perform_attack(attacker, [attacker], [warrior], _SequenceRng([0.99, 0.99, 0.99, 0.20]))
    warrior_counter = hit_event["counter_damage"]
    assert warrior_counter > 0
    assert hit_event["counter_effect"] == "无双反击"
    assert hp_before - attacker.hp == warrior_counter

    # 同装备换高防敌人时反击伤害会降低，换文官（低武高智）穿吕布套时反击显著更低。
    tank_attacker = Combatant(
        name="高防甲将",
        attack=300,
        defense=900,
        hp=20000,
        max_hp=20000,
        side="attacker",
        rarity="orange",
        luck=80,
        agility=100,
        priority=0,
        kind="guest",
        troop_strength=0,
        template_key="enemy_tank",
    )
    tank_event = perform_attack(tank_attacker, [tank_attacker], [warrior], _SequenceRng([0.99, 0.99, 0.99, 0.20]))
    assert 0 < tank_event["counter_damage"] < warrior_counter

    scholar_event = perform_attack(attacker, [attacker], [scholar], _SequenceRng([0.99, 0.99, 0.99, 0.20]))
    assert warrior_counter > scholar_event["counter_damage"] * 2

    # 远程攻击（弓兵在 -1 / -2 回合）即使 rng=0.0 也不得触发反击。
    archer = Combatant(
        name="敌方神射",
        attack=5000,
        defense=10,
        hp=50000,
        max_hp=50000,
        side="attacker",
        rarity="orange",
        luck=0,
        agility=100,
        priority=-1,
        kind="troop",
        troop_class="gong",
        troop_strength=500,
        initial_troop_strength=500,
        unit_attack=10,
        unit_defense=10,
        unit_hp=100,
        template_key="arrow_god",
    )
    ranged_event = perform_attack(archer, [archer], [warrior], _SequenceRng([0.99, 0.99, 0.99, 0.0]), round_priority=-1)
    assert ranged_event["attack_type"] == "ranged"
    assert ranged_event.get("counter_damage", 0) == 0

    # 护院满 2000 拆成 3 次分队连续打击时，单次主动行动对吕布套门客最多判定 1 次反击。
    grouped_dao = Combatant(
        name="刀圣",
        attack=20000,
        defense=10,
        hp=120000,
        max_hp=120000,
        side="attacker",
        rarity="orange",
        luck=0,
        agility=100,
        priority=0,
        kind="troop",
        troop_class="dao",
        troop_strength=2000,
        initial_troop_strength=2000,
        unit_attack=10,
        unit_defense=10,
        unit_hp=60,
        template_key="dao_sheng",
    )
    warrior.hp = warrior.max_hp = 500000
    # dao 对门客无克制目标筛选（0 次 random），每段消耗 dodge, crit, counter 共 3 次 random。
    grouped_event = perform_attack(
        grouped_dao,
        [grouped_dao],
        [warrior],
        _SequenceRng([0.99, 0.99, 0.20, 0.99, 0.99, 0.20, 0.99, 0.99, 0.20]),
    )
    all_segments = [grouped_event, *grouped_event.get("additional_attacks", [])]
    assert len(all_segments) == 3
    assert sum(1 for seg in all_segments if seg.get("counter_damage", 0) > 0) == 1
    assert grouped_event["counter_effect"] == "无双反击"

    # 首次概率未命中也必须消耗本次行动的机会，不能在同一次分队第二段重掷。
    missed_grouped = perform_attack(
        grouped_dao,
        [grouped_dao],
        [warrior],
        _SequenceRng([0.99, 0.99, 0.85, 0.99, 0.99, 0.05, 0.99, 0.99, 0.05]),
    )
    assert (
        sum(
            1
            for seg in [missed_grouped, *missed_grouped.get("additional_attacks", [])]
            if seg.get("counter_damage", 0) > 0
        )
        == 0
    )

    # 门客触发主动技能（burst）+ 普攻（normal）两段连续打击同一次行动时，也至多反击 1 次。
    skill_attacker = Combatant(
        name="敌方狂战",
        attack=300,
        defense=420,
        hp=40000,
        max_hp=40000,
        side="attacker",
        rarity="orange",
        luck=100,
        agility=100,
        priority=0,
        kind="guest",
        troop_strength=0,
        template_key="enemy_burst_guest",
        skills=[
            {
                "key": "burst_hit",
                "name": "裂甲斩",
                "kind": "active",
                "power": 150,
                "targets": 1,
                "damage_formula": {"base": 200, "ally": {"force": 2.0}, "enemy": {"defense": 0.2}},
                "status_effect": "",
                "status_probability": 0.0,
                "status_duration": 1,
                "trigger_chance": 1.0,
                "passive_config": {"active": {"trigger_chance": 1.0}},
            }
        ],
    )
    warrior.hp = warrior.max_hp = 500000
    # 门客 burst: target_weight(0.99), burst_trigger(0.15), dodge(0.99), crit(0.99), counter(0.20)
    # 随后 normal: target_weight(0.99), dodge(0.99), crit(0.99), counter(0.20)
    burst_event = perform_attack(
        skill_attacker,
        [skill_attacker],
        [warrior],
        _SequenceRng([0.99, 0.15, 0.99, 0.99, 0.20, 0.99, 0.99, 0.99, 0.20]),
    )
    burst_segments = [burst_event, *burst_event.get("additional_attacks", [])]
    assert len(burst_segments) == 2
    assert sum(1 for seg in burst_segments if seg.get("counter_damage", 0) > 0) == 1

    # 战报渲染必须显示“无双反击”而非默认“反戈一击”。
    display = build_rounds_display([{"events": [hit_event]}], {})[0]
    report_html = render_to_string(
        "battle/partials/round_event_list.html",
        {"events": display["attacker_events"], "my_side": "attacker"},
    )
    text = "".join(strip_tags(report_html).split())
    assert "爆发无双反击" in text
    assert "反戈一击" not in text
    clear_modifier_scope(warrior, "battle")

    # 端到端 simulate_battle 验证：通过 build_guest_combatants 入场并在实战斗中触发无双反击。
    e2e_lvbu = build_guest_combatants([_equipped_guest(warrior_tpl, force=420, intellect=100)], "defender")[0]
    e2e_enemy = Combatant(
        name="演武敌将",
        attack=420,
        defense=380,
        hp=20000,
        max_hp=20000,
        side="attacker",
        rarity="orange",
        luck=80,
        agility=100,
        priority=0,
        kind="guest",
        troop_strength=0,
        template_key="enemy_e2e",
    )
    sim_result = simulate_battle([e2e_enemy], [e2e_lvbu], random.Random(7), 7, 0, {"max_rounds": 15})
    counter_logs = [
        ev
        for rnd in sim_result.rounds
        for ev in rnd.get("events", [])
        if ev.get("counter_damage", 0) > 0 and ev.get("counter_effect") == "无双反击"
    ]
    assert counter_logs
    assert sim_result.winner == "defender"

    # 任务/掠夺快照（BattleGuestSnapshotProxy）与竞技场快照（ArenaGuestSnapshotProxy）均须保留吕布套反击被动。
    from gameplay.services.arena.snapshots import ArenaGuestSnapshotProxy, build_entry_guest_snapshot
    from gameplay.services.battle_snapshots import BattleGuestSnapshotProxy, build_guest_battle_snapshot

    live_lvbu_guest = _equipped_guest(warrior_tpl, force=420, intellect=100)
    battle_snap = build_guest_battle_snapshot(live_lvbu_guest, include_identity=True)
    arena_snap = build_entry_guest_snapshot(live_lvbu_guest)
    assert "set_combat_effects" in battle_snap
    assert "set_combat_effects" in arena_snap

    for proxy in (
        BattleGuestSnapshotProxy(battle_snap, include_guest_identity=True),
        BattleGuestSnapshotProxy(
            {k: v for k, v in battle_snap.items() if k != "set_combat_effects"},
            include_guest_identity=True,
        ),
        ArenaGuestSnapshotProxy(arena_snap),
    ):
        proxy_combatant = build_guest_combatants([proxy], "defender")[0]
        assert any(s.get("key") == "set_bonus:lvbu_set:4" for s in proxy_combatant.skills)


@pytest.mark.django_db
def test_0276_lvbu_equipment_set_migration_syncs_items_affixes_and_chests_idempotently(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    yaml_items = _load_yaml_items()
    for key in (
        "equip_fangtianhuaji",
        "equip_longlinzhanjia",
        "equip_chituma",
        "mission_chest_hulao_qunying_zhan_lvbu",
        "mission_chest_v3_hulao_qunying_zhan_lvbu",
        "mission_chest_baimenlou_mingyun_jueze",
        "mission_chest_v3_baimenlou_mingyun_jueze",
    ):
        row = yaml_items[key]
        old_payload = dict(row["effect_payload"])
        if key.startswith("equip_"):
            old_payload.pop("set_key", None)
            old_payload.pop("set_description", None)
            old_payload.pop("set_bonus", None)
        else:
            old_payload["random_item_groups"] = [
                g for g in old_payload["random_item_groups"] if g.get("label") != "飞将冠靴"
            ]
        ItemTemplate.objects.create(
            key=key,
            name=row["name"],
            description=row["description"],
            effect_type=row["effect_type"],
            effect_payload=old_payload,
            rarity=row["rarity"],
            tradeable=row["tradeable"],
            price=row["price"],
            storage_space=row["storage_space"],
            is_usable=row.get("is_usable", False),
        )
        if key.startswith("equip_"):
            GearTemplate.objects.create(
                key=key,
                **build_gear_template_defaults(
                    ItemTemplate.objects.get(key=key), slot=EQUIP_SLOT_MAP[row["effect_type"]]
                ),
            )

    halberd = ItemTemplate.objects.get(key="equip_fangtianhuaji")
    affix_halberd = get_or_create_equipment_affix_template(halberd, "force")
    assert affix_halberd is not None

    lvbu_276.apply_lvbu_equipment_set(apps, SimpleNamespace(connection=connection))
    lvbu_276.apply_lvbu_equipment_set(apps, SimpleNamespace(connection=connection))
    lvbu_277.apply_concise_lvbu_effect_description(apps, SimpleNamespace(connection=connection))
    lvbu_277.apply_concise_lvbu_effect_description(apps, SimpleNamespace(connection=connection))

    for key in LVBU_SET_KEYS:
        item = ItemTemplate.objects.get(key=key)
        gear = GearTemplate.objects.get(key=key)
        assert item.effect_payload == yaml_items[key]["effect_payload"]
        assert gear.set_key == "lvbu_set"
        assert gear.set_description == "吕布套装"
        assert gear.set_bonus == yaml_items[key]["effect_payload"]["set_bonus"]

    for key in ("equip_shufazijinkuan", "equip_lvbuzhanxue"):
        assert str(ItemTemplate.objects.get(key=key).image).endswith(".webp")

    affix_halberd.refresh_from_db()
    assert affix_halberd.effect_payload["set_key"] == "lvbu_set"
    assert affix_halberd.effect_payload["set_bonus"]["effect_description"] == "近战受击有40%概率以6.5倍武力反击"
    assert GearTemplate.objects.get(key=affix_halberd.key).set_key == "lvbu_set"

    for chest_key in (
        "mission_chest_hulao_qunying_zhan_lvbu",
        "mission_chest_v3_hulao_qunying_zhan_lvbu",
        "mission_chest_baimenlou_mingyun_jueze",
        "mission_chest_v3_baimenlou_mingyun_jueze",
    ):
        chest = ItemTemplate.objects.get(key=chest_key)
        assert chest.effect_payload == yaml_items[chest_key]["effect_payload"]


@pytest.mark.django_db
def test_lvbu_set_warehouse_and_guild_hero_pool_render_effect_description(client):
    yaml_items = _load_yaml_items()
    _item_templates, gear_templates = _setup_lvbu_templates()
    user = get_user_model().objects.create_user(username="lvbu_wh_viewer", password="pw")
    manor = ensure_manor(user)
    boots_tpl = ItemTemplate.objects.get(key="equip_lvbuzhanxue")
    InventoryItem.objects.create(manor=manor, template=boots_tpl, quantity=1, bound_quantity=1)

    qilin_row = yaml_items["equip_qilinxue"]
    qilin_tpl = ItemTemplate.objects.create(
        key="equip_qilinxue",
        name=qilin_row["name"],
        description=qilin_row["description"],
        effect_type=qilin_row["effect_type"],
        effect_payload=qilin_row["effect_payload"],
        rarity=qilin_row["rarity"],
        tradeable=qilin_row["tradeable"],
        price=qilin_row["price"],
        storage_space=qilin_row["storage_space"],
    )
    InventoryItem.objects.create(manor=manor, template=qilin_tpl, quantity=1)

    client.force_login(user)
    response = client.get(reverse("gameplay:warehouse"))
    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "套装：吕布套装" in body
    assert "4件套效果：" in body
    assert "近战受击有40%概率以6.5倍武力反击" in body
    assert "（过防御减伤" not in body
    assert "麒麟套装" in body

    guild_detail_html = render_to_string(
        "guilds/partials/hero_pool_guest_detail.html",
        {
            "guest": SimpleNamespace(
                display_name="吕布",
                rarity=GuestRarity.ORANGE,
                level=80,
                avatar_url="",
            ),
            "health_stat": {"current": 1000, "max": 1000},
            "owner_name": "tester",
            "basic_stats": [],
            "equipment_panels": [
                {
                    "value": "shoes",
                    "label": "鞋子",
                    "items": [SimpleNamespace(id=1, template=gear_templates["equip_lvbuzhanxue"])],
                }
            ],
            "skill_count": 0,
            "skill_capacity": 4,
            "skill_slots": [],
        },
    )
    assert "4件套属性：近战受击有40%概率以6.5倍武力反击" in guild_detail_html
