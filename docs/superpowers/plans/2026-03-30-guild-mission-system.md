# Guild Mission System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为帮会增加主动派遣型任务、帮会公共护院池、红宝石奖励与两条红宝石科技，并把当前帮会任务接入首页事件栏与帮会页面。

**Architecture:** 在 `guilds` 模块内新增独立的任务与护院领域模型、service、view 和 Celery 任务；复用现有帮会门客池、帮会仓库、帮会科技和 battle 执行入口。门客在帮会任务中按发起瞬间生成快照但不占用真实状态，护院则从帮会公共池真实扣减并按战报返还存活部分。

**Tech Stack:** Django ORM、Django views/templates、Celery、pytest、现有 battle 执行与 guilds/gameplay 服务层

---

## File Map

### Create

- `guilds/models/missions.py`
- `guilds/services/guild_troops.py`
- `guilds/services/guild_missions.py`
- `guilds/views/missions.py`
- `guilds/templates/guilds/missions.html`
- `tests/test_guild_troop_donation.py`
- `tests/test_guild_mission_service.py`
- `tests/test_guild_mission_views.py`
- `tests/test_guild_home_mission_events.py`
- `guilds/migrations/0008_guild_mission_and_troop_models.py`

### Modify

- `guilds/models/__init__.py`
- `guilds/admin.py`
- `guilds/constants.py`
- `data/guild_rules.yaml`
- `data/item_templates.yaml`
- `guilds/services/hero_pool.py`
- `guilds/services/technology.py`
- `guilds/services/warehouse.py`
- `guilds/tasks.py`
- `guilds/urls.py`
- `guilds/templates/guilds/detail.html`
- `templates/landing.html`
- `gameplay/selectors/home.py`
- `tests/test_guild_hero_pool.py`
- `tests/test_guild_hero_pool_views.py`
- `tests/test_guilds_tasks.py`
- `tests/test_guilds_technology_service.py`

## Task 1: Add Guild Mission and Guild Troop Domain Models

**Files:**
- Create: `guilds/models/missions.py`
- Modify: `guilds/models/__init__.py`
- Modify: `guilds/admin.py`
- Create: `guilds/migrations/0008_guild_mission_and_troop_models.py`
- Test: `tests/test_guild_mission_service.py`

- [ ] **Step 1: Write the failing model/service smoke tests**

```python
from guilds.models import GuildMissionRun, GuildMissionTemplate, GuildTroopDonationLog, GuildTroopStorage


@pytest.mark.django_db
def test_guild_mission_run_allows_only_one_active_run_per_guild(django_user_model):
    user = django_user_model.objects.create_user(username="guild_mission_model_leader", password="pass12345")
    guild = Guild.objects.create(name="任务模型帮", founder=user, is_active=True)
    template = GuildMissionTemplate.objects.create(
        key="guild_patrol_alpha",
        name="巡防前哨",
        description="测试任务",
        difficulty="junior",
        task_type="dispatch",
        base_duration_seconds=600,
        ruby_reward=3,
        recommended_guest_count=3,
        allow_troops=True,
        is_active=True,
        sort_weight=10,
    )
    GuildMissionRun.objects.create(guild=guild, template=template, status="active", selected_guest_count=3, ruby_reward=3)

    with pytest.raises(IntegrityError):
        GuildMissionRun.objects.create(
            guild=guild,
            template=template,
            status="active",
            selected_guest_count=2,
            ruby_reward=1,
        )


@pytest.mark.django_db
def test_guild_troop_storage_is_unique_per_template(django_user_model):
    user = django_user_model.objects.create_user(username="guild_troop_model_owner", password="pass12345")
    guild = Guild.objects.create(name="护院模型帮", founder=user, is_active=True)
    troop_template = TroopTemplate.objects.create(
        key="guild_model_archer",
        name="模型弓手",
        description="",
        base_attack=1,
        base_defense=1,
        base_hp=1,
        speed_bonus=0,
        priority=1,
        default_count=0,
    )
    GuildTroopStorage.objects.create(guild=guild, troop_template=troop_template, count=20)

    with pytest.raises(IntegrityError):
        GuildTroopStorage.objects.create(guild=guild, troop_template=troop_template, count=5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest "/home/daniel/code/web_game_v5/tests/test_guild_mission_service.py" -k "model or unique" -q`
Expected: FAIL with import errors because the guild mission models do not exist yet.

- [ ] **Step 3: Add the new Django models**

```python
class GuildMissionTemplate(models.Model):
    class Difficulty(models.TextChoices):
        JUNIOR = "junior", "初级"
        INTERMEDIATE = "intermediate", "中级"
        ADVANCED = "advanced", "高级"

    class TaskType(models.TextChoices):
        DISPATCH = "dispatch", "派遣"

    key = models.SlugField(unique=True)
    name = models.CharField(max_length=128)
    description = models.TextField(blank=True)
    difficulty = models.CharField(max_length=16, choices=Difficulty.choices, default=Difficulty.JUNIOR)
    task_type = models.CharField(max_length=16, choices=TaskType.choices, default=TaskType.DISPATCH)
    base_duration_seconds = models.PositiveIntegerField(default=600)
    ruby_reward = models.PositiveIntegerField(default=1)
    recommended_guest_count = models.PositiveIntegerField(default=1)
    allow_troops = models.BooleanField(default=False)
    enemy_guests = models.JSONField(default=list, blank=True)
    enemy_troops = models.JSONField(default=dict, blank=True)
    enemy_technology = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    sort_weight = models.IntegerField(default=0)


class GuildMissionRun(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "进行中"
        COMPLETED = "completed", "已完成"
        RETREATED = "retreated", "已撤回"

    guild = models.ForeignKey(Guild, on_delete=models.CASCADE, related_name="mission_runs")
    template = models.ForeignKey(GuildMissionTemplate, on_delete=models.PROTECT, related_name="runs")
    started_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    selected_guest_count = models.PositiveIntegerField(default=0)
    ruby_reward = models.PositiveIntegerField(default=0)
    guest_ids = models.JSONField(default=list, blank=True)
    guest_snapshots = models.JSONField(default=list, blank=True)
    troop_loadout = models.JSONField(default=dict, blank=True)
    battle_report = models.ForeignKey("battle.BattleReport", null=True, blank=True, on_delete=models.SET_NULL)
    started_at = models.DateTimeField(auto_now_add=True)
    battle_at = models.DateTimeField(null=True, blank=True)
    return_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["guild"],
                condition=models.Q(status="active"),
                name="guildmission_one_active_run_uq",
            ),
        ]


class GuildTroopStorage(models.Model):
    guild = models.ForeignKey(Guild, on_delete=models.CASCADE, related_name="troop_storages")
    troop_template = models.ForeignKey("battle.TroopTemplate", on_delete=models.CASCADE, related_name="guild_storages")
    count = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("guild", "troop_template")]


class GuildTroopDonationLog(models.Model):
    guild = models.ForeignKey(Guild, on_delete=models.CASCADE, related_name="troop_donation_logs")
    member = models.ForeignKey(GuildMember, on_delete=models.CASCADE, related_name="troop_donation_logs")
    troop_template = models.ForeignKey("battle.TroopTemplate", on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    donated_at = models.DateTimeField(auto_now_add=True)
```

- [ ] **Step 4: Export models, register admin, and add migration seed rows**

```python
from .missions import GuildMissionRun, GuildMissionTemplate, GuildTroopDonationLog, GuildTroopStorage

__all__ += [
    "GuildMissionTemplate",
    "GuildMissionRun",
    "GuildTroopStorage",
    "GuildTroopDonationLog",
]
```

```python
@admin.register(GuildMissionTemplate)
class GuildMissionTemplateAdmin(admin.ModelAdmin):
    list_display = ("key", "name", "difficulty", "allow_troops", "ruby_reward", "is_active")


@admin.register(GuildMissionRun)
class GuildMissionRunAdmin(admin.ModelAdmin):
    list_display = ("guild", "template", "status", "started_by", "started_at", "return_at")
```

```python
def seed_guild_mission_templates(apps, schema_editor):
    GuildMissionTemplate = apps.get_model("guilds", "GuildMissionTemplate")
    defaults = [
        {
            "key": "guild_patrol_alpha",
            "name": "巡防前哨",
            "description": "派出门客巡防前哨，成功后可获得红宝石。",
            "difficulty": "junior",
            "task_type": "dispatch",
            "base_duration_seconds": 900,
            "ruby_reward": 2,
            "recommended_guest_count": 3,
            "allow_troops": False,
            "enemy_guests": [],
            "enemy_troops": {},
            "enemy_technology": {},
            "is_active": True,
            "sort_weight": 10,
        },
        {
            "key": "guild_supply_escort",
            "name": "护送商队",
            "description": "允许携带帮会护院执行护送任务。",
            "difficulty": "intermediate",
            "task_type": "dispatch",
            "base_duration_seconds": 1200,
            "ruby_reward": 4,
            "recommended_guest_count": 4,
            "allow_troops": True,
            "enemy_guests": [],
            "enemy_troops": {"archer": 60},
            "enemy_technology": {},
            "is_active": True,
            "sort_weight": 20,
        },
    ]
    for row in defaults:
        GuildMissionTemplate.objects.update_or_create(key=row["key"], defaults=row)
```

- [ ] **Step 5: Run the model tests again**

Run: `pytest "/home/daniel/code/web_game_v5/tests/test_guild_mission_service.py" -k "model or unique" -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add guilds/models/missions.py guilds/models/__init__.py guilds/admin.py guilds/migrations/0008_guild_mission_and_troop_models.py tests/test_guild_mission_service.py
git commit -m "feat: add guild mission domain models"
```

## Task 2: Add Red Ruby Tech Rules and Dynamic Guild Lineup Capacity

**Files:**
- Modify: `guilds/constants.py`
- Modify: `data/guild_rules.yaml`
- Modify: `data/item_templates.yaml`
- Modify: `guilds/services/hero_pool.py`
- Modify: `guilds/services/technology.py`
- Test: `tests/test_guild_hero_pool.py`
- Test: `tests/test_guilds_technology_service.py`

- [ ] **Step 1: Write the failing tests for dynamic capacity and ruby tech upgrades**

```python
@pytest.mark.django_db
def test_lineup_limit_uses_guild_lineup_capacity_tech(django_user_model):
    leader, leader_manor = _create_user_with_manor(django_user_model, "guild_lineup_tech_leader")
    guild = Guild.objects.create(name="科技上阵帮", founder=leader)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    GuildTechnology.objects.create(guild=guild, tech_key="guild_lineup_capacity", level=3, max_level=20)

    template = _create_template("guild_lineup_capacity_tpl")
    entries = []
    for idx in range(24):
        user, manor = _create_user_with_manor(django_user_model, f"guild_lineup_{idx}")
        member = GuildMember.objects.create(guild=guild, user=user, position="member")
        guest = _create_guest(manor=manor, template=template, name=f"门客{idx}")
        entries.append(hero_pool_service.submit_hero_pool_entry(member, guest_id=guest.id, slot_index=1).entry)

    for entry in entries[:23]:
        hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry.id)

    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entries[23].id)
    assert GuildBattleLineupEntry.objects.filter(guild=guild).count() == 24


@pytest.mark.django_db
def test_upgrade_new_guild_capacity_tech_consumes_red_ruby(django_user_model):
    user = django_user_model.objects.create_user(username="guild_ruby_tech_user", password="pass12345")
    guild = Guild.objects.create(name="红宝石科技帮", founder=user, is_active=True)
    GuildMember.objects.create(guild=guild, user=user, position="leader")
    tech = GuildTechnology.objects.create(guild=guild, tech_key="guild_dispatch_capacity", level=0, max_level=20)
    GuildWarehouse.objects.create(guild=guild, item_key="red_ruby", quantity=10, contribution_cost=0)

    technology_service.upgrade_technology(guild, tech.tech_key, user)

    tech.refresh_from_db()
    ruby = GuildWarehouse.objects.get(guild=guild, item_key="red_ruby")
    assert tech.level == 1
    assert ruby.quantity == 9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest "/home/daniel/code/web_game_v5/tests/test_guild_hero_pool.py" "/home/daniel/code/web_game_v5/tests/test_guilds_technology_service.py" -k "lineup_limit_uses_guild_lineup_capacity_tech or consumes_red_ruby" -q`
Expected: FAIL because the new tech keys, dynamic lineup helper, and `red_ruby` branch do not exist.

- [ ] **Step 3: Add new guild tech config and ruby item**

```yaml
technology:
  upgrade_costs:
    guild_lineup_capacity:
      red_ruby: 1
    guild_dispatch_capacity:
      red_ruby: 1
  names:
    guild_lineup_capacity: 帮会可上阵门客
    guild_dispatch_capacity: 帮会出战门客
hero_pool:
  battle_lineup_limit: 20
  dispatch_guest_base_limit: 5
```

```yaml
- key: red_ruby
  name: 红宝石
  category: material
  rarity: purple
  stackable: true
  is_usable: false
  description: 帮会任务奖励，用于升级帮会可上阵门客与出战门客科技。
```

- [ ] **Step 4: Replace fixed lineup limit reads with helper-based limits**

```python
def get_guild_lineup_capacity(guild: Guild) -> int:
    return GUILD_BATTLE_LINEUP_LIMIT + get_guild_tech_level(guild, "guild_lineup_capacity")


def get_guild_dispatch_capacity(guild: Guild) -> int:
    return GUILD_DISPATCH_GUEST_BASE_LIMIT + get_guild_tech_level(guild, "guild_dispatch_capacity")
```

```python
lineup_limit = technology_service.get_guild_lineup_capacity(locked_guild)
if len(lineup_rows) >= lineup_limit:
    raise GuildValidationError(f"出战名单已满（最多 {lineup_limit} 名）")
```

```python
if tech_key in {"guild_lineup_capacity", "guild_dispatch_capacity"}:
    ruby_cost = calculate_tech_upgrade_cost(tech_key, tech_locked.level)["red_ruby"]
    warehouse_item = GuildWarehouse.objects.select_for_update().filter(guild=guild_locked, item_key="red_ruby").first()
    if not warehouse_item or warehouse_item.quantity < ruby_cost:
        raise GuildTechnologyError(f"帮会红宝石不足，需要{ruby_cost}")
    GuildWarehouse.objects.filter(pk=warehouse_item.pk).update(quantity=F("quantity") - ruby_cost)
    GuildTechnology.objects.filter(pk=tech_locked.pk).update(level=F("level") + 1)
    return
```

- [ ] **Step 5: Run the targeted tests again**

Run: `pytest "/home/daniel/code/web_game_v5/tests/test_guild_hero_pool.py" "/home/daniel/code/web_game_v5/tests/test_guilds_technology_service.py" -k "lineup_limit_uses_guild_lineup_capacity_tech or consumes_red_ruby" -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add guilds/constants.py data/guild_rules.yaml data/item_templates.yaml guilds/services/hero_pool.py guilds/services/technology.py tests/test_guild_hero_pool.py tests/test_guilds_technology_service.py
git commit -m "feat: add ruby tech rules for guild missions"
```

## Task 3: Add Guild Troop Donation and Guild Troop Storage Services

**Files:**
- Create: `guilds/services/guild_troops.py`
- Modify: `guilds/views/missions.py`
- Modify: `guilds/urls.py`
- Test: `tests/test_guild_troop_donation.py`

- [ ] **Step 1: Write the failing troop donation tests**

```python
@pytest.mark.django_db(transaction=True)
def test_donate_guild_troops_moves_units_from_player_to_guild(django_user_model):
    user = django_user_model.objects.create_user(username="guild_troop_donor", password="pass12345")
    manor = ensure_manor(user)
    guild = Guild.objects.create(name="护院捐赠帮", founder=user, is_active=True)
    member = GuildMember.objects.create(guild=guild, user=user, position="leader")
    troop_template = TroopTemplate.objects.create(
        key="guild_donate_archer",
        name="捐赠弓手",
        description="",
        base_attack=1,
        base_defense=1,
        base_hp=1,
        speed_bonus=0,
        priority=1,
        default_count=0,
    )
    PlayerTroop.objects.create(manor=manor, troop_template=troop_template, count=80)

    guild_troop_service.donate_troops(member=member, troop_key=troop_template.key, quantity=30)

    player_troop = PlayerTroop.objects.get(manor=manor, troop_template=troop_template)
    guild_troop = GuildTroopStorage.objects.get(guild=guild, troop_template=troop_template)
    assert player_troop.count == 50
    assert guild_troop.count == 30
    assert GuildTroopDonationLog.objects.filter(guild=guild, member=member, troop_template=troop_template).exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest "/home/daniel/code/web_game_v5/tests/test_guild_troop_donation.py" -q`
Expected: FAIL because the guild troop donation service and endpoint do not exist.

- [ ] **Step 3: Implement transactional troop donation helpers**

```python
@transaction.atomic
def donate_troops(*, member: GuildMember, troop_key: str, quantity: int) -> None:
    if quantity <= 0:
        raise GuildValidationError("捐赠数量必须大于 0")

    locked_member = GuildMember.objects.select_for_update().select_related("guild", "user__manor").get(pk=member.pk)
    player_troop = (
        PlayerTroop.objects.select_for_update()
        .select_related("troop_template")
        .filter(manor=locked_member.user.manor, troop_template__key=troop_key)
        .first()
    )
    if not player_troop or player_troop.count < quantity:
        raise GuildValidationError(f"护院 {troop_key} 数量不足")

    PlayerTroop.objects.filter(pk=player_troop.pk).update(count=F("count") - quantity)
    storage, _ = GuildTroopStorage.objects.get_or_create(
        guild=locked_member.guild,
        troop_template=player_troop.troop_template,
        defaults={"count": 0},
    )
    GuildTroopStorage.objects.filter(pk=storage.pk).update(count=F("count") + quantity)
    GuildTroopDonationLog.objects.create(
        guild=locked_member.guild,
        member=locked_member,
        troop_template=player_troop.troop_template,
        quantity=quantity,
    )
```

- [ ] **Step 4: Add the donation POST endpoint to the guild missions page**

```python
@login_required
@require_guild_member
@require_POST
def donate_troops(request: Any) -> HttpResponse:
    member = request.guild_member
    troop_key = str(request.POST.get("troop_key", "")).strip()
    quantity = safe_int(request.POST.get("quantity"), default=0, min_val=1)
    execute_guild_action(
        request,
        action=lambda: guild_troop_service.donate_troops(member=member, troop_key=troop_key, quantity=quantity),
        success_message="护院已捐赠到帮会护院池",
    )
    return redirect("guilds:missions")
```

- [ ] **Step 5: Run donation tests again**

Run: `pytest "/home/daniel/code/web_game_v5/tests/test_guild_troop_donation.py" -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add guilds/services/guild_troops.py guilds/views/missions.py guilds/urls.py tests/test_guild_troop_donation.py
git commit -m "feat: add guild troop donation flow"
```

## Task 4: Implement Guild Mission Launch, Retreat, and Auto Finalization

**Files:**
- Create: `guilds/services/guild_missions.py`
- Modify: `guilds/tasks.py`
- Test: `tests/test_guild_mission_service.py`
- Test: `tests/test_guilds_tasks.py`

- [ ] **Step 1: Write the failing guild mission lifecycle tests**

```python
@pytest.mark.django_db(transaction=True)
def test_launch_guild_mission_snapshots_guests_and_deducts_troops(django_user_model):
    leader, leader_manor = _create_user_with_manor(django_user_model, "guild_mission_launch_leader")
    guild = Guild.objects.create(name="帮会任务发起帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    template = GuildMissionTemplate.objects.create(
        key="guild_launch_task",
        name="巡山",
        description="",
        difficulty="junior",
        task_type="dispatch",
        base_duration_seconds=600,
        ruby_reward=2,
        recommended_guest_count=2,
        allow_troops=True,
        is_active=True,
        sort_weight=1,
    )
    GuildTechnology.objects.create(guild=guild, tech_key="guild_dispatch_capacity", level=2, max_level=20)
    troop_template = TroopTemplate.objects.create(key="guild_launch_archer", name="任务弓手", description="", base_attack=1, base_defense=1, base_hp=1, speed_bonus=0, priority=1, default_count=0)
    GuildTroopStorage.objects.create(guild=guild, troop_template=troop_template, count=50)
    guest_a = _create_guest(manor=leader_manor, template=_create_template("guild_launch_tpl_a"), name="甲")
    guest_b = _create_guest(manor=leader_manor, template=_create_template("guild_launch_tpl_b"), name="乙")
    entry_a = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest_a.id, slot_index=1).entry
    entry_b = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest_b.id, slot_index=2).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry_a.id)
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry_b.id)

    run = guild_mission_service.launch_guild_mission(
        guild=guild,
        operator=leader,
        template_key=template.key,
        pool_entry_ids=[entry_a.id, entry_b.id],
        troop_loadout={troop_template.key: 20},
    )

    assert run.status == GuildMissionRun.Status.ACTIVE
    assert run.selected_guest_count == 2
    assert len(run.guest_snapshots) == 2
    assert GuildTroopStorage.objects.get(guild=guild, troop_template=troop_template).count == 30


@pytest.mark.django_db(transaction=True)
def test_retreat_guild_mission_returns_all_troops_without_ruby_reward(django_user_model):
    leader, leader_manor = _create_user_with_manor(django_user_model, "guild_mission_retreat_leader")
    guild = Guild.objects.create(name="帮会任务撤回帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    template = GuildMissionTemplate.objects.create(
        key="guild_retreat_task",
        name="撤回测试任务",
        description="",
        difficulty="junior",
        task_type="dispatch",
        base_duration_seconds=600,
        ruby_reward=3,
        recommended_guest_count=1,
        allow_troops=True,
        is_active=True,
        sort_weight=2,
    )
    troop_template = TroopTemplate.objects.create(key="guild_retreat_archer", name="撤回弓手", description="", base_attack=1, base_defense=1, base_hp=1, speed_bonus=0, priority=1, default_count=0)
    storage = GuildTroopStorage.objects.create(guild=guild, troop_template=troop_template, count=50)
    guest = _create_guest(manor=leader_manor, template=_create_template("guild_retreat_tpl"), name="撤回门客")
    entry = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry.id)
    run = guild_mission_service.launch_guild_mission(
        guild=guild,
        operator=leader,
        template_key=template.key,
        pool_entry_ids=[entry.id],
        troop_loadout={troop_template.key: 20},
    )

    guild_mission_service.request_retreat(run=run, operator=leader)
    run.refresh_from_db()
    storage.refresh_from_db()
    warehouse_item = GuildWarehouse.objects.filter(guild=guild, item_key="red_ruby").first()
    assert run.status == GuildMissionRun.Status.RETREATED
    assert storage.count == 50
    assert warehouse_item is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest "/home/daniel/code/web_game_v5/tests/test_guild_mission_service.py" "/home/daniel/code/web_game_v5/tests/test_guilds_tasks.py" -k "guild_mission" -q`
Expected: FAIL because the guild mission service and completion task do not exist.

- [ ] **Step 3: Implement launch and retreat service functions**

```python
@transaction.atomic
def launch_guild_mission(*, guild: Guild, operator, template_key: str, pool_entry_ids: list[int], troop_loadout: dict[str, int]) -> GuildMissionRun:
    membership = GuildMember.objects.select_for_update().filter(guild=guild, user=operator, is_active=True).first()
    if not membership or not membership.can_manage:
        raise GuildPermissionError("只有管理员/帮主可以发起帮会任务")
    if GuildMissionRun.objects.select_for_update().filter(guild=guild, status=GuildMissionRun.Status.ACTIVE).exists():
        raise GuildValidationError("当前已有帮会任务进行中")

    template = GuildMissionTemplate.objects.filter(key=template_key, is_active=True).first()
    if not template:
        raise GuildValidationError("帮会任务不存在")

    lineup_rows = list(
        GuildBattleLineupEntry.objects.select_for_update()
        .select_related("pool_entry__source_guest__template", "pool_entry__owner_member__user__manor")
        .filter(guild=guild, pool_entry_id__in=pool_entry_ids)
        .order_by("slot_index")
    )
    dispatch_limit = technology_service.get_guild_dispatch_capacity(guild)
    if len(lineup_rows) == 0 or len(lineup_rows) > dispatch_limit:
        raise GuildValidationError(f"本次最多只能派出 {dispatch_limit} 名门客")

    guests = [row.pool_entry.source_guest for row in lineup_rows]
    guest_snapshots = build_guest_battle_snapshots(guests, include_identity=True)
    normalized_troops = normalize_positive_int_mapping(troop_loadout if template.allow_troops else {})
    guild_troop_service.deduct_guild_troops(guild=guild, loadout=normalized_troops)

    now = timezone.now()
    run = GuildMissionRun.objects.create(
        guild=guild,
        template=template,
        started_by=operator,
        status=GuildMissionRun.Status.ACTIVE,
        selected_guest_count=len(guests),
        ruby_reward=template.ruby_reward,
        guest_ids=[guest.id for guest in guests],
        guest_snapshots=guest_snapshots,
        troop_loadout=normalized_troops,
        battle_at=now + timedelta(seconds=template.base_duration_seconds),
        return_at=now + timedelta(seconds=template.base_duration_seconds),
    )
    schedule_guild_mission_completion(run)
    return run
```

```python
@transaction.atomic
def request_retreat(*, run: GuildMissionRun, operator) -> None:
    locked_run = GuildMissionRun.objects.select_for_update().select_related("guild").filter(pk=run.pk).first()
    membership = GuildMember.objects.select_for_update().filter(guild=locked_run.guild, user=operator, is_active=True).first()
    if not membership or not membership.can_manage:
        raise GuildPermissionError("只有管理员/帮主可以撤回帮会任务")
    if locked_run.status != GuildMissionRun.Status.ACTIVE:
        raise GuildValidationError("当前任务不可撤回")

    guild_troop_service.add_guild_troops(guild=locked_run.guild, loadout=locked_run.troop_loadout)
    locked_run.status = GuildMissionRun.Status.RETREATED
    locked_run.completed_at = timezone.now()
    locked_run.save(update_fields=["status", "completed_at"])
```

- [ ] **Step 4: Implement battle-based finalization and Celery scheduling**

```python
def finalize_guild_mission_run(run: GuildMissionRun, *, now=None) -> None:
    now = now or timezone.now()
    locked_run = GuildMissionRun.objects.select_for_update().select_related("guild", "template").filter(pk=run.pk).first()
    if not locked_run or locked_run.status != GuildMissionRun.Status.ACTIVE:
        return

    guest_models = build_snapshot_guest_models(locked_run.guest_snapshots)
    report = execute_battle(
        guests=guest_models,
        options=BattleOptions(
            battle_type="guild_mission",
            troop_loadout=locked_run.troop_loadout,
            fill_default_troops=False,
            defender_setup={"guests": locked_run.template.enemy_guests, "troops": locked_run.template.enemy_troops, "technology": locked_run.template.enemy_technology},
            opponent_name=locked_run.template.name,
            auto_reward=False,
            send_message=False,
            apply_damage=False,
            validate_attacker_troop_capacity=False,
        ),
    )
    surviving = guild_troop_service.calculate_surviving_guild_troops(locked_run.troop_loadout, report)
    guild_troop_service.add_guild_troops(guild=locked_run.guild, loadout=surviving)
    if getattr(report, "winner", "") == "attacker":
        warehouse_service.add_item_to_warehouse(locked_run.guild, "red_ruby", locked_run.ruby_reward, 0)
    locked_run.status = GuildMissionRun.Status.COMPLETED
    locked_run.completed_at = now
    locked_run.battle_report = report
    locked_run.save(update_fields=["status", "completed_at", "battle_report"])
```

```python
@shared_task(name="guilds.complete_guild_mission", bind=True, max_retries=2, default_retry_delay=30)
def complete_guild_mission_task(self, run_id: int):
    run = GuildMissionRun.objects.select_related("guild", "template").filter(pk=run_id).first()
    if not run:
        return "not_found"
    if run.return_at and run.return_at > timezone.now():
        return maybe_reschedule_for_future(
            task_func=complete_guild_mission_task,
            record_id=run_id,
            eta_value=run.return_at,
            dedup_key=f"guild_mission:complete:{run_id}",
            schedule_func=safe_apply_async_with_dedup,
            logger=logger,
            now_func=timezone.now,
            log_message=f"guild mission reschedule failed: run_id={run_id}",
            failure_message=f"guild mission reschedule dispatch failed: run_id={run_id}",
            dedup_timeout=DEFAULT_TASK_DEDUP_TIMEOUT,
        )
    finalize_guild_mission_run(run)
    return "completed"
```

- [ ] **Step 5: Run lifecycle tests again**

Run: `pytest "/home/daniel/code/web_game_v5/tests/test_guild_mission_service.py" "/home/daniel/code/web_game_v5/tests/test_guilds_tasks.py" -k "guild_mission" -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add guilds/services/guild_missions.py guilds/tasks.py tests/test_guild_mission_service.py tests/test_guilds_tasks.py
git commit -m "feat: add guild mission lifecycle services"
```

## Task 5: Build Guild Mission Page, Routes, and Manager Actions

**Files:**
- Create: `guilds/views/missions.py`
- Create: `guilds/templates/guilds/missions.html`
- Modify: `guilds/urls.py`
- Modify: `guilds/templates/guilds/detail.html`
- Test: `tests/test_guild_mission_views.py`

- [ ] **Step 1: Write the failing view tests**

```python
@pytest.mark.django_db
def test_guild_mission_page_lists_templates_for_members(guild_member_client):
    client, user, guild = guild_member_client
    GuildMissionTemplate.objects.create(
        key="guild_view_task",
        name="视图任务",
        description="",
        difficulty="junior",
        task_type="dispatch",
        base_duration_seconds=600,
        ruby_reward=2,
        recommended_guest_count=2,
        allow_troops=False,
        is_active=True,
        sort_weight=1,
    )

    response = client.get(reverse("guilds:missions"))

    assert response.status_code == 200
    assert "视图任务" in response.content.decode("utf-8")


@pytest.mark.django_db
def test_non_manager_cannot_launch_guild_mission(member_client, guild_with_template):
    response = member_client.post(reverse("guilds:mission_launch"), {"template_key": "guild_view_task"}, follow=True)
    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert "只有管理员/帮主可以发起帮会任务" in messages[-1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest "/home/daniel/code/web_game_v5/tests/test_guild_mission_views.py" -q`
Expected: FAIL with missing route/view/template errors.

- [ ] **Step 3: Add the page view and POST handlers**

```python
@login_required
@require_guild_member
def mission_page(request: Any) -> HttpResponse:
    member = request.guild_member
    context = guild_mission_service.get_guild_mission_page_context(member)
    return render(request, "guilds/missions.html", context)


@login_required
@require_guild_manager
@require_POST
def launch_mission(request: Any) -> HttpResponse:
    member = request.guild_member
    template_key = str(request.POST.get("template_key", "")).strip()
    pool_entry_ids = [int(value) for value in request.POST.getlist("pool_entry_ids") if value]
    troop_loadout = guild_mission_service.parse_troop_loadout_from_post(request.POST)
    execute_guild_action(
        request,
        action=lambda: guild_mission_service.launch_guild_mission(
            guild=member.guild,
            operator=request.user,
            template_key=template_key,
            pool_entry_ids=pool_entry_ids,
            troop_loadout=troop_loadout,
        ),
        success_message="帮会任务已出征",
    )
    return redirect("guilds:missions")


@login_required
@require_guild_manager
@require_POST
def retreat_mission(request: Any) -> HttpResponse:
    member = request.guild_member
    run_id = safe_int(request.POST.get("run_id"), default=None, min_val=1)
    if run_id is None:
        messages.error(request, "参数错误")
        return redirect("guilds:missions")
    run = get_object_or_404(GuildMissionRun, pk=run_id, guild=member.guild)
    execute_guild_action(
        request,
        action=lambda: guild_mission_service.request_retreat(run=run, operator=request.user),
        success_message="帮会任务已撤回",
    )
    return redirect("guilds:missions")
```

- [ ] **Step 4: Add the mission page template and detail page entry point**

```html
<section class="tw-card">
  <h2 class="m-0">当前帮会出征</h2>
  {% if active_run %}
    <div class="tw-guild-mission-active">
      <h3>{{ active_run.template.name }}</h3>
      <p class="tw-muted">剩余时间：<span class="countdown" data-countdown="{{ active_run.return_at|date:'c' }}" data-refresh="1">计算中</span></p>
      {% if member.can_manage %}
      <form method="post" action="{% url 'guilds:mission_retreat' %}">
        {% csrf_token %}
        <input type="hidden" name="run_id" value="{{ active_run.id }}">
        <button type="submit" class="tw-btn-secondary">撤回</button>
      </form>
      {% endif %}
    </div>
  {% else %}
    <p class="tw-muted">暂无帮会出征任务</p>
  {% endif %}
</section>

<section class="tw-card tw-guild-section-spacing">
  <h2 class="m-0">帮会任务列表</h2>
  <table class="tw-mission-table">
    <thead><tr><th>任务名称</th><th>类型</th><th>红宝石</th><th>操作</th></tr></thead>
    <tbody>
    {% for mission in mission_templates %}
      <tr>
        <td>{{ mission.name }}</td>
        <td>{% if mission.allow_troops %}门客+护院{% else %}仅门客{% endif %}</td>
        <td>{{ mission.ruby_reward }}</td>
        <td><button type="button" class="tw-btn-primary tw-btn-sm" {% if active_run or not member.can_manage %}disabled{% endif %}>选择出征</button></td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
</section>
```

- [ ] **Step 5: Run the view tests again**

Run: `pytest "/home/daniel/code/web_game_v5/tests/test_guild_mission_views.py" -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add guilds/views/missions.py guilds/templates/guilds/missions.html guilds/urls.py guilds/templates/guilds/detail.html tests/test_guild_mission_views.py
git commit -m "feat: add guild mission page and actions"
```

## Task 6: Show Active Guild Missions on the Home Event Board

**Files:**
- Modify: `gameplay/selectors/home.py`
- Modify: `templates/landing.html`
- Test: `tests/test_guild_home_mission_events.py`

- [ ] **Step 1: Write the failing homepage event tests**

```python
@pytest.mark.django_db
def test_home_page_shows_active_guild_mission_event(client, django_user_model):
    user = django_user_model.objects.create_user(username="guild_home_event_user", password="pass12345")
    manor = ensure_manor(user)
    guild = Guild.objects.create(name="首页帮会事件帮", founder=user, is_active=True)
    GuildMember.objects.create(guild=guild, user=user, position="leader")
    template = GuildMissionTemplate.objects.create(
        key="guild_home_event_task",
        name="首页巡防",
        description="",
        difficulty="junior",
        task_type="dispatch",
        base_duration_seconds=600,
        ruby_reward=2,
        recommended_guest_count=2,
        allow_troops=False,
        is_active=True,
        sort_weight=1,
    )
    GuildMissionRun.objects.create(
        guild=guild,
        template=template,
        status="active",
        selected_guest_count=2,
        ruby_reward=2,
        return_at=timezone.now() + timedelta(minutes=5),
    )
    client.login(username="guild_home_event_user", password="pass12345")

    response = client.get("/")

    body = response.content.decode("utf-8")
    assert "帮会出征：首页巡防" in body
    assert reverse("guilds:missions") in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest "/home/daniel/code/web_game_v5/tests/test_guild_home_mission_events.py" -q`
Expected: FAIL because the home selector does not yet inject guild mission events.

- [ ] **Step 3: Add guild mission context to the home selector**

```python
active_guild_mission = None
if hasattr(manor.user, "guild_membership") and manor.user.guild_membership.is_active:
    active_guild_mission = (
        GuildMissionRun.objects.select_related("template")
        .filter(guild=manor.user.guild_membership.guild, status=GuildMissionRun.Status.ACTIVE, return_at__isnull=False)
        .order_by("-started_at")
        .first()
    )

return {
    "manor": manor,
    "resources": resources,
    "resource_labels": resource_labels,
    "guests": guests,
    "guest_count": len(guests),
    "active_runs": runs,
    "upgrading_buildings": upgrading_buildings,
    "upgrading_technologies": upgrading_techs,
    "total_guest_salary": total_guest_salary,
    "building_income": building_income,
    "grain_production": hourly_rates.get("grain", 0),
    "personnel_grain_cost": get_personnel_grain_cost_per_hour(manor),
    "player_troops": player_troops,
    "active_scouts": get_active_scouts(manor),
    "active_raids": get_active_raids(manor),
    "incoming_raids": get_incoming_raids(manor),
    "active_guild_mission": active_guild_mission,
}
```

- [ ] **Step 4: Render the extra event row in the landing page**

```html
{% if active_guild_mission %}
<tr>
  <td>
    <strong>帮会出征：{{ active_guild_mission.template.name }}</strong>
  </td>
  <td>
    <span class="countdown"
          data-countdown="{{ active_guild_mission.return_at|date:'c' }}"
          data-format="zh"
          data-refresh="1"
          title="预计完成 {{ active_guild_mission.return_at|date:'Y-m-d H:i:s' }}">计算中</span>
  </td>
  <td>
    <a href="{% url 'guilds:missions' %}" class="btn-secondary btn-sm">查看</a>
  </td>
</tr>
{% endif %}
```

- [ ] **Step 5: Run the homepage test again**

Run: `pytest "/home/daniel/code/web_game_v5/tests/test_guild_home_mission_events.py" -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add gameplay/selectors/home.py templates/landing.html tests/test_guild_home_mission_events.py
git commit -m "feat: show guild mission events on home page"
```

## Task 7: Regression Pass for Warehouse, Technology, and Mission Flows

**Files:**
- Modify: `tests/test_guilds_technology_service.py`
- Modify: `tests/test_guilds_tasks.py`
- Modify: `tests/test_guild_hero_pool_views.py`
- Modify: `tests/gameplay/mission_flow.py`

- [ ] **Step 1: Add the final regression tests**

```python
@pytest.mark.django_db(transaction=True)
def test_complete_guild_mission_awards_red_ruby_and_returns_survivors(django_user_model, monkeypatch):
    leader, leader_manor = _create_user_with_manor(django_user_model, "guild_mission_finalize_leader")
    guild = Guild.objects.create(name="帮会任务结算帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    template = GuildMissionTemplate.objects.create(
        key="guild_finalize_task",
        name="结算测试任务",
        description="",
        difficulty="intermediate",
        task_type="dispatch",
        base_duration_seconds=600,
        ruby_reward=5,
        recommended_guest_count=2,
        allow_troops=True,
        is_active=True,
        sort_weight=3,
    )
    troop_template = TroopTemplate.objects.create(key="guild_finalize_archer", name="结算弓手", description="", base_attack=1, base_defense=1, base_hp=1, speed_bonus=0, priority=1, default_count=0)
    storage = GuildTroopStorage.objects.create(guild=guild, troop_template=troop_template, count=50)
    guest_a = _create_guest(manor=leader_manor, template=_create_template("guild_finalize_tpl_a"), name="结算甲")
    guest_b = _create_guest(manor=leader_manor, template=_create_template("guild_finalize_tpl_b"), name="结算乙")
    entry_a = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest_a.id, slot_index=1).entry
    entry_b = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest_b.id, slot_index=2).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry_a.id)
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry_b.id)
    run = guild_mission_service.launch_guild_mission(
        guild=guild,
        operator=leader,
        template_key=template.key,
        pool_entry_ids=[entry_a.id, entry_b.id],
        troop_loadout={troop_template.key: 20},
    )
    monkeypatch.setattr(
        "guilds.services.guild_missions.execute_battle",
        lambda *args, **kwargs: SimpleNamespace(
            winner="attacker",
            losses={"attacker": {"casualties": [{"key": troop_template.key, "lost": 8}]}}
        ),
    )

    complete_guild_mission_task.run(run.id)
    run.refresh_from_db()
    ruby = GuildWarehouse.objects.get(guild=guild, item_key="red_ruby")
    storage.refresh_from_db()
    assert run.status == GuildMissionRun.Status.COMPLETED
    assert ruby.quantity == run.ruby_reward
    assert storage.count == 42


@pytest.mark.django_db
def test_guild_mission_page_uses_manager_only_retreat_button(client, django_user_model):
    leader = django_user_model.objects.create_user(username="guild_mission_retreat_button_leader", password="pass12345")
    member_user = django_user_model.objects.create_user(username="guild_mission_retreat_button_member", password="pass12345")
    ensure_manor(leader)
    ensure_manor(member_user)
    guild = Guild.objects.create(name="撤回按钮帮", founder=leader, is_active=True)
    GuildMember.objects.create(guild=guild, user=leader, position="leader")
    GuildMember.objects.create(guild=guild, user=member_user, position="member")
    template = GuildMissionTemplate.objects.create(
        key="guild_retreat_button_task",
        name="按钮任务",
        description="",
        difficulty="junior",
        task_type="dispatch",
        base_duration_seconds=600,
        ruby_reward=2,
        recommended_guest_count=1,
        allow_troops=False,
        is_active=True,
        sort_weight=4,
    )
    GuildMissionRun.objects.create(guild=guild, template=template, status="active", selected_guest_count=1, ruby_reward=2, return_at=timezone.now() + timedelta(minutes=5))

    client.login(username="guild_mission_retreat_button_member", password="pass12345")
    response = client.get(reverse("guilds:missions"))

    body = response.content.decode("utf-8")
    assert "撤回" not in body
```

- [ ] **Step 2: Run the focused regression suite and verify at least one red/green cycle**

Run: `pytest "/home/daniel/code/web_game_v5/tests/test_guild_troop_donation.py" "/home/daniel/code/web_game_v5/tests/test_guild_mission_service.py" "/home/daniel/code/web_game_v5/tests/test_guild_mission_views.py" "/home/daniel/code/web_game_v5/tests/test_guild_home_mission_events.py" "/home/daniel/code/web_game_v5/tests/test_guilds_tasks.py" "/home/daniel/code/web_game_v5/tests/test_guilds_technology_service.py" "/home/daniel/code/web_game_v5/tests/test_guild_hero_pool.py" -q`
Expected: FAIL first if any launch/finalize/view wiring is still missing.

- [ ] **Step 3: Fill the remaining integration wiring**

```python
def get_guild_mission_page_context(member: GuildMember) -> dict[str, Any]:
    guild = member.guild
    active_run = GuildMissionRun.objects.select_related("template", "started_by__manor").filter(
        guild=guild,
        status=GuildMissionRun.Status.ACTIVE,
    ).first()
    mission_templates = list(GuildMissionTemplate.objects.filter(is_active=True).order_by("difficulty", "sort_weight", "id"))
    lineup_entries = list(
        GuildBattleLineupEntry.objects.filter(guild=guild)
        .select_related("pool_entry__source_guest__template", "pool_entry__owner_member__user__manor")
        .order_by("slot_index")
    )
    troop_storages = list(GuildTroopStorage.objects.filter(guild=guild, count__gt=0).select_related("troop_template"))
    return {
        "guild": guild,
        "member": member,
        "active_run": active_run,
        "mission_templates": mission_templates,
        "lineup_entries": lineup_entries,
        "dispatch_limit": technology_service.get_guild_dispatch_capacity(guild),
        "lineup_limit": technology_service.get_guild_lineup_capacity(guild),
        "troop_storages": troop_storages,
    }
```

- [ ] **Step 4: Run the focused regression suite again**

Run: `pytest "/home/daniel/code/web_game_v5/tests/test_guild_troop_donation.py" "/home/daniel/code/web_game_v5/tests/test_guild_mission_service.py" "/home/daniel/code/web_game_v5/tests/test_guild_mission_views.py" "/home/daniel/code/web_game_v5/tests/test_guild_home_mission_events.py" "/home/daniel/code/web_game_v5/tests/test_guilds_tasks.py" "/home/daniel/code/web_game_v5/tests/test_guilds_technology_service.py" "/home/daniel/code/web_game_v5/tests/test_guild_hero_pool.py" -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_guild_troop_donation.py tests/test_guild_mission_service.py tests/test_guild_mission_views.py tests/test_guild_home_mission_events.py tests/test_guilds_tasks.py tests/test_guilds_technology_service.py tests/test_guild_hero_pool.py tests/test_guild_hero_pool_views.py tests/gameplay/mission_flow.py
git commit -m "feat: complete guild mission gameplay loop"
```
