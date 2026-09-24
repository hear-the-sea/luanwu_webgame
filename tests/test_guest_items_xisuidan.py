import pytest

from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.inventory.guest_items import use_xisuidan
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestArchetype, GuestRarity, GuestTemplate


@pytest.mark.django_db
def test_use_xisuidan_rerolls_growth_from_current_stats(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="xisuidan_player", password="pass123")
    manor = ensure_manor(user)

    template = GuestTemplate.objects.create(
        key="xisuidan_guest_tpl",
        name="洗髓测试门客",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.BLUE,
        base_attack=100,
        base_intellect=90,
        base_defense=80,
        base_agility=70,
        base_luck=60,
        growth_range=[12, 13],
    )
    guest = Guest.objects.create(
        manor=manor,
        template=template,
        level=10,
        force=130,
        intellect=120,
        defense_stat=110,
        agility=100,
        initial_force=100,
        initial_intellect=90,
        initial_defense=80,
        initial_agility=70,
        allocated_force=5,
        allocated_intellect=2,
        allocated_defense=3,
        allocated_agility=1,
    )

    item_template = ItemTemplate.objects.create(
        key="xisuidan_item_tpl",
        name="洗髓丹",
        effect_type=ItemTemplate.EffectType.TOOL,
        effect_payload={"action": "reroll_growth"},
    )
    item = InventoryItem.objects.create(manor=manor, template=item_template, quantity=1)

    def fake_allocate_level_up_attributes(_guest, levels=1, rng=None):
        assert _guest.pk == guest.pk
        assert levels == 9
        return {"force": 20, "intellect": 30, "defense": 25, "agility": 35}

    monkeypatch.setattr("guests.utils.attribute_growth.allocate_level_up_attributes", fake_allocate_level_up_attributes)

    result = use_xisuidan(manor, item, guest.id)

    guest.refresh_from_db()
    assert guest.force == 125
    assert guest.intellect == 122
    assert guest.defense_stat == 108
    assert guest.agility == 106
    assert guest.xisuidan_used == 1
    assert not InventoryItem.objects.filter(pk=item.pk).exists()

    assert result["old_total"] == 109
    assert result["new_total"] == 110
    assert result["growth_diff"] == 1
    assert result["changes"] == {"force": -5, "intellect": 2, "defense": -2, "agility": 6}
