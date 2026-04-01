# Guild Mission Battle Limit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make guild mission battle resolution honor the guild mission's actual attacker and defender guest counts instead of silently falling back to the global 5-guest battle default.

**Architecture:** Keep the existing guild mission battle path on `guilds/services/guild_missions.py` and patch it locally by computing explicit `limit` and `defender_limit` values for `BattleOptions`. Add focused regression tests in `tests/test_guild_mission_service.py` that prove both attacker and defender counts above 5 are forwarded into battle resolution without changing the global battle defaults.

**Tech Stack:** Django, pytest, guild mission service layer, battle execution `BattleOptions`

---

## File Structure

- Modify: `/home/daniel/code/web_game_v5/guilds/services/guild_missions.py`
  Responsibility: compute guild-mission-specific battle squad limits and pass them into `BattleOptions` during mission finalization.
- Modify: `/home/daniel/code/web_game_v5/tests/test_guild_mission_service.py`
  Responsibility: cover the new `>5` attacker and defender battle-limit behavior plus preserve existing guild mission completion expectations.

Note: this plan intentionally does not include `git commit` steps because the current workspace instruction says not to plan or execute commits unless the user explicitly asks for them.

### Task 1: Add a Failing Regression Test for Expanded Guild Mission Battle Limits

**Files:**
- Modify: `/home/daniel/code/web_game_v5/tests/test_guild_mission_service.py`
- Verify against: `/home/daniel/code/web_game_v5/guilds/services/guild_missions.py`

- [ ] **Step 1: Write the failing test for attacker and defender counts above 5**

Add a new test near the existing guild mission finalization coverage that:

```python
@pytest.mark.django_db(transaction=True)
def test_finalize_guild_mission_passes_expanded_battle_limits(monkeypatch, django_user_model):
    ...
    run = GuildMissionRun.objects.create(
        ...,
        selected_guest_count=6,
        guest_ids=[...6 ids...],
        guest_snapshots=[...6 snapshots...],
    )

    captured = {}

    def _fake_execute_battle(_owner, guests, active_guests, options):
        captured["guest_count"] = len(guests)
        captured["active_guest_count"] = len(active_guests)
        captured["limit"] = options.limit
        captured["defender_limit"] = options.defender_limit
        captured["enemy_guest_count"] = len(options.defender_setup["guests"])
        return report
```

Assertions should prove:

- `options.limit == 6`
- `options.defender_limit == 7` for a template with 7 configured enemy guests
- `len(options.defender_setup["guests"]) == 7`

- [ ] **Step 2: Run the new test and verify it fails for the right reason**

Run:

```bash
pytest "tests/test_guild_mission_service.py" -q -k "expanded_battle_limits"
```

Expected before implementation:

- FAIL because `options.limit` and `options.defender_limit` still resolve to the default value `5`

### Task 2: Implement Guild-Mission-Specific Battle Limit Resolution

**Files:**
- Modify: `/home/daniel/code/web_game_v5/guilds/services/guild_missions.py`
- Re-read: `/home/daniel/code/web_game_v5/battle/execution.py`

- [ ] **Step 1: Add small helpers to compute attacker and defender limits**

In `guilds/services/guild_missions.py`, add local helpers above `finalize_guild_mission_run()` with logic equivalent to:

```python
def _resolve_guild_mission_attacker_limit(run: GuildMissionRun) -> int:
    candidate = int(getattr(run, "selected_guest_count", 0) or len(getattr(run, "guest_snapshots", []) or []))
    return max(1, candidate)


def _resolve_guild_mission_defender_limit(run: GuildMissionRun, *, attacker_limit: int) -> int:
    enemy_guests = getattr(run.template, "enemy_guests", None) or []
    if isinstance(enemy_guests, list) and enemy_guests:
        return max(1, len(enemy_guests))
    return max(1, attacker_limit)
```

Keep the helpers narrow and guild-mission-specific; do not modify global battle config.

- [ ] **Step 2: Pass the resolved limits into `BattleOptions`**

Update `finalize_guild_mission_run()` so it computes:

```python
attacker_limit = _resolve_guild_mission_attacker_limit(locked_run)
defender_limit = _resolve_guild_mission_defender_limit(locked_run, attacker_limit=attacker_limit)
```

and then passes them into `BattleOptions(...)`:

```python
BattleOptions(
    ...,
    defender_limit=defender_limit,
    limit=attacker_limit,
    ...
)
```

No other guild mission logic should change in this task.

- [ ] **Step 3: Run the focused regression test and verify it passes**

Run:

```bash
pytest "tests/test_guild_mission_service.py" -q -k "expanded_battle_limits"
```

Expected after implementation:

- PASS

### Task 3: Regression Coverage for Existing Guild Mission Finalization Paths

**Files:**
- Modify: `/home/daniel/code/web_game_v5/tests/test_guild_mission_service.py`

- [ ] **Step 1: Keep existing completion behavior covered**

Check the existing completion test still asserts:

```python
assert run.status == "completed"
assert storage.count == 42
assert guild.warehouse_items.get(item_key="red_ruby").quantity == 5
```

Only adjust helper setup if the new limit helpers require minimal fixture cleanup.

- [ ] **Step 2: Run the guild mission service test file**

Run:

```bash
pytest "tests/test_guild_mission_service.py" -q
```

Expected:

- All guild mission service tests PASS

### Task 4: Broader Guild Mission Verification

**Files:**
- Verify: `/home/daniel/code/web_game_v5/tests/test_guilds_tasks.py`
- Verify: `/home/daniel/code/web_game_v5/tests/test_guild_mission_views.py`
- Verify: `/home/daniel/code/web_game_v5/tests/test_guild_home_mission_events.py`

- [ ] **Step 1: Run the broader guild mission regression suite**

Run:

```bash
pytest \
  "tests/test_guild_mission_service.py" \
  "tests/test_guilds_tasks.py" \
  "tests/test_guild_mission_views.py" \
  "tests/test_guild_home_mission_events.py" \
  -q
```

Expected:

- PASS with no guild mission regressions

- [ ] **Step 2: Record the behavioral outcome for the next stage**

Confirm in the implementation notes that:

- guild mission battles now support attacker counts above 5 when snapshots contain more than 5 guests
- guild mission enemy guest configs above 5 now fully participate
- global `MAX_SQUAD` remains unchanged

## Self-Review

- Spec coverage: the plan covers the exact scope from the design doc: local guild mission override only, no global battle default change, explicit attacker/defender limits, and focused regression tests.
- Placeholder scan: no `TODO`/`TBD` placeholders remain.
- Type consistency: all referenced functions and fields (`selected_guest_count`, `guest_snapshots`, `enemy_guests`, `BattleOptions.limit`, `BattleOptions.defender_limit`) match the existing codebase.
