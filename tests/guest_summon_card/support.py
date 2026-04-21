from guests.models import GuestTemplate


def make_pubayi_template(key: str, rarity: str) -> GuestTemplate:
    return GuestTemplate.objects.create(
        key=key,
        name="蒲巴乙",
        archetype="civil",
        rarity=rarity,
        base_attack=80,
        base_intellect=90,
        base_defense=70,
        base_agility=75,
        base_luck=60,
        base_hp=1000,
        default_gender="male",
        default_morality=60,
        recruitable=False,
    )


def make_edward_template(key: str, rarity: str) -> GuestTemplate:
    return GuestTemplate.objects.create(
        key=key,
        name="爱德华",
        archetype="military",
        rarity=rarity,
        base_attack=120,
        base_intellect=88,
        base_defense=95,
        base_agility=92,
        base_luck=58,
        base_hp=1320,
        default_gender="male",
        default_morality=68 if rarity == "blue" else 72,
        recruitable=False,
    )
