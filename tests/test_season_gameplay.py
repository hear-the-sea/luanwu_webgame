from datetime import timedelta
from uuid import uuid4

import pytest
from django.contrib.staticfiles import finders
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from battle.models import BattleReport
from core.exceptions import GameError
from gameplay.models import InventoryItem, ItemTemplate, Manor, Season, SeasonAction, SeasonExpedition
from gameplay.selectors.season import get_season_context
from gameplay.services.season import actions
from gameplay.services.season.campaign_rules import load_campaign_rules
from gameplay.services.season.catalog import load_rules
from gameplay.services.season.expeditions import process_expedition
from guests.models import Guest, GuestStatus, GuestTemplate

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(user_factory):
    rules = load_rules()
    rules["campaign"] = load_campaign_rules()
    rules["campaign"]["expeditions"]["guest_daily_limit"] = 0  # 兼容旧快照，限次由专项验收。
    rules["campaign"]["simplification"]["enabled"] = False
    rules["campaign"].setdefault("resource_economy", {})["enabled"] = False
    # 旧规则快照兼容回归；新人口与差异活动在专项测试覆盖。
    for feature in ("population", "activities", "contribution"):
        rules["campaign"][feature]["enabled"] = False
    season = Season.objects.create(
        name="山川争衡",
        starts_at=timezone.now() - timedelta(days=16),
        ends_at=timezone.now() + timedelta(days=74),
        rules=rules,
    )
    template = GuestTemplate.objects.create(
        key="base_black_military", name="乡勇", archetype="military", rarity="black"
    )

    def player():
        user = user_factory()
        manor, _ = Manor.objects.get_or_create(user=user)
        guest = Guest.objects.create(
            manor=manor, template=template, force=100, defense_stat=100, intellect=40, agility=60, current_hp=100
        )
        return manor, guest

    return season, player


def join(season, manor, guest, token=None):
    return actions.perform_action(manor, season.pk, "join", str(token or uuid4()), {"preference": "fiction"})


def act(season, manor, kind, payload=None, token=None):
    return actions.perform_action(manor, season.pk, kind, str(token or uuid4()), payload or {})


def test_join_balances_and_retries_bind_to_payload(world):
    season, player = world
    a, ga = player()
    b, gb = player()
    token = uuid4()
    first = join(season, a, ga, token)
    assert join(season, a, ga, token) == first
    join(season, b, gb)
    assert set(season.members.values_list("faction", flat=True)) == {"fiction", "history"}
    assert season.members.count() == 2
    with pytest.raises(GameError, match="另一项操作"):
        act(season, a, "lineup", {"guest_ids": [ga.pk]}, token)


def test_cannot_use_foreign_or_duplicate_guests(world):
    season, player = world
    manor, guest = player()
    _, foreign = player()
    join(season, manor, guest)
    for ids in ([foreign.pk], [guest.pk, guest.pk]):
        with pytest.raises(GameError):
            act(season, manor, "dispatch", {"mode": "pve", "node_key": "mountain:inn", "guest_ids": ids})
    assert season.members.count() == 1
    assert not SeasonExpedition.objects.exists()


def test_real_pve_is_dispatched_and_settled_only_once(world):
    season, player = world
    manor, guest = player()
    join(season, manor, guest)
    token = uuid4()
    payload = {"mode": "pve", "node_key": "mountain:inn", "guest_ids": [guest.pk]}
    result = act(season, manor, "dispatch", payload, token)
    assert act(season, manor, "dispatch", payload, token) == result
    run = SeasonExpedition.objects.get(pk=result["run_id"])
    guest.refresh_from_db()
    assert guest.status == GuestStatus.DEPLOYED
    assert run.due_at > run.launched_at
    assert not BattleReport.objects.exists()
    assert process_expedition(run.pk, run.due_at)
    assert not process_expedition(run.pk, run.due_at - timedelta(seconds=1))
    member = season.members.get(manor=manor)
    run.refresh_from_db()
    assert run.status == "returning"
    assert process_expedition(run.pk, run.due_at)
    assert not process_expedition(run.pk, run.due_at + timedelta(seconds=1))
    guest.refresh_from_db()
    assert guest.status in {GuestStatus.IDLE, GuestStatus.INJURED}
    assert member.merit in {10, 20}
    assert BattleReport.objects.filter(battle_type="season").count() == 1
    assert member.actions.get(kind="logistics").result["report_url"]


def test_locked_node_and_retired_instant_challenge_do_not_dispatch(world):
    season, player = world
    season.rules["campaign"]["day_gates_enabled"] = True
    season.save(update_fields=["rules"])
    manor, guest = player()
    join(season, manor, guest)
    with pytest.raises(GameError):
        act(season, manor, "dispatch", {"node_key": "forest:granary", "mode": "pve", "guest_ids": [guest.pk]})
    with pytest.raises(GameError, match="真实派遣"):
        act(season, manor, "challenge", {"node_key": "mountain:gate"})
    member = season.members.get(manor=manor)
    assert not member.expeditions.exists()
    assert member.merit == 0
    assert not BattleReport.objects.exists()


def test_join_does_not_pin_any_guest_or_create_fixed_lineup(world):
    season, player = world
    a, ga = player()
    join(season, a, ga)
    member = season.members.get(manor=a)
    ga.refresh_from_db()
    assert set(member.lineup) <= {"registration"}
    assert "guest_ids" not in member.lineup
    assert ga.status == GuestStatus.IDLE
    assert not member.expeditions.exists()


def test_daily_limit_and_rollover(world):
    season, player = world
    manor, guest = player()
    join(season, manor, guest)
    member = season.members.get(manor=manor)
    for _ in range(season.rules["campaign"]["expeditions"]["daily_actions"]):
        SeasonAction.objects.create(
            member=member, token=uuid4(), request_hash="daily-limit", kind="dispatch", date=timezone.localdate()
        )
    payload = {"node_key": "mountain:inn", "mode": "pve", "guest_ids": [guest.pk]}
    with pytest.raises(GameError, match="次数"):
        act(season, manor, "dispatch", payload)
    member.actions.filter(kind="dispatch").update(date=timezone.localdate() - timedelta(days=1))
    act(season, manor, "dispatch", payload)
    assert member.expeditions.count() == 1


def test_busy_guest_cannot_be_dispatched_twice(world):
    season, player = world
    manor, guest = player()
    join(season, manor, guest)
    payload = {"mode": "pve", "node_key": "mountain:inn", "guest_ids": [guest.pk]}
    act(season, manor, "dispatch", payload)
    with pytest.raises(GameError, match="出征"):
        act(season, manor, "dispatch", payload)
    assert season.members.get(manor=manor).expeditions.count() == 1
    assert not BattleReport.objects.exists()


def test_rewards_exchange_are_bound_and_once(world):
    season, player = world
    manor, guest = player()
    join(season, manor, guest)
    member = season.members.get(manor=manor)
    member.merit = member.coins = 3500
    member.save()
    ItemTemplate.objects.create(key="book_iron_wall_heart", name="铁壁心法", effect_type="skill_book")
    token = uuid4()
    act(season, manor, "exchange", {"item_key": "fiction_heart"}, token)
    act(season, manor, "exchange", {"item_key": "fiction_heart"}, token)
    stock = InventoryItem.objects.get(manor=manor, template__key="book_iron_wall_heart")
    assert stock.quantity == stock.bound_quantity == 1
    assert season.members.get(pk=member.pk).coins == 3350
    act(season, manor, "claim", {"reward_key": "summon_scroll"})
    with pytest.raises(GameError, match="已经领取"):
        act(season, manor, "claim", {"reward_key": "summon_scroll"})
    assert season.members.get(pk=member.pk).scroll_available


def test_missing_reward_items_rollback_currency(world):
    season, player = world
    manor, guest = player()
    join(season, manor, guest)
    member = season.members.get(manor=manor)
    member.coins = 1000
    member.save()
    with pytest.raises(GameError, match="尚未载入"):
        act(season, manor, "exchange", {"item_key": "fiction_heart"})
    assert season.members.get(pk=member.pk).coins == 1000


def test_forged_manual_upgrade_is_rejected_without_spending_resources(world):
    season, player = world
    manor, guest = player()
    join(season, manor, guest)
    member = season.members.get(manor=manor)
    before = list(season.faction_states.order_by("pk").values("resources", "levels", "troops"))
    personal_before = (manor.grain, manor.silver)
    with pytest.raises(GameError, match="自动"):
        act(season, manor, "upgrade", {"upgrade_key": "logistics"})
    assert list(season.faction_states.order_by("pk").values("resources", "levels", "troops")) == before
    manor.refresh_from_db()
    assert (manor.grain, manor.silver) == personal_before
    assert not member.actions.filter(kind="upgrade").exists()


def test_empty_roster_cannot_consume_scroll_as_fallback(world):
    season, player = world
    manor, guest = player()
    join(season, manor, guest)
    member = season.members.get(manor=manor)
    member.scroll_available = True
    member.save()
    with pytest.raises(GameError, match="目录尚未载入"):
        act(season, manor, "summon_fallback")
    assert season.members.get(pk=member.pk).scroll_available


def test_summon_selects_only_valid_shortlist_and_consumes_once(world):
    from gameplay.services.season.catalog import faction_template_keys

    season, player = world
    manor, guest = player()
    join(season, manor, guest)
    member = season.members.get(manor=manor)
    member.scroll_available = True
    member.save()
    choices = [
        GuestTemplate.objects.create(key=key, name=f"候选{index}", rarity="purple", archetype="military")
        for index, key in enumerate(sorted(faction_template_keys("fiction"))[:4])
    ]
    ids = [template.pk for template in choices[:3]]
    with pytest.raises(GameError):
        act(season, manor, "summon", {"template_ids": [ids[0], ids[0], ids[1]]})
    token = uuid4()
    result = act(season, manor, "summon", {"template_ids": ids}, token)
    assert act(season, manor, "summon", {"template_ids": ids}, token) == result
    summoned = Guest.objects.get(pk=result["guest_id"])
    assert summoned.template_id in ids
    assert manor.guests.count() == 2
    member.refresh_from_db()
    assert member.scroll_used and not member.scroll_available


def test_summon_capacity_and_stale_choices_preserve_scroll(world):
    from gameplay.services.season.catalog import faction_template_keys

    season, player = world
    manor, guest = player()
    join(season, manor, guest)
    member = season.members.get(manor=manor)
    member.scroll_available = True
    member.save()
    choices = [
        GuestTemplate.objects.create(key=key, name=f"人物{index}", rarity="purple", archetype="military")
        for index, key in enumerate(sorted(faction_template_keys("fiction"))[:4])
    ]
    Guest.objects.create(manor=manor, template=choices[0])
    with pytest.raises(GameError, match="候选人物"):
        act(season, manor, "summon", {"template_ids": [t.pk for t in choices[:3]]})
    while manor.active_guest_count < manor.guest_capacity:
        Guest.objects.create(manor=manor, template=guest.template)
    with pytest.raises(GameError, match="容量不足"):
        act(season, manor, "summon", {"template_ids": [t.pk for t in choices[1:]]})
    assert season.members.get(pk=member.pk).scroll_available


def test_completed_collection_exchanges_fallback_materials(world):
    from gameplay.services.season.catalog import faction_template_keys

    season, player = world
    manor, guest = player()
    join(season, manor, guest)
    template = GuestTemplate.objects.create(
        key=next(iter(faction_template_keys("fiction"))), name="已有人物", rarity="purple", archetype="military"
    )
    Guest.objects.create(manor=manor, template=template)
    for key in season.rules["summon_fallback_items"]:
        ItemTemplate.objects.create(key=key, name=key, effect_type="tool")
    member = season.members.get(manor=manor)
    member.scroll_available = True
    member.save()
    act(season, manor, "summon_fallback")
    assert season.members.get(pk=member.pk).scroll_used
    assert set(InventoryItem.objects.filter(manor=manor).values_list("template__key", "bound_quantity")) >= {
        (key, quantity) for key, quantity in season.rules["summon_fallback_items"].items()
    }


def test_archive_get_is_read_only_and_navigation_is_next_to_tasks(world):
    season, player = world
    manor, guest = player()
    client = Client()
    client.force_login(manor.user)
    response = client.get(reverse("gameplay:season"))
    assert response.status_code == 200
    html = response.content.decode()
    assert html.index(">任务</a>") < html.index(">赛季玩法</a>") < html.index('id="nav-messages-link-top"')
    assert not season.members.exists()
    maps = get_season_context(manor)["season_maps"]
    assert len(maps) == 3
    assert all(
        region["background"].endswith(".webp") and all(node["image"].endswith(".webp") for node in region["nodes"])
        for region in maps
    )
    assert client.get(reverse("gameplay:season_action")).status_code == 405


def test_static_sprite_alias_is_readable_for_collectstatic():
    from config.static_finders import CatalogImageFinder

    finder = CatalogImageFinder()
    path = "season-assets/forest/granary.png"
    assert finders.find(path)
    entries = dict(finder.list([]))
    with entries[path].open(path) as handle:
        assert handle.read(8) == b"\x89PNG\r\n\x1a\n"


def test_post_workflow_and_csrf_enforcement(world):
    season, player = world
    manor, guest = player()
    protected = Client(enforce_csrf_checks=True)
    protected.force_login(manor.user)
    endpoint = reverse("gameplay:season_action")
    payload = {
        "season_id": season.pk,
        "action": "join",
        "action_token": str(uuid4()),
        "preference": "fiction",
    }
    assert protected.post(endpoint, payload).status_code == 403
    client = Client()
    client.force_login(manor.user)
    assert client.post(endpoint, payload).status_code == 302
    assert client.post(endpoint, payload).status_code == 302
    assert season.members.count() == 1
    response = client.post(
        endpoint,
        {
            "season_id": season.pk,
            "action": "dispatch",
            "action_token": str(uuid4()),
            "node_key": "mountain:inn",
            "mode": "pve",
            "guest_ids": [guest.pk],
        },
        follow=True,
    )
    assert response.status_code == 200
    run = season.members.get(manor=manor).expeditions.get()
    assert run.status == "outbound"
    assert process_expedition(run.pk, run.due_at)
    run.refresh_from_db()
    assert process_expedition(run.pk, run.due_at)
    response = client.get(reverse("gameplay:season"))
    text = response.content.decode()
    assert f'href="{reverse("gameplay:messages")}"' in text
    assert "邮件战报" in text


def test_recall_is_owned_and_keeps_other_players_expeditions_private(world):
    season, player = world
    manor, guest = player()
    join(season, manor, guest)
    other, other_guest = player()
    join(season, other, other_guest)
    result = act(season, manor, "dispatch", {"node_key": "mountain:inn", "mode": "pve", "guest_ids": [guest.pk]})
    with pytest.raises(GameError, match="不存在"):
        act(season, other, "recall", {"run_id": result["run_id"]})
    run = SeasonExpedition.objects.get(pk=result["run_id"])
    assert run.status == "outbound"
    act(season, manor, "recall", {"run_id": run.pk})
    run.refresh_from_db()
    assert run.status == "returning"
    assert not BattleReport.objects.exists()


def test_each_rendered_form_has_independent_stable_token_without_javascript(world):
    import re
    from uuid import UUID

    from gameplay.services.season.catalog import reward_catalog

    season, player = world
    manor, guest = player()
    join(season, manor, guest)
    member = season.members.get(manor=manor)
    member.merit = 3500
    member.save(update_fields=["merit"])
    for reward in reward_catalog(member.faction, rules=season.rules):
        for key in reward.get("items", {}):
            ItemTemplate.objects.get_or_create(key=key, defaults={"name": key, "effect_type": "tool"})
    client = Client()
    client.force_login(manor.user)
    html = client.get(reverse("gameplay:season")).content.decode()
    tokens = re.findall(r'name="action_token" value="([^"]+)"', html)
    assert len(tokens) > 5
    assert len(tokens) == len(set(tokens))
    assert all(UUID(token).version == 4 for token in tokens)
    assert 'name="action" value="upgrade"' not in html
    submitted = set()
    for form in re.findall(r"<form\b[\s\S]*?</form>", html):
        match = re.search(r'name="reward_key" value="([^"]+)"', form)
        if not match or match[1] in submitted:
            continue
        token = re.search(r'name="action_token" value="([^"]+)"', form)[1]
        result = act(season, manor, "claim", {"reward_key": match[1]}, token)
        assert act(season, manor, "claim", {"reward_key": match[1]}, token) == result
        submitted.add(match[1])
        if len(submitted) == 2:
            break
    assert len(submitted) == 2
    assert season.members.get(manor=manor).actions.filter(kind="claim").count() == 2


def test_configured_summon_shortlist_is_identical_in_selector_and_command(world):
    from gameplay.services.season.catalog import faction_template_keys

    season, player = world
    season.rules["summon_pick_count"] = 2
    season.save(update_fields=["rules"])
    manor, guest = player()
    join(season, manor, guest)
    member = season.members.get(manor=manor)
    member.scroll_available = True
    member.save(update_fields=["scroll_available"])
    choices = [
        GuestTemplate.objects.create(key=key, name=f"候选{index}", rarity="purple", archetype="military")
        for index, key in enumerate(sorted(faction_template_keys("fiction"))[:4])
    ]
    assert get_season_context(manor, season.pk)["summon_pick_count"] == 2
    with pytest.raises(GameError, match="2 名"):
        act(season, manor, "summon", {"template_ids": [row.pk for row in choices[:3]]})
    result = act(season, manor, "summon", {"template_ids": [row.pk for row in choices[:2]]})
    assert Guest.objects.get(pk=result["guest_id"]).template_id in [row.pk for row in choices[:2]]


def test_protected_enemy_site_removes_attack_modes_and_shows_deadline(world):
    from gameplay.models import SeasonSite

    season, player = world
    manor, guest = player()
    join(season, manor, guest)
    SeasonSite.objects.create(
        season=season,
        key="mountain:gate",
        owner="history",
        guards=0,
        durability=0,
        protected_until=timezone.now() + timedelta(minutes=30),
        last_score_at=timezone.now(),
    )
    context = get_season_context(manor, season.pk)
    node = next(node for region in context["season_maps"] for node in region["nodes"] if node["key"] == "mountain:gate")
    assert node["site"]["protected_until"]
    assert not node["modes"]
    client = Client()
    client.force_login(manor.user)
    assert "占领休整保护至" in client.get(reverse("gameplay:season")).content.decode()


def test_join_dialog_exposes_faction_preference(world, django_user_model, client):
    season, _ = world
    user = django_user_model.objects.create_user(username="season_preference_viewer", password="pass123")
    client.force_login(user)
    html = client.get(reverse("gameplay:season")).content.decode()
    assert 'name="preference"' in html
    assert 'value="fiction"' in html
    assert 'value="history"' in html
    assert "阵营意向" in html and "不影响已有门客出战" in html
