from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.manor.core import ensure_manor
from guests.models import GearItem, GearSlot, GearTemplate, Guest, GuestStatus, GuestTemplate


def _prepare_rarity_upgrade_case(django_user_model, suffix: str):
    user = django_user_model.objects.create_user(username=f"rarity_upgrade_{suffix}", password="pass123")
    manor = ensure_manor(user)

    green_template = GuestTemplate.objects.create(
        key=f"hist_sljnbc_0589_{suffix}",
        name="邢道荣",
        archetype="military",
        rarity="green",
        base_attack=120,
        base_intellect=80,
        base_defense=100,
        base_agility=90,
        base_luck=50,
        base_hp=1200,
        growth_range=[4, 8],
        attribute_weights={"force": 40, "intellect": 30, "defense": 15, "agility": 15},
    )
    blue_template = GuestTemplate.objects.create(
        key=f"hist_sljnbc_0589_blue_{suffix}",
        name="邢道荣",
        archetype="military",
        rarity="blue",
        base_attack=150,
        base_intellect=100,
        base_defense=130,
        base_agility=110,
        base_luck=60,
        base_hp=1500,
        growth_range=[],
        attribute_weights={"force": 40, "intellect": 30, "defense": 15, "agility": 15},
        recruitable=False,
    )
    guest = Guest.objects.create(
        manor=manor,
        template=green_template,
        level=50,
        force=220,
        intellect=180,
        defense_stat=200,
        agility=170,
        luck=60,
        loyalty=80,
        gender="male",
        morality=60,
        status=GuestStatus.IDLE,
        initial_force=80,
        initial_intellect=70,
        initial_defense=75,
        initial_agility=72,
    )

    item_template = ItemTemplate.objects.create(
        key=f"rarity_upgrade_item_tpl_{suffix}",
        name="进击的无双上将",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "upgrade_guest_rarity",
            "target_template_map": {
                green_template.key: blue_template.key,
            },
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=item_template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    return manor, guest, item, green_template, blue_template


def _prepare_rarity_upgrade_blue_to_purple_case(django_user_model, suffix: str):
    user = django_user_model.objects.create_user(username=f"rarity_upgrade_bp_{suffix}", password="pass123")
    manor = ensure_manor(user)

    blue_template = GuestTemplate.objects.create(
        key=f"hist_sljnbc_0589_blue_{suffix}",
        name="邢道荣",
        archetype="military",
        rarity="blue",
        base_attack=150,
        base_intellect=100,
        base_defense=130,
        base_agility=110,
        base_luck=60,
        base_hp=1500,
        growth_range=[],
        attribute_weights={"force": 40, "intellect": 30, "defense": 15, "agility": 15},
        recruitable=False,
    )
    purple_template = GuestTemplate.objects.create(
        key=f"hist_sljnbc_0589_purple_{suffix}",
        name="邢道荣",
        archetype="military",
        rarity="purple",
        base_attack=180,
        base_intellect=120,
        base_defense=150,
        base_agility=130,
        base_luck=70,
        base_hp=1800,
        growth_range=[],
        attribute_weights={"force": 40, "intellect": 30, "defense": 15, "agility": 15},
        recruitable=False,
    )
    guest = Guest.objects.create(
        manor=manor,
        template=blue_template,
        level=60,
        force=280,
        intellect=220,
        defense_stat=230,
        agility=210,
        luck=65,
        loyalty=80,
        gender="male",
        morality=60,
        status=GuestStatus.IDLE,
        initial_force=100,
        initial_intellect=90,
        initial_defense=95,
        initial_agility=92,
    )

    item_template = ItemTemplate.objects.create(
        key=f"rarity_upgrade_item_tpl_bp_{suffix}",
        name="《上将的自我修养》残卷2",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "upgrade_guest_rarity",
            "target_template_map": {
                blue_template.key: purple_template.key,
            },
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=item_template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    return manor, guest, item, blue_template, purple_template


def _prepare_soul_container_case(
    django_user_model,
    suffix: str,
    *,
    guest_rarity: str,
    archetype: str,
    level: int,
    force: int,
    intellect: int,
    defense: int,
    agility: int,
    luck: int,
):
    user = django_user_model.objects.create_user(username=f"soul_container_{suffix}", password="pass123")
    manor = ensure_manor(user)

    template = GuestTemplate.objects.create(
        key=f"soul_container_guest_tpl_{suffix}",
        name=f"灵魂融合门客{suffix}",
        archetype=archetype,
        rarity=guest_rarity,
        base_attack=max(80, force - 40),
        base_intellect=max(80, intellect - 30),
        base_defense=max(80, defense - 30),
        base_agility=max(70, agility - 30),
        base_luck=max(40, luck - 10),
        base_hp=1200,
        default_gender="male",
        default_morality=60,
    )
    guest = Guest.objects.create(
        manor=manor,
        template=template,
        level=level,
        force=force,
        intellect=intellect,
        defense_stat=defense,
        agility=agility,
        luck=luck,
        loyalty=80,
        gender="male",
        morality=60,
        status=GuestStatus.IDLE,
        initial_force=max(40, force - 90),
        initial_intellect=max(40, intellect - 80),
        initial_defense=max(40, defense - 80),
        initial_agility=max(40, agility - 80),
    )

    item_template = ItemTemplate.objects.create(
        key=f"soul_container_item_tpl_{suffix}",
        name="灵魂容器",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "soul_fusion",
            "min_level": 30,
            "allowed_rarities": ["green", "blue", "purple"],
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=item_template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    return manor, guest, item


class _RangeSpyRng:
    def __init__(self):
        self.last_range = None

    def randint(self, a: int, b: int) -> int:
        self.last_range = (a, b)
        return a

    def choice(self, seq):
        return seq[0]


class _SoulFusionFixedRng:
    def randint(self, a: int, b: int) -> int:
        return (a + b) // 2

    def uniform(self, _a: float, _b: float) -> float:
        return 0.0


def _attach_soul_fusion_gear_state(
    guest: Guest,
    *,
    manor,
    key: str,
    name: str,
    slot: str = GearSlot.WEAPON,
    rarity: str = "green",
    extra_stats: dict | None = None,
    set_bonus: dict | None = None,
) -> ItemTemplate:
    extra_stats = extra_stats or {}
    set_bonus = set_bonus or {}

    item_template = ItemTemplate.objects.create(
        key=key,
        name=name,
        effect_type="equip_weapon" if slot == GearSlot.WEAPON else "equip_ornament",
        rarity=rarity,
    )
    gear_template = GearTemplate.objects.create(
        key=key,
        name=name,
        slot=slot,
        rarity=rarity,
        extra_stats=extra_stats,
    )
    GearItem.objects.create(manor=manor, template=gear_template, guest=guest)

    guest.force += int(extra_stats.get("force", 0) or 0) + int(set_bonus.get("force", 0) or 0)
    guest.intellect += int(extra_stats.get("intellect", 0) or 0) + int(set_bonus.get("intellect", 0) or 0)
    guest.defense_stat += int(extra_stats.get("defense", 0) or 0) + int(set_bonus.get("defense", 0) or 0)
    guest.agility += int(extra_stats.get("agility", 0) or 0) + int(set_bonus.get("agility", 0) or 0)
    guest.luck += int(extra_stats.get("luck", 0) or 0) + int(set_bonus.get("luck", 0) or 0)
    guest.gear_set_bonus = set_bonus
    guest.save(update_fields=["force", "intellect", "defense_stat", "agility", "luck", "gear_set_bonus"])

    return item_template
