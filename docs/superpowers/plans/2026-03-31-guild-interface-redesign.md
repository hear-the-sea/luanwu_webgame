# Guild Interface Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the guild resources and guild missions interfaces so guild resources show silver, grain, gold bars, red rubies, and troop details, the donation area supports silver/grain/gold-bar/troop donations, and guild missions use the same table-plus-detail interaction model as the personal tasks page.

**Architecture:** Keep the existing guild service layer and routes, and rebuild the UI by extending current guild context builders instead of introducing a second parallel flow. Gold-bar donation is added to the existing guild contribution transaction path; troop donation keeps its current service and route but moves its entry point to the resource dashboard. Guild missions stay on the existing launch/retreat services, but the page context and template are reshaped to match the personal tasks experience with difficulty tabs and a query-driven detail modal.

**Tech Stack:** Django templates/views/services, pytest, existing guild mission service layer, existing `static/js/tasks-page.js`, guild YAML runtime config

---

## File Structure

- Modify: `/home/daniel/code/web_game_v5/data/guild_rules.yaml`
  Responsibility: define runtime donation rates and daily limits for `gold_bar`.
- Modify: `/home/daniel/code/web_game_v5/guilds/constants.py`
  Responsibility: expose `gold_bar` contribution defaults and keep normalized config behavior in sync with YAML.
- Modify: `/home/daniel/code/web_game_v5/guilds/services/contribution.py`
  Responsibility: extend `donate_resource()` to support `gold_bar` inventory donation with the same transactional guarantees as silver and grain.
- Modify: `/home/daniel/code/web_game_v5/guilds/views/contribution.py`
  Responsibility: build resource dashboard context with red-ruby totals, troop summaries, player gold-bar count, and donation panel data for both `resources` and `donate` routes.
- Modify: `/home/daniel/code/web_game_v5/guilds/views/missions.py`
  Responsibility: keep the troop donation endpoint but redirect users back to the guild resources page after donation.
- Modify: `/home/daniel/code/web_game_v5/guilds/services/guild_missions.py`
  Responsibility: build guild mission page data grouped by difficulty and expose a selected-mission detail context.
- Modify: `/home/daniel/code/web_game_v5/guilds/templates/guilds/resources.html`
  Responsibility: render the new resource dashboard with four resource cards, troop overview/details, and unified donation panels.
- Modify: `/home/daniel/code/web_game_v5/guilds/templates/guilds/donate.html`
  Responsibility: keep the route alive by rendering the same unified donation experience instead of the old two-card donation page.
- Modify: `/home/daniel/code/web_game_v5/guilds/templates/guilds/missions.html`
  Responsibility: replace the old inline launch list with task-style tabs, tables, a current-run status card, and a selected mission detail modal.
- Create: `/home/daniel/code/web_game_v5/guilds/templates/guilds/partials/mission_detail_modal.html`
  Responsibility: hold the guild mission detail panel so `missions.html` stays readable.
- Delete: `/home/daniel/code/web_game_v5/guilds/templates/guilds/partials/mission_launch_form.html`
  Responsibility: remove the obsolete inline launch form once the modal replaces it.
- Modify: `/home/daniel/code/web_game_v5/static/js/tasks-page.js`
  Responsibility: make the existing task-tab/guest-count/troop-slider behavior work for the guild mission page without breaking the personal tasks page.
- Modify: `/home/daniel/code/web_game_v5/tests/guilds/contribution_upgrade.py`
  Responsibility: cover guild contribution service behavior for `gold_bar`.
- Modify: `/home/daniel/code/web_game_v5/tests/test_guild_rules_loader.py`
  Responsibility: lock in runtime config support for `gold_bar` contribution defaults.
- Create: `/home/daniel/code/web_game_v5/tests/test_guild_resource_views.py`
  Responsibility: cover resource dashboard rendering for red rubies, troop summaries, and unified donation forms.
- Modify: `/home/daniel/code/web_game_v5/tests/test_guild_troop_donation.py`
  Responsibility: preserve troop donation behavior while updating the redirect target to the resource page.
- Modify: `/home/daniel/code/web_game_v5/tests/test_guild_mission_views.py`
  Responsibility: verify the guild mission page adopts the personal-task layout, removes the troop-pool block, and renders the new detail modal.

Note: this plan intentionally omits `git commit` steps because the workspace instructions explicitly say not to plan or execute commits unless the user asks.

### Task 1: Extend Guild Contribution Config and Service for Gold Bars

**Files:**
- Modify: `/home/daniel/code/web_game_v5/data/guild_rules.yaml`
- Modify: `/home/daniel/code/web_game_v5/guilds/constants.py`
- Modify: `/home/daniel/code/web_game_v5/guilds/services/contribution.py`
- Modify: `/home/daniel/code/web_game_v5/tests/guilds/contribution_upgrade.py`
- Modify: `/home/daniel/code/web_game_v5/tests/test_guild_rules_loader.py`

- [ ] **Step 1: Write the failing config and service tests**

Add `gold_bar` coverage to the existing tests.

In `/home/daniel/code/web_game_v5/tests/guilds/contribution_upgrade.py`, add service tests shaped like:

```python
def test_donate_gold_bar_uses_inventory_items(user_with_gold_bars, gold_bar_template):
    guild = guild_service.create_guild(user=user_with_gold_bars, name="金条捐赠帮会", description="")
    member = GuildMember.objects.get(user=user_with_gold_bars, guild=guild)
    manor = Manor.objects.get(user=user_with_gold_bars)
    gold_item = InventoryItem.objects.get(
        manor=manor,
        template=gold_bar_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    contribution_service.donate_resource(member, "gold_bar", 3)

    member.refresh_from_db()
    guild.refresh_from_db()
    gold_item.refresh_from_db()
    assert guild.gold_bar == 3
    assert gold_item.quantity == 5
    assert member.total_contribution == 3 * contribution_service.CONTRIBUTION_RATES["gold_bar"]


def test_donate_gold_bar_rejects_insufficient_inventory(user_with_gold_bars):
    guild = guild_service.create_guild(user=user_with_gold_bars, name="金条不足帮会", description="")
    member = GuildMember.objects.get(user=user_with_gold_bars, guild=guild)

    with pytest.raises(GuildContributionError, match="金条不足"):
        contribution_service.donate_resource(member, "gold_bar", 99)
```

In `/home/daniel/code/web_game_v5/tests/test_guild_rules_loader.py`, extend the normalization assertions:

```python
assert loaded["contribution"]["rates"]["gold_bar"] == 50
assert loaded["contribution"]["daily_limits"]["gold_bar"] == 20
```

and update the custom input payload so it includes:

```python
"contribution": {
    "rates": {"silver": "2", "gold_bar": "50"},
    "daily_limits": {"grain": "60000", "gold_bar": "20"},
    "min_donation_amount": "1",
},
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
pytest "tests/guilds/contribution_upgrade.py" "tests/test_guild_rules_loader.py" -q
```

Expected before implementation:

- the new `gold_bar` service test fails with `GuildContributionError: 不支持捐赠gold_bar`
- the rules-loader test fails because `gold_bar` is missing from `rates` / `daily_limits`

- [ ] **Step 3: Implement the minimal config and transactional service support**

Update `/home/daniel/code/web_game_v5/data/guild_rules.yaml` and `/home/daniel/code/web_game_v5/guilds/constants.py` so contribution defaults include `gold_bar`:

```yaml
contribution:
  rates:
    silver: 1
    grain: 2
    gold_bar: 50
  daily_limits:
    silver: 100000
    grain: 50000
    gold_bar: 20
  min_donation_amount: 1
```

In `/home/daniel/code/web_game_v5/guilds/services/contribution.py`, split the donation flow into a resource branch and an inventory-item branch:

```python
from gameplay.models import InventoryItem, ItemTemplate, Manor, ResourceEvent


def _lock_gold_bar_item(*, manor: Manor) -> InventoryItem | None:
    try:
        gold_bar_template = ItemTemplate.objects.get(key="gold_bar")
    except ItemTemplate.DoesNotExist as exc:
        raise GuildContributionError("金条物品不存在，请联系管理员") from exc
    return (
        InventoryItem.objects.select_for_update()
        .filter(
            manor=manor,
            template=gold_bar_template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
        .first()
    )
```

and then inside `donate_resource()` use:

```python
if resource_type == "gold_bar":
    gold_bar_item = _lock_gold_bar_item(manor=manor)
    if not gold_bar_item or gold_bar_item.quantity < amount:
        raise GuildContributionError(f"金条不足，需要{amount}金条")
    updated = InventoryItem.objects.filter(pk=gold_bar_item.pk, quantity__gte=amount).update(
        quantity=F("quantity") - amount
    )
    if updated != 1:
        raise GuildContributionError(f"金条不足，需要{amount}金条")
    InventoryItem.objects.filter(pk=gold_bar_item.pk, quantity=0).delete()
else:
    spend_resources_locked(
        manor,
        {resource_type: amount},
        note="帮会捐献",
        reason=ResourceEvent.Reason.GUILD_DONATION,
    )
```

Keep the guild/member/logging updates shared so `GuildDonationLog` and `GuildResourceLog` are still created through one code path.

- [ ] **Step 4: Re-run the focused tests and verify they pass**

Run:

```bash
pytest "tests/guilds/contribution_upgrade.py" "tests/test_guild_rules_loader.py" -q
```

Expected:

- PASS

### Task 2: Build the Unified Guild Resource Dashboard Context and Templates

**Files:**
- Modify: `/home/daniel/code/web_game_v5/guilds/views/contribution.py`
- Modify: `/home/daniel/code/web_game_v5/guilds/templates/guilds/resources.html`
- Modify: `/home/daniel/code/web_game_v5/guilds/templates/guilds/donate.html`
- Create: `/home/daniel/code/web_game_v5/tests/test_guild_resource_views.py`

- [ ] **Step 1: Write failing view tests for the new resource dashboard**

Create `/home/daniel/code/web_game_v5/tests/test_guild_resource_views.py` with tests like:

```python
@pytest.mark.django_db
def test_resource_page_shows_red_ruby_and_troop_details(django_user_model):
    user = django_user_model.objects.create_user(username="guild_resource_view", password="pass12345")
    manor = ensure_manor(user)
    guild = Guild.objects.create(name="资源界面帮会", founder=user, is_active=True, silver=1200, grain=3400, gold_bar=5)
    GuildMember.objects.create(guild=guild, user=user, position="leader", is_active=True)
    GuildWarehouse.objects.create(guild=guild, item_key="red_ruby", quantity=18, contribution_cost=0)
    troop_template = TroopTemplate.objects.create(key="view_archer", name="界面弓兵")
    GuildTroopStorage.objects.create(guild=guild, troop_template=troop_template, count=9)

    client = Client()
    assert client.login(username="guild_resource_view", password="pass12345")
    response = client.get(reverse("guilds:resources"))

    body = response.content.decode("utf-8")
    assert "红宝石" in body
    assert "18" in body
    assert "界面弓兵" in body
    assert "库存 9" in body
    assert "捐赠金条" in body
    assert "捐赠护院" in body
```

Add a second test that keeps the `guilds:donate` route alive:

```python
@pytest.mark.django_db
def test_donate_page_renders_unified_dashboard(django_user_model, gold_bar_template):
    user = django_user_model.objects.create_user(username="guild_donate_dashboard", password="pass12345")
    manor = ensure_manor(user)
    InventoryItem.objects.create(
        manor=manor,
        template=gold_bar_template,
        quantity=7,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    guild = Guild.objects.create(name="捐赠整合帮会", founder=user, is_active=True)
    GuildMember.objects.create(guild=guild, user=user, position="leader", is_active=True)
    response = client.get(reverse("guilds:donate"))
    body = response.content.decode("utf-8")
    assert "资源总览" in body
    assert "捐赠银两" in body
    assert "捐赠粮食" in body
    assert "捐赠金条" in body
    assert "捐赠护院" in body
```

- [ ] **Step 2: Run the new resource view tests and verify they fail**

Run:

```bash
pytest "tests/test_guild_resource_views.py" -q
```

Expected before implementation:

- FAIL because the current templates do not render red rubies, troop details, or gold-bar/troop donation panels

- [ ] **Step 3: Implement the shared dashboard context**

In `/home/daniel/code/web_game_v5/guilds/views/contribution.py`, add a helper that both `resource_status()` and `donate_resource()` can use:

```python
from gameplay.models import InventoryItem
from guilds.models import GuildTroopStorage, GuildWarehouse


def _build_guild_resource_dashboard_context(member: GuildMember, *, manor: Manor) -> dict[str, object]:
    troop_storages = list(
        GuildTroopStorage.objects.filter(guild=member.guild, count__gt=0)
        .select_related("troop_template")
        .order_by("troop_template__priority", "troop_template__id")
    )
    red_ruby_quantity = (
        GuildWarehouse.objects.filter(guild=member.guild, item_key="red_ruby")
        .values_list("quantity", flat=True)
        .first()
        or 0
    )
    gold_bar_count = (
        InventoryItem.objects.filter(
            manor=manor,
            template__key="gold_bar",
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
        .values_list("quantity", flat=True)
        .first()
        or 0
    )
    return {
        "manor": manor,
        "red_ruby_quantity": red_ruby_quantity,
        "troop_storages": troop_storages,
        "troop_total_count": sum(storage.count for storage in troop_storages),
        "troop_type_count": len(troop_storages),
        "troop_preview": troop_storages[:3],
        "gold_bar_count": gold_bar_count,
        "contribution_rates": CONTRIBUTION_RATES,
        "daily_limits": DAILY_DONATION_LIMITS,
    }
```

Use that helper from both `resource_status()` and the GET branch of `donate_resource()`.

- [ ] **Step 4: Rebuild the templates around the shared dashboard**

Update `/home/daniel/code/web_game_v5/guilds/templates/guilds/resources.html` so the top section renders:

```django
<section class="tw-card">
    <h2>资源总览</h2>
    <div class="tw-guild-resource-cards">
        <div class="tw-guild-resource-card tw-guild-resource-silver">
            <h4>银两</h4>
            <p class="tw-guild-resource-amount">{{ guild.silver|floatformat:0 }}</p>
        </div>
        <div class="tw-guild-resource-card tw-guild-resource-grain">
            <h4>粮食</h4>
            <p class="tw-guild-resource-amount">{{ guild.grain|floatformat:0 }}</p>
        </div>
        <div class="tw-guild-resource-card tw-guild-resource-gold">
            <h4>金条</h4>
            <p class="tw-guild-resource-amount">{{ guild.gold_bar }}</p>
        </div>
        <div class="tw-guild-resource-card tw-guild-resource-purple">
            <h4>红宝石</h4>
            <p class="tw-guild-resource-amount">{{ red_ruby_quantity }}</p>
        </div>
    </div>
</section>
```

Then add a troop overview/detail section:

```django
<section class="tw-card tw-guild-section-spacing">
    <h2>护院概览</h2>
    <p class="caption">总数 {{ troop_total_count }} / 种类 {{ troop_type_count }}</p>
    {% for storage in troop_preview %}
    <div class="tw-guild-announcement">
        <div class="tw-guild-announcement-header">
            <span class="tw-guild-announcement-title">{{ storage.troop_template.name }}</span>
            <span class="tw-muted">库存 {{ storage.count }}</span>
        </div>
    </div>
    {% endfor %}
    <div class="mt-4">
        {% for storage in troop_storages %}
        <div class="tw-guild-announcement">
            <div class="tw-guild-announcement-header">
                <span class="tw-guild-announcement-title">{{ storage.troop_template.name }}</span>
                <span class="tw-muted">库存 {{ storage.count }}</span>
            </div>
            <p class="m-0">模板标识：{{ storage.troop_template.key }}</p>
        </div>
        {% empty %}
        <p class="tw-muted">当前还没有帮会护院库存。</p>
        {% endfor %}
    </div>
</section>
```

Rework `/home/daniel/code/web_game_v5/guilds/templates/guilds/donate.html` to render the same unified donation layout instead of the old two-card page. Reuse the existing action route (`method="post"` on `guilds:donate`) for silver/grain/gold-bar forms and `action="{% url 'guilds:donate_troops' %}"` for troop donations.

- [ ] **Step 5: Run the resource dashboard tests and verify they pass**

Run:

```bash
pytest "tests/test_guild_resource_views.py" -q
```

Expected:

- PASS

### Task 3: Move Troop Donation UX to the Resource Page Without Changing the Service

**Files:**
- Modify: `/home/daniel/code/web_game_v5/guilds/views/missions.py`
- Modify: `/home/daniel/code/web_game_v5/tests/test_guild_troop_donation.py`

- [ ] **Step 1: Write the failing redirect expectation**

In `/home/daniel/code/web_game_v5/tests/test_guild_troop_donation.py`, change the success view test to assert the final redirect target is the resource page:

```python
assert response.redirect_chain[-1][0].endswith(reverse("guilds:resources"))
assert "护院已捐赠到帮会护院池" in response.content.decode("utf-8")
```

Add the same redirect expectation to the failure-path tests so the troop donation flow is consistently routed through the new unified dashboard.

- [ ] **Step 2: Run the troop donation tests and verify they fail**

Run:

```bash
pytest "tests/test_guild_troop_donation.py" -q
```

Expected before implementation:

- FAIL because `donate_troops()` still redirects to `guilds:missions`

- [ ] **Step 3: Update the redirect target only**

In `/home/daniel/code/web_game_v5/guilds/views/missions.py`, keep the service call and success message untouched and only change:

```python
return redirect("guilds:resources")
```

Do not move the route or duplicate the donation logic.

- [ ] **Step 4: Re-run the troop donation tests and verify they pass**

Run:

```bash
pytest "tests/test_guild_troop_donation.py" -q
```

Expected:

- PASS

### Task 4: Rebuild the Guild Mission Page Around Tabs and a Detail Modal

**Files:**
- Modify: `/home/daniel/code/web_game_v5/guilds/services/guild_missions.py`
- Modify: `/home/daniel/code/web_game_v5/guilds/templates/guilds/missions.html`
- Create: `/home/daniel/code/web_game_v5/guilds/templates/guilds/partials/mission_detail_modal.html`
- Delete: `/home/daniel/code/web_game_v5/guilds/templates/guilds/partials/mission_launch_form.html`
- Modify: `/home/daniel/code/web_game_v5/static/js/tasks-page.js`
- Modify: `/home/daniel/code/web_game_v5/tests/test_guild_mission_views.py`

- [ ] **Step 1: Write failing mission-page layout tests**

Extend `/home/daniel/code/web_game_v5/tests/test_guild_mission_views.py` with coverage like:

```python
@pytest.mark.django_db
def test_guild_mission_page_uses_task_tabs_and_hides_old_troop_pool(guild_member_client):
    client, _user, _guild = guild_member_client
    GuildMissionTemplate.objects.create(
        key="guild_page_tab_task",
        name="分页任务",
        description="",
        difficulty="junior",
        task_type="patrol",
        base_duration_seconds=600,
        ruby_reward=12,
        recommended_guest_count=2,
        allow_troops=True,
        is_active=True,
        sort_weight=1,
    )

    response = client.get(reverse("guilds:missions"))
    body = response.content.decode("utf-8")
    assert "tw-mission-tabs" in body
    assert "帮会护院池" not in body
    assert "详情" in body
```

Add a selected-mission detail test:

```python
@pytest.mark.django_db
def test_guild_mission_page_renders_selected_task_detail_modal(guild_member_client):
    client, user, guild = guild_member_client
    template = GuildMissionTemplate.objects.create(
        key="guild_modal_task",
        name="详情任务",
        description="显示详情",
        difficulty="intermediate",
        task_type="escort",
        base_duration_seconds=900,
        ruby_reward=20,
        recommended_guest_count=1,
        allow_troops=True,
        is_active=True,
        sort_weight=2,
    )
    leader_member = user.guild_membership
    guest_template = _create_template("guild_modal_tpl")
    guest = _create_guest(manor=user.manor, template=guest_template, name="详情门客")
    entry = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=user, pool_entry_id=entry.id)
    response = client.get(f"{reverse('guilds:missions')}?mission={template.key}")
    body = response.content.decode("utf-8")
    assert "详情任务" in body
    assert "选择门客" in body
    assert "配置护院" in body
    assert "tw-modal-overlay" in body
```

- [ ] **Step 2: Run the mission view tests and verify they fail**

Run:

```bash
pytest "tests/test_guild_mission_views.py" -q
```

Expected before implementation:

- FAIL because the page still renders the inline launch form and the `帮会护院池` block

- [ ] **Step 3: Extend the mission page context for tabs and selected mission detail**

In `/home/daniel/code/web_game_v5/guilds/services/guild_missions.py`, reshape `get_guild_mission_page_context()` so it returns grouped templates and an optional selected mission:

```python
def get_guild_mission_page_context(member: GuildMember, *, selected_mission_key: str = "") -> dict[str, Any]:
    guild = member.guild
    now = timezone.now()
    active_run = (
        GuildMissionRun.objects.select_related("template", "started_by__user__manor")
        .filter(guild=guild, status=GuildMissionRun.Status.ACTIVE)
        .filter(Q(return_at__isnull=True) | Q(return_at__gt=now))
        .order_by("-started_at")
        .first()
    )
    mission_templates = list(GuildMissionTemplate.objects.filter(is_active=True).order_by("sort_weight", "id"))
    lineup_entries = list(
        GuildBattleLineupEntry.objects.filter(guild=guild)
        .select_related("pool_entry__source_guest__template", "pool_entry__owner_member__user__manor")
        .order_by("slot_index", "id")
    )
    troop_storages = list(
        GuildTroopStorage.objects.filter(guild=guild, count__gt=0)
        .select_related("troop_template")
        .order_by("troop_template__priority", "troop_template__id")
    )
    mission_groups = {
        "junior": [mission for mission in mission_templates if mission.difficulty == "junior"],
        "intermediate": [mission for mission in mission_templates if mission.difficulty == "intermediate"],
        "advanced": [mission for mission in mission_templates if mission.difficulty == "advanced"],
    }
    selected_mission = next(
        (mission for mission in mission_templates if mission.key == selected_mission_key),
        None,
    )
    return {
        "guild": guild,
        "member": member,
        "active_run": active_run,
        "mission_groups": mission_groups,
        "selected_mission": selected_mission,
        "lineup_entries": lineup_entries,
        "troop_storages": troop_storages,
        "dispatch_limit": get_guild_dispatch_capacity(guild),
        "lineup_limit": get_guild_lineup_capacity(guild),
    }
```

Then in `/home/daniel/code/web_game_v5/guilds/views/missions.py`, pass the query parameter:

```python
selected_mission_key = str(request.GET.get("mission", "")).strip()
context = guild_mission_service.get_guild_mission_page_context(
    request.guild_member,
    selected_mission_key=selected_mission_key,
)
```

- [ ] **Step 4: Replace the mission template with the personal-task structure**

Update `/home/daniel/code/web_game_v5/guilds/templates/guilds/missions.html` to follow the same high-level pattern as `gameplay/templates/gameplay/tasks.html`:

```django
<nav class="tw-mission-tabs">
    <button class="tw-trade-tab active" data-tab="junior">初级任务</button>
    <button class="tw-trade-tab" data-tab="intermediate">中级任务</button>
    <button class="tw-trade-tab" data-tab="advanced">高级任务</button>
</nav>

<section class="tw-card" style="padding: 0; overflow: hidden; border-top-left-radius: 0;">
    <div class="tw-mission-content">
        <div id="tab-junior" class="mission-tab-content active">
            <table class="tw-mission-table">
                <thead>
                    <tr>
                        <th>任务名称</th>
                        <th class="w-[120px] text-center">类型</th>
                        <th class="w-[120px] text-center">红宝石</th>
                        <th class="w-[120px] text-center">操作</th>
                    </tr>
                </thead>
                <tbody>
                    {% for mission in mission_groups.junior %}
                    <tr>
                        <td><div class="font-bold text-text-primary">{{ mission.name }}</div></td>
                        <td class="text-center"><span class="tw-chip">{{ mission.get_task_type_display }}</span></td>
                        <td class="text-center"><span class="tw-chip">{{ mission.ruby_reward }}</span></td>
                        <td class="text-center"><a class="tw-btn-primary tw-btn-sm" href="?mission={{ mission.key }}">详情</a></td>
                    </tr>
                    {% empty %}
                    <tr><td colspan="4" class="tw-muted text-center py-8">暂无任务配置。</td></tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        <div id="tab-intermediate" class="mission-tab-content" style="display: none;">
            <table class="tw-mission-table">
                <thead>
                    <tr>
                        <th>任务名称</th>
                        <th class="w-[120px] text-center">类型</th>
                        <th class="w-[120px] text-center">红宝石</th>
                        <th class="w-[120px] text-center">操作</th>
                    </tr>
                </thead>
                <tbody>
                    {% for mission in mission_groups.intermediate %}
                    <tr>
                        <td><div class="font-bold text-text-primary">{{ mission.name }}</div></td>
                        <td class="text-center"><span class="tw-chip">{{ mission.get_task_type_display }}</span></td>
                        <td class="text-center"><span class="tw-chip">{{ mission.ruby_reward }}</span></td>
                        <td class="text-center"><a class="tw-btn-primary tw-btn-sm" href="?mission={{ mission.key }}">详情</a></td>
                    </tr>
                    {% empty %}
                    <tr><td colspan="4" class="tw-muted text-center py-8">暂无任务配置。</td></tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        <div id="tab-advanced" class="mission-tab-content" style="display: none;">
            <table class="tw-mission-table">
                <thead>
                    <tr>
                        <th>任务名称</th>
                        <th class="w-[120px] text-center">类型</th>
                        <th class="w-[120px] text-center">红宝石</th>
                        <th class="w-[120px] text-center">操作</th>
                    </tr>
                </thead>
                <tbody>
                    {% for mission in mission_groups.advanced %}
                    <tr>
                        <td><div class="font-bold text-text-primary">{{ mission.name }}</div></td>
                        <td class="text-center"><span class="tw-chip">{{ mission.get_task_type_display }}</span></td>
                        <td class="text-center"><span class="tw-chip">{{ mission.ruby_reward }}</span></td>
                        <td class="text-center"><a class="tw-btn-primary tw-btn-sm" href="?mission={{ mission.key }}">详情</a></td>
                    </tr>
                    {% empty %}
                    <tr><td colspan="4" class="tw-muted text-center py-8">暂无任务配置。</td></tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
</section>
```

Create `/home/daniel/code/web_game_v5/guilds/templates/guilds/partials/mission_detail_modal.html` with a personal-task-style overlay that renders:

```django
{% if selected_mission %}
<div class="tw-modal-overlay" aria-hidden="false">
    <div class="tw-modal-content" style="max-width: 900px;">
        <div class="tw-modal-header">
            <div>
                <p class="tw-muted text-sm mb-2">帮会任务情报</p>
                <h3>{{ selected_mission.name }}</h3>
            </div>
            <a href="{% url 'guilds:missions' %}" class="tw-btn-secondary tw-btn-sm">关闭</a>
        </div>
        <div class="tw-modal-body">
            <div class="grid grid-cols-1 md:grid-cols-2 gap-8">
                <div class="task-info">
                    <p class="tw-muted mb-4">{{ selected_mission.description|default:"暂无任务描述" }}</p>
                    <div class="flex items-center gap-2 flex-wrap mb-4">
                        <span class="tw-chip">推荐门客 {{ selected_mission.recommended_guest_count }}</span>
                        <span class="tw-chip">红宝石 {{ selected_mission.ruby_reward }}</span>
                        <span class="tw-chip">
                            {% if selected_mission.allow_troops %}允许护院{% else %}仅门客{% endif %}
                        </span>
                    </div>
                    <p class="tw-muted text-sm">预计耗时 {{ selected_mission.base_duration_seconds }} 秒</p>
                </div>
                <div>
                    <h4 class="mb-4 font-bold">出征配置</h4>
                    <form method="post" action="{% url 'guilds:mission_launch' %}">
                        {% csrf_token %}
                        <input type="hidden" name="template_key" value="{{ selected_mission.key }}">
                        <div class="tw-guest-selection">
                            {% for lineup in lineup_entries %}
                            <label class="tw-guest-checkbox">
                                <input type="checkbox" name="pool_entry_ids" value="{{ lineup.pool_entry_id }}" class="guest-input">
                                <div class="tw-guest-card">
                                    <div class="tw-guest-details">
                                        <div class="tw-guest-info-row">
                                            <span class="tw-guest-name-sm">{{ lineup.pool_entry.source_guest.display_name }}</span>
                                            <span class="tw-guest-level-sm">位 {{ lineup.slot_index }}</span>
                                        </div>
                                        <div class="tw-guest-stats-sm">
                                            <span>{{ lineup.pool_entry.owner_member.user.manor.display_name }}</span>
                                        </div>
                                    </div>
                                </div>
                            </label>
                            {% empty %}
                            <p class="tw-muted">暂无可派遣上阵门客</p>
                            {% endfor %}
                        </div>
                        <div class="tw-selection-summary">
                            已选择 <strong id="selected-guest-count" data-max-squad="{{ dispatch_limit }}">0</strong> / {{ dispatch_limit }} 人
                        </div>
                        {% if selected_mission.allow_troops %}
                        <div class="tw-troop-selection">
                            {% for storage in troop_storages %}
                            <div class="tw-troop-row">
                                <div class="tw-troop-info">
                                    <div class="tw-troop-details">
                                        <span class="tw-troop-name">{{ storage.troop_template.name }}</span>
                                        <span class="tw-troop-count">可用: {{ storage.count }}</span>
                                    </div>
                                </div>
                                <div class="tw-troop-slider-group">
                                    <input type="range" class="tw-troop-slider" value="0" min="0" max="{{ storage.count }}" data-troop-key="{{ storage.troop_template.key }}" data-max="{{ storage.count }}">
                                    <input type="number" class="tw-troop-num-input" value="0" min="0" max="{{ storage.count }}" data-troop-key="{{ storage.troop_template.key }}" data-max="{{ storage.count }}" name="troop_{{ storage.troop_template.key }}">
                                </div>
                            </div>
                            {% empty %}
                            <p class="tw-muted">暂无可用护院</p>
                            {% endfor %}
                        </div>
                        {% endif %}
                    </form>
                </div>
            </div>
        </div>
    </div>
</div>
{% endif %}
```

Delete `/home/daniel/code/web_game_v5/guilds/templates/guilds/partials/mission_launch_form.html` once `missions.html` no longer includes it.

- [ ] **Step 5: Adjust the shared task-page JavaScript only where needed**

In `/home/daniel/code/web_game_v5/static/js/tasks-page.js`, keep the existing selectors and only make the count/slider logic tolerant of the guild modal rendering. The change should stay narrow, for example:

```javascript
const selectedCountEls = document.querySelectorAll("[id='selected-guest-count']");
const guestInputs = document.querySelectorAll(".guest-input");
const maxSquadSize = Number.parseInt(
  selectedCountEls[0]?.dataset?.maxSquad || document.body.dataset.maxMissionSquad || "0",
  10,
) || 5;

const updateGuestCount = () => {
  selectedCountEls.forEach((element) => {
    const container = element.closest(".tw-modal-body") || document;
    const count = container.querySelectorAll(".guest-input:checked").length;
    element.textContent = String(count);
  });
};
```

Do not fork a second mission-page script unless the shared script becomes impossible to isolate cleanly.

- [ ] **Step 6: Re-run the mission view tests and verify they pass**

Run:

```bash
pytest "tests/test_guild_mission_views.py" -q
```

Expected:

- PASS

### Task 5: Run the End-to-End Guild UI Regression Suite

**Files:**
- Verify: `/home/daniel/code/web_game_v5/tests/guilds/contribution_upgrade.py`
- Verify: `/home/daniel/code/web_game_v5/tests/test_guild_rules_loader.py`
- Verify: `/home/daniel/code/web_game_v5/tests/test_guild_resource_views.py`
- Verify: `/home/daniel/code/web_game_v5/tests/test_guild_troop_donation.py`
- Verify: `/home/daniel/code/web_game_v5/tests/test_guild_mission_views.py`
- Verify: `/home/daniel/code/web_game_v5/tests/test_guild_mission_service.py`

- [ ] **Step 1: Run the focused guild UI regression command**

Run:

```bash
pytest \
  "tests/guilds/contribution_upgrade.py" \
  "tests/test_guild_rules_loader.py" \
  "tests/test_guild_resource_views.py" \
  "tests/test_guild_troop_donation.py" \
  "tests/test_guild_mission_views.py" \
  "tests/test_guild_mission_service.py" \
  -q
```

Expected:

- PASS

- [ ] **Step 2: Verify the final user-visible behaviors**

Check the implementation against the spec by confirming all of the following are true in rendered HTML or tests:

```text
- guild resources page shows 银两 / 粮食 / 金条 / 红宝石
- guild resources page shows troop summary plus full troop detail list
- unified donation area includes silver, grain, gold bar, and troop donation forms
- troop donation no longer returns users to the guild missions page
- guild mission page no longer renders “帮会护院池”
- guild mission page uses junior/intermediate/advanced tabs and a query-driven detail modal
```

## Self-Review

- Spec coverage: the plan covers all approved requirements from the spec: full resource display, gold-bar donation support, troop donation migration, task-style guild mission layout, removal of the old troop-pool block, and regression testing.
- Placeholder scan: no `TODO` / `TBD` placeholders remain; each task includes concrete files, code shapes, commands, and expected outcomes.
- Type consistency: the plan uses existing codebase types and names (`GuildWarehouse`, `GuildTroopStorage`, `GuildMissionTemplate.difficulty`, `InventoryItem.StorageLocation.WAREHOUSE`, `donate_resource`, `donate_troops`) that already exist in this repository.
