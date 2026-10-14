from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from django.db import connections, transaction

from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestTemplate, RecruitmentCandidate


@pytest.mark.django_db
@pytest.mark.parametrize("hp_ratio", [0, 0.5, 1])
def test_lier_promotion_preserves_player_stats_and_hp_ratio(django_user_model, hp_ratio):
    manor = ensure_manor(django_user_model.objects.create_user(username="lier_promotion"))
    template = GuestTemplate.objects.create(
        key="hist_xianqin_0007",
        name="李耳",
        rarity="purple",
        archetype="civil",
        base_attack=42,
        base_intellect=46,
        base_defense=22,
        base_agility=48,
        base_luck=141,
        base_hp=800,
        growth_range=[7, 12],
    )
    guest = Guest.objects.create(
        manor=manor,
        template=template,
        level=100,
        force=142,
        intellect=646,
        defense_stat=222,
        agility=248,
        luck=141,
        initial_force=42,
        initial_intellect=46,
        initial_defense=22,
        initial_agility=48,
        current_hp=int((800 + 222 * 50) * hp_ratio),
        hp_bonus=0,
    )
    if hp_ratio == 0:
        Guest.objects.filter(pk=guest.pk).update(current_hp=0)
        guest.refresh_from_db()
    before = guest.__dict__.copy()
    models = {m.__name__: m for m in (GuestTemplate, Guest, RecruitmentCandidate)}
    apps = SimpleNamespace(get_model=lambda app, name: models[name])
    migration = import_module("guests.migrations.0085_promote_lier")
    with transaction.atomic():
        migration.promote_lier(apps, SimpleNamespace(connection=connections["default"]))
    guest.refresh_from_db()
    template.refresh_from_db()
    assert template.rarity == "orange"
    assert template.archetype == "civil"
    assert template.growth_range == [8, 14]
    assert (guest.force, guest.intellect, guest.defense_stat, guest.agility) == (154, 745, 251, 270)
    assert (guest.initial_force, guest.initial_intellect, guest.initial_defense, guest.initial_agility) == (
        45,
        50,
        24,
        52,
    )
    assert guest.luck == 165
    assert guest.level == 100
    assert guest.current_hp == int((1000 + guest.defense_stat * 50) * hp_ratio)
    for field, value in before.items():
        if field in (
            "_state",
            "force",
            "intellect",
            "defense_stat",
            "agility",
            "luck",
            "initial_force",
            "initial_intellect",
            "initial_defense",
            "initial_agility",
            "current_hp",
        ):
            continue
        assert getattr(guest, field) == value


def test_lier_yaml_promoted_without_duplicate():
    data = yaml.safe_load(Path("data/guests/history_xianqin_01.yaml").read_text())
    matches = [
        (rarity, row) for rarity, rows in data["heroes"].items() for row in rows if row["key"] == "hist_xianqin_0007"
    ]
    assert len(matches) == 1
    rarity, row = matches[0]
    assert rarity == "orange"
    assert row["growth_range"] == [8, 14]
    assert row["archetype"] == "civil"
    assert row["attribute_weights"] == {"force": 6, "intellect": 64, "defense": 18, "agility": 12}
