from io import StringIO

import pytest
from django.core.management import call_command

from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestTemplate


@pytest.mark.django_db
def test_sync_xiaozhao_is_scoped_and_preserves_owned_progress(django_user_model):
    manor = ensure_manor(django_user_model.objects.create_user(username="xiaozhao_sync", password="pass"))
    template = GuestTemplate.objects.create(
        key="hero_jinyong_0215",
        name="小昭",
        rarity="blue",
        archetype="civil",
        base_attack=36,
        base_intellect=40,
        base_defense=19,
        base_agility=42,
        base_luck=120,
        base_hp=600,
    )
    guest = Guest.objects.create(
        manor=manor,
        template=template,
        level=20,
        force=90,
        intellect=200,
        defense_stat=100,
        agility=170,
        luck=120,
        current_hp=1000,
    )
    unrelated = GuestTemplate.objects.create(key="unrelated_sync", name="其它门客", base_attack=999)
    converted = GuestTemplate.objects.create(
        key=f"guest_rarity_conversion_purple_{template.pk}", name="小昭", rarity="purple", recruitable=False
    )
    original_stats = (guest.level, guest.force, guest.intellect, guest.defense_stat, guest.agility, guest.luck)
    previous_max = guest.max_hp
    call_command("sync_xiaozhao_template", skip_images=True, stdout=StringIO())
    template.refresh_from_db()
    guest.refresh_from_db()
    unrelated.refresh_from_db()
    assert template.pk == guest.template_id
    assert template.rarity == "purple"
    assert template.archetype == "civil"
    assert template.growth_range == [6, 12]
    assert template.attribute_weights == {"force": 10, "intellect": 45, "defense": 10, "agility": 35}
    assert (template.base_attack, template.base_intellect, template.base_defense, template.base_agility) == (
        42,
        46,
        22,
        48,
    )
    assert (guest.level, guest.force, guest.intellect, guest.defense_stat, guest.agility, guest.luck) == original_stats
    assert guest.current_hp == int(guest.max_hp * 1000 / previous_max)
    assert unrelated.base_attack == 999
    assert GuestTemplate.objects.filter(pk=converted.pk).exists()
    first_hp = guest.current_hp
    call_command("sync_xiaozhao_template", skip_images=True, stdout=StringIO())
    guest.refresh_from_db()
    assert guest.current_hp == first_hp
    assert GuestTemplate.objects.filter(key="hero_jinyong_0215").count() == 1


@pytest.mark.django_db
def test_sync_xiaozhao_dry_run_does_not_create_template():
    call_command("sync_xiaozhao_template", skip_images=True, dry_run=True, stdout=StringIO())
    assert not GuestTemplate.objects.filter(key="hero_jinyong_0215").exists()


@pytest.mark.django_db
def test_sync_xiaozhao_creates_missing_template_without_full_import():
    existing_ids = set(GuestTemplate.objects.values_list("pk", flat=True))
    call_command("sync_xiaozhao_template", skip_images=True, stdout=StringIO())
    template = GuestTemplate.objects.get(key="hero_jinyong_0215")
    assert template.rarity == "purple"
    assert template.base_hp == 800
    assert set(GuestTemplate.objects.values_list("pk", flat=True)) == existing_ids | {template.pk}
