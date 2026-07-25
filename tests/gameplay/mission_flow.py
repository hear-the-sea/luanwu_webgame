from datetime import timedelta

import pytest
from django.utils import timezone

from core.exceptions import (
    ActionPointsInsufficientError,
    InsufficientStockError,
    MissionCannotRetreatError,
    MissionSquadSizeExceededError,
    MissionTroopLoadoutError,
)
from gameplay.models import InventoryItem, ItemTemplate, MissionRun, MissionTemplate
from gameplay.services.inventory.core import add_item_to_inventory
from gameplay.services.manor.core import ensure_manor
from gameplay.services.missions import launch_mission, refresh_mission_runs, request_retreat
from guests.models import GuestStatus
from tests.gameplay.support import recruit_frontline_guests


@pytest.mark.django_db(transaction=True)
def test_mission_launch_and_return(game_data, mission_templates, manor_with_troops):
    mission = MissionTemplate.objects.create(
        key="mission_launch_return_case",
        name="出征返程测试任务",
        guest_only=False,
        is_defense=False,
        enemy_troops={"dao_ke": 1},
        drop_table={"silver": 1},
    )
    manor = manor_with_troops
    manor.silver = max(int(manor.silver or 0), 5_000)
    manor.save(update_fields=["silver"])

    recruit_frontline_guests(manor, count=3)
    guests = list(manor.guests.all()[:3])

    from battle.models import TroopTemplate
    from gameplay.models import PlayerTroop

    archer_template = TroopTemplate.objects.get(key="archer")
    troop_before = PlayerTroop.objects.get(manor=manor, troop_template=archer_template)
    initial_count = troop_before.count
    assert initial_count >= 100

    run = launch_mission(manor, mission, [g.id for g in guests], {"archer": 100})
    assert run.battle_report is not None
    assert run.mission == mission
    assert run.guests.count() == len(guests)
    for guest in guests:
        guest.refresh_from_db()
        assert guest.status == GuestStatus.DEPLOYED

    troop_before.refresh_from_db()
    assert troop_before.count == initial_count - 100

    run.return_at = timezone.now() - timedelta(seconds=1)
    run.save(update_fields=["return_at"])
    refresh_mission_runs(manor)

    for guest in guests:
        guest.refresh_from_db()
        assert guest.status in [GuestStatus.IDLE, GuestStatus.INJURED]

    troop_before.refresh_from_db()
    assert troop_before.count >= initial_count - 100


@pytest.mark.django_db(transaction=True)
def test_mission_launch_with_invalid_troop_type(game_data, mission_templates, manor_with_troops):
    from gameplay.models import PlayerTroop
    from guests.models import Guest, GuestTemplate

    mission = MissionTemplate.objects.filter(guest_only=False, is_defense=False).first()
    if not mission:
        pytest.skip("No mission available that allows troops")
    manor = manor_with_troops

    template = GuestTemplate.objects.first()
    if template:
        guest = Guest.objects.create(manor=manor, template=template, level=50, status=GuestStatus.IDLE)
    else:
        pytest.skip("No guest template available")

    fake_troop_key = "nonexistent_troop_xxx"
    assert not PlayerTroop.objects.filter(manor=manor, troop_template__key=fake_troop_key).exists()

    with pytest.raises(MissionTroopLoadoutError) as exc:
        launch_mission(manor, mission, [guest.id], {fake_troop_key: 100})

    assert str(exc.value) == "护院配置包含无效类型"
    assert not PlayerTroop.objects.filter(manor=manor, troop_template__key=fake_troop_key).exists()


@pytest.mark.django_db(transaction=True)
def test_mission_launch_with_insufficient_troops_wraps_shared_loadout_error(
    game_data, mission_templates, manor_with_troops
):
    from battle.models import TroopTemplate
    from gameplay.models import PlayerTroop
    from guests.models import Guest, GuestTemplate

    mission = MissionTemplate.objects.filter(guest_only=False, is_defense=False).first()
    if not mission:
        pytest.skip("No mission available that allows troops")
    manor = manor_with_troops
    template = GuestTemplate.objects.first()
    if template is None:
        pytest.skip("No guest template available")

    guest = Guest.objects.create(manor=manor, template=template, level=50, status=GuestStatus.IDLE)
    troop_template = TroopTemplate.objects.create(
        key="mission_insufficient_guard",
        name="任务测试护院",
        description="",
        base_attack=1,
        base_defense=1,
        base_hp=1,
        speed_bonus=0,
        priority=9999,
        default_count=0,
    )
    PlayerTroop.objects.create(manor=manor, troop_template=troop_template, count=1)

    with pytest.raises(MissionTroopLoadoutError) as exc:
        launch_mission(manor, mission, [guest.id], {troop_template.key: 2})

    assert "数量不足" in str(exc.value)


@pytest.mark.django_db(transaction=True)
def test_mission_launch_rejects_when_guest_count_exceeds_max_squad(game_data, mission_templates, manor_with_troops):
    from guests.models import Guest, GuestTemplate

    mission = MissionTemplate.objects.filter(is_defense=False).order_by("-guest_only", "id").first()
    if not mission:
        pytest.skip("No offense mission available")
    manor = manor_with_troops
    template = GuestTemplate.objects.first()
    if template is None:
        pytest.skip("No guest template available")

    max_squad_size = getattr(manor, "max_squad_size", 0)
    if max_squad_size <= 0:
        pytest.skip("Invalid manor max_squad_size")

    guests = [
        Guest.objects.create(
            manor=manor,
            template=template,
            level=10,
            status=GuestStatus.IDLE,
            custom_name=f"mission_limit_guest_{idx}",
        )
        for idx in range(max_squad_size + 1)
    ]

    with pytest.raises(MissionSquadSizeExceededError) as exc:
        launch_mission(manor, mission, [guest.id for guest in guests], {})
    assert f"最多只能派出 {max_squad_size} 名门客出征" in str(exc.value)

    for guest in guests:
        guest.refresh_from_db()
        assert guest.status == GuestStatus.IDLE


@pytest.mark.django_db(transaction=True)
def test_mission_launch_consumes_entry_cost_item(game_data, django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="player_mission_entry_cost", password="pass12345")
    manor = ensure_manor(user)
    item_template = ItemTemplate.objects.create(
        key="mission_entry_token",
        name="任务入场信物",
        effect_type="resource",
        rarity="blue",
        tradeable=False,
        price=0,
        storage_space=1,
        is_usable=False,
    )
    add_item_to_inventory(manor, item_template.key, 2)
    mission = MissionTemplate.objects.create(
        key="mission_entry_cost_case",
        name="入场消耗任务",
        guest_only=True,
        entry_cost={item_template.key: 1},
    )
    recruit_frontline_guests(manor, count=1)
    guest = manor.guests.first()

    monkeypatch.setattr(
        "gameplay.services.missions_impl.mission_followups.try_prepare_launch_report",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "gameplay.services.missions_impl.mission_followups.dispatch_complete_mission_task",
        lambda *args, **kwargs: None,
    )

    launch_mission(manor, mission, [guest.id], {})

    inventory_item = InventoryItem.objects.get(
        manor=manor,
        template=item_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    assert inventory_item.quantity == 1


@pytest.mark.django_db(transaction=True)
def test_mission_launch_consumes_action_points(game_data, django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="player_mission_action_points", password="pass12345")
    manor = ensure_manor(user)
    mission = MissionTemplate.objects.create(
        key="mission_action_points_case",
        name="行动力任务",
        guest_only=True,
    )
    recruit_frontline_guests(manor, count=1)
    guest = manor.guests.first()

    monkeypatch.setattr(
        "gameplay.services.missions_impl.mission_followups.try_prepare_launch_report",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "gameplay.services.missions_impl.mission_followups.dispatch_complete_mission_task",
        lambda *args, **kwargs: None,
    )

    launch_mission(manor, mission, [guest.id], {})

    manor.refresh_from_db()
    assert manor.action_points == 990


@pytest.mark.django_db(transaction=True)
def test_defense_mission_launch_consumes_action_points(game_data, django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="player_defense_mission_action_points", password="pass12345")
    manor = ensure_manor(user)
    mission = MissionTemplate.objects.create(
        key="defense_mission_action_points_case",
        name="行动力防守任务",
        is_defense=True,
        base_travel_time=60,
    )

    monkeypatch.setattr(
        "gameplay.services.missions_impl.mission_followups.try_prepare_launch_report",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "gameplay.services.missions_impl.mission_followups.dispatch_complete_mission_task",
        lambda *args, **kwargs: None,
    )

    launch_mission(manor, mission, [], {})

    manor.refresh_from_db()
    assert manor.action_points == 990


@pytest.mark.django_db(transaction=True)
def test_mission_launch_rejects_when_action_points_insufficient(game_data, django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="player_mission_no_action_points", password="pass12345")
    manor = ensure_manor(user)
    manor.action_points = 9
    manor.save(update_fields=["action_points"])
    mission = MissionTemplate.objects.create(
        key="mission_no_action_points_case",
        name="行动力不足任务",
        guest_only=True,
    )
    recruit_frontline_guests(manor, count=1)
    guest = manor.guests.first()

    monkeypatch.setattr(
        "gameplay.services.missions_impl.mission_followups.try_prepare_launch_report",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "gameplay.services.missions_impl.mission_followups.dispatch_complete_mission_task",
        lambda *args, **kwargs: None,
    )

    with pytest.raises(ActionPointsInsufficientError, match="行动力不足"):
        launch_mission(manor, mission, [guest.id], {})

    manor.refresh_from_db()
    assert manor.action_points == 9
    assert MissionRun.objects.filter(mission=mission).exists() is False


@pytest.mark.django_db(transaction=True)
def test_mission_launch_rejects_missing_entry_cost_item(game_data, django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="player_mission_missing_entry_cost", password="pass12345")
    manor = ensure_manor(user)
    item_template = ItemTemplate.objects.create(
        key="mission_missing_entry_token",
        name="缺失任务信物",
        effect_type="resource",
        rarity="blue",
        tradeable=False,
        price=0,
        storage_space=1,
        is_usable=False,
    )
    mission = MissionTemplate.objects.create(
        key="mission_missing_entry_cost_case",
        name="缺少入场消耗任务",
        guest_only=True,
        entry_cost={item_template.key: 1},
    )
    recruit_frontline_guests(manor, count=1)
    guest = manor.guests.first()

    monkeypatch.setattr(
        "gameplay.services.missions_impl.mission_followups.try_prepare_launch_report",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "gameplay.services.missions_impl.mission_followups.dispatch_complete_mission_task",
        lambda *args, **kwargs: None,
    )

    with pytest.raises(InsufficientStockError, match="缺失任务信物数量不足"):
        launch_mission(manor, mission, [guest.id], {})

    guest.refresh_from_db()
    assert guest.status == GuestStatus.IDLE
    assert MissionRun.objects.filter(mission=mission).exists() is False


@pytest.mark.django_db(transaction=True)
def test_request_retreat_raises_mission_cannot_retreat_when_already_retreating(django_user_model, monkeypatch):
    import gameplay.services.missions_impl.execution as mission_execution

    user = django_user_model.objects.create_user(username="player_mission_retreating", password="pass12345")
    manor = ensure_manor(user)
    mission = MissionTemplate.objects.create(key="mission_retreating_case", name="撤退测试任务", is_defense=False)
    run = MissionRun.objects.create(manor=manor, mission=mission, status=MissionRun.Status.ACTIVE, travel_time=300)

    monkeypatch.setattr(mission_execution, "schedule_mission_completion", lambda *_args, **_kwargs: None)
    request_retreat(run)

    with pytest.raises(MissionCannotRetreatError, match="任务已在撤退中"):
        request_retreat(run)


@pytest.mark.django_db(transaction=True)
def test_request_retreat_rejects_future_started_at_contract(django_user_model, monkeypatch):
    import gameplay.services.missions_impl.execution as mission_execution

    user = django_user_model.objects.create_user(username="player_mission_retreat_future", password="pass12345")
    manor = ensure_manor(user)
    mission = MissionTemplate.objects.create(key="mission_retreat_future_case", name="未来撤退测试", is_defense=False)
    run = MissionRun.objects.create(manor=manor, mission=mission, status=MissionRun.Status.ACTIVE, travel_time=300)
    future_started_at = timezone.now() + timedelta(seconds=10)
    MissionRun.objects.filter(pk=run.pk).update(started_at=future_started_at)
    run.refresh_from_db()

    monkeypatch.setattr(mission_execution, "schedule_mission_completion", lambda *_args, **_kwargs: None)

    with pytest.raises(AssertionError, match="started_at cannot be in the future"):
        request_retreat(run)


@pytest.mark.django_db(transaction=True)
def test_request_retreat_immediate_retreat_preserves_minimum_return_time(django_user_model, monkeypatch):
    import gameplay.services.missions_impl.execution as mission_execution

    user = django_user_model.objects.create_user(username="player_mission_retreat_immediate", password="pass12345")
    manor = ensure_manor(user)
    mission = MissionTemplate.objects.create(
        key="mission_retreat_immediate_case", name="立即撤退测试", is_defense=False
    )
    run = MissionRun.objects.create(manor=manor, mission=mission, status=MissionRun.Status.ACTIVE, travel_time=300)
    started_at = timezone.now()
    MissionRun.objects.filter(pk=run.pk).update(started_at=started_at)
    run.refresh_from_db()

    monkeypatch.setattr(mission_execution, "schedule_mission_completion", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("gameplay.services.missions_impl.retreat_command.timezone.now", lambda: started_at)

    request_retreat(run)

    run.refresh_from_db()
    assert run.is_retreating is True
    assert run.return_at == started_at + timedelta(seconds=1)
