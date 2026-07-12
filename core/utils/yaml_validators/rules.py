"""Validators for arena, coop arena, trade, warehouse, auction, guild, and recruitment YAML configs."""

from __future__ import annotations

from .base import ValidationResult, _check_positive, _check_required_fields, _check_type, _check_unique_keys

# ---------------------------------------------------------------------------
# Schema: arena_rules.yaml
# ---------------------------------------------------------------------------


def validate_arena_rules(data: dict, *, file: str = "arena_rules.yaml") -> ValidationResult:
    result = ValidationResult()

    if not isinstance(data, dict):
        result.add(file, "<root>", "expected a mapping at root level")
        return result

    for section in ("registration", "runtime", "rewards"):
        section_data = data.get(section)
        if section_data is None:
            result.add(file, "<root>", f"missing required section '{section}'")
        elif not isinstance(section_data, dict):
            result.add(file, section, "expected a mapping")

    reg = data.get("registration")
    if isinstance(reg, dict):
        for int_field in (
            "max_guests_per_entry",
            "registration_silver_cost",
            "daily_participation_limit",
            "tournament_player_limit",
        ):
            val = reg.get(int_field)
            if val is not None:
                _check_type(val, int, result=result, file=file, path="registration", field_name=int_field)
                _check_positive(
                    val, result=result, file=file, path="registration", field_name=int_field, allow_zero=False
                )

    runtime = data.get("runtime")
    if isinstance(runtime, dict):
        virtual_fill_wait = runtime.get("virtual_fill_wait_seconds")
        if virtual_fill_wait is not None:
            _check_type(
                virtual_fill_wait,
                int,
                result=result,
                file=file,
                path="runtime",
                field_name="virtual_fill_wait_seconds",
            )
            _check_positive(
                virtual_fill_wait,
                result=result,
                file=file,
                path="runtime",
                field_name="virtual_fill_wait_seconds",
                allow_zero=False,
            )

    rewards = data.get("rewards")
    if isinstance(rewards, dict):
        base_coins = rewards.get("base_participation_coins")
        if base_coins is not None:
            _check_type(
                base_coins, int, result=result, file=file, path="rewards", field_name="base_participation_coins"
            )
            _check_positive(base_coins, result=result, file=file, path="rewards", field_name="base_participation_coins")

        rank_bonus = rewards.get("rank_bonus_coins")
        if rank_bonus is not None:
            if not isinstance(rank_bonus, dict):
                result.add(file, "rewards.rank_bonus_coins", "expected a mapping")

    return result


def validate_arena_coop_rules(data: dict, *, file: str = "arena_coop_rules.yaml") -> ValidationResult:
    result = ValidationResult()

    if not isinstance(data, dict):
        result.add(file, "<root>", "expected a mapping at root level")
        return result

    for section in ("registration", "runtime", "contribution", "rewards", "rare_drop", "enemy"):
        section_data = data.get(section)
        if section_data is None:
            result.add(file, "<root>", f"missing required section '{section}'")
        elif not isinstance(section_data, dict):
            result.add(file, section, "expected a mapping")

    registration = data.get("registration")
    if isinstance(registration, dict):
        for field_name in (
            "player_limit",
            "guest_limit_per_entry",
            "daily_participation_limit",
            "prepare_duration_seconds",
            "registration_silver_cost",
            "recruiting_lock_timeout",
        ):
            value = registration.get(field_name)
            if value is None:
                continue
            _check_type(value, int, result=result, file=file, path="registration", field_name=field_name)
            _check_positive(
                value,
                result=result,
                file=file,
                path="registration",
                field_name=field_name,
                allow_zero=field_name == "registration_silver_cost",
            )
        recruiting_lock_key = registration.get("recruiting_lock_key")
        if recruiting_lock_key is not None:
            _check_type(
                recruiting_lock_key,
                str,
                result=result,
                file=file,
                path="registration",
                field_name="recruiting_lock_key",
            )

    runtime = data.get("runtime")
    if isinstance(runtime, dict):
        for field_name in ("auto_start_scan_seconds", "virtual_fill_wait_seconds", "completed_retention_seconds"):
            value = runtime.get(field_name)
            if value is None:
                continue
            _check_type(value, int, result=result, file=file, path="runtime", field_name=field_name)
            _check_positive(
                value,
                result=result,
                file=file,
                path="runtime",
                field_name=field_name,
                allow_zero=field_name == "completed_retention_seconds",
            )

    contribution = data.get("contribution")
    if isinstance(contribution, dict):
        minimum_share_bps = contribution.get("minimum_share_bps")
        if minimum_share_bps is not None:
            _check_type(
                minimum_share_bps,
                int,
                result=result,
                file=file,
                path="contribution",
                field_name="minimum_share_bps",
            )
            _check_positive(
                minimum_share_bps,
                result=result,
                file=file,
                path="contribution",
                field_name="minimum_share_bps",
            )

    rewards = data.get("rewards")
    if isinstance(rewards, dict):
        for field_name in ("participation_coins", "clear_coins"):
            value = rewards.get(field_name)
            if value is None:
                continue
            _check_type(value, int, result=result, file=file, path="rewards", field_name=field_name)
            _check_positive(value, result=result, file=file, path="rewards", field_name=field_name)

        damage_tiers = rewards.get("damage_tiers")
        if damage_tiers is not None:
            if not isinstance(damage_tiers, list):
                result.add(file, "rewards.damage_tiers", "expected a list")
            else:
                for idx, row in enumerate(damage_tiers):
                    row_path = f"rewards.damage_tiers[{idx}]"
                    if not isinstance(row, dict):
                        result.add(file, row_path, "expected a mapping")
                        continue
                    _check_required_fields(
                        row,
                        ["min_share_bps", "coins"],
                        result=result,
                        file=file,
                        path=row_path,
                    )
                    for field_name in ("min_share_bps", "coins"):
                        value = row.get(field_name)
                        if value is None:
                            continue
                        _check_type(value, int, result=result, file=file, path=row_path, field_name=field_name)
                        _check_positive(value, result=result, file=file, path=row_path, field_name=field_name)

        rank_rewards = rewards.get("rank_rewards")
        if rank_rewards is not None and not isinstance(rank_rewards, dict):
            result.add(file, "rewards.rank_rewards", "expected a mapping")

    rare_drop = data.get("rare_drop")
    if isinstance(rare_drop, dict):
        for field_name in ("enabled", "requires_clear", "requires_minimum_contribution"):
            value = rare_drop.get(field_name)
            if value is None:
                continue
            _check_type(value, bool, result=result, file=file, path="rare_drop", field_name=field_name)
        item_key = rare_drop.get("item_key")
        if item_key is not None:
            _check_type(item_key, str, result=result, file=file, path="rare_drop", field_name="item_key")
        chance_bps = rare_drop.get("chance_bps")
        if chance_bps is not None:
            _check_type(chance_bps, int, result=result, file=file, path="rare_drop", field_name="chance_bps")
            _check_positive(chance_bps, result=result, file=file, path="rare_drop", field_name="chance_bps")

    enemy = data.get("enemy")
    if isinstance(enemy, dict):
        boss = enemy.get("boss")
        if boss is not None:
            if not isinstance(boss, dict):
                result.add(file, "enemy.boss", "expected a mapping")
            else:
                _check_required_fields(
                    boss,
                    ["template_key", "display_name"],
                    result=result,
                    file=file,
                    path="enemy.boss",
                )
        guards = enemy.get("guards")
        if guards is not None:
            if not isinstance(guards, list):
                result.add(file, "enemy.guards", "expected a list")
            else:
                for idx, guard in enumerate(guards):
                    guard_path = f"enemy.guards[{idx}]"
                    if not isinstance(guard, dict):
                        result.add(file, guard_path, "expected a mapping")
                        continue
                    _check_required_fields(
                        guard,
                        ["template_key", "display_name"],
                        result=result,
                        file=file,
                        path=guard_path,
                    )

    return result


# ---------------------------------------------------------------------------
# Schema: arena_rewards.yaml
# ---------------------------------------------------------------------------


def validate_arena_rewards(
    data: dict,
    *,
    file: str = "arena_rewards.yaml",
    item_keys: set[str] | None = None,
) -> ValidationResult:
    result = ValidationResult()

    if not isinstance(data, dict):
        result.add(file, "<root>", "expected a mapping at root level")
        return result

    rewards = data.get("rewards")
    if rewards is None:
        result.add(file, "<root>", "missing required key 'rewards'")
        return result

    if not isinstance(rewards, list):
        result.add(file, "rewards", "expected a list")
        return result

    _check_unique_keys(rewards, "key", result=result, file=file, context="rewards")

    for idx, entry in enumerate(rewards):
        path = f"rewards[{idx}]"
        if not isinstance(entry, dict):
            result.add(file, path, "expected a mapping")
            continue

        _check_required_fields(entry, ["key", "name", "cost_coins"], result=result, file=file, path=path)

        cost_coins = entry.get("cost_coins")
        if cost_coins is not None:
            _check_type(cost_coins, int, result=result, file=file, path=path, field_name="cost_coins")
            _check_positive(cost_coins, result=result, file=file, path=path, field_name="cost_coins", allow_zero=False)

        daily_limit = entry.get("daily_limit")
        if daily_limit is not None:
            _check_type(daily_limit, int, result=result, file=file, path=path, field_name="daily_limit")
            _check_positive(
                daily_limit, result=result, file=file, path=path, field_name="daily_limit", allow_zero=False
            )

        reward_data = entry.get("rewards")
        if reward_data is not None and not isinstance(reward_data, dict):
            result.add(file, path, "field 'rewards' expected a mapping")

        # Validate random_items referential integrity
        if isinstance(reward_data, dict) and item_keys is not None:
            random_items = reward_data.get("random_items")
            if random_items is not None:
                if not isinstance(random_items, list):
                    result.add(file, f"{path}.rewards", "field 'random_items' expected a list")
                else:
                    for ri, rand_item in enumerate(random_items):
                        ri_path = f"{path}.rewards.random_items[{ri}]"
                        if not isinstance(rand_item, dict):
                            result.add(file, ri_path, "expected a mapping")
                            continue
                        rand_item_key = rand_item.get("item_key")
                        if rand_item_key is not None and rand_item_key not in item_keys:
                            result.add(file, ri_path, f"item_key '{rand_item_key}' not found in item_templates.yaml")
                        weight = rand_item.get("weight")
                        if weight is not None:
                            _check_type(weight, int, result=result, file=file, path=ri_path, field_name="weight")
                            _check_positive(
                                weight, result=result, file=file, path=ri_path, field_name="weight", allow_zero=False
                            )

        rotating_pool = entry.get("rotating_blueprint_pool")
        if rotating_pool is not None:
            pool_path = f"{path}.rotating_blueprint_pool"
            if not isinstance(rotating_pool, dict):
                result.add(file, pool_path, "expected a mapping")
                continue

            rarity = rotating_pool.get("rarity")
            if not isinstance(rarity, str) or not rarity.strip():
                result.add(file, pool_path, "field 'rarity' expected a non-empty string")

            blueprint_keys = rotating_pool.get("blueprint_keys")
            if not isinstance(blueprint_keys, list):
                result.add(file, pool_path, "field 'blueprint_keys' expected a list")
                continue
            if not all(isinstance(blueprint_key, str) for blueprint_key in blueprint_keys):
                result.add(file, pool_path, "field 'blueprint_keys' expected a list of strings")
            elif len(blueprint_keys) != 4 or len(set(blueprint_keys)) != len(blueprint_keys):
                result.add(file, pool_path, "field 'blueprint_keys' must contain exactly four unique entries")

            for blueprint_index, blueprint_key in enumerate(blueprint_keys):
                blueprint_path = f"{pool_path}.blueprint_keys[{blueprint_index}]"
                if not isinstance(blueprint_key, str) or not blueprint_key.startswith("blueprint_"):
                    result.add(file, blueprint_path, "expected a blueprint_* item key")
                    continue
                if item_keys is not None and blueprint_key not in item_keys:
                    result.add(file, blueprint_path, f"item_key '{blueprint_key}' not found in item_templates.yaml")

    return result


# ---------------------------------------------------------------------------
# Schema: trade_market_rules.yaml
# ---------------------------------------------------------------------------


def validate_trade_market_rules(data: dict, *, file: str = "trade_market_rules.yaml") -> ValidationResult:
    result = ValidationResult()

    if not isinstance(data, dict):
        result.add(file, "<root>", "expected a mapping at root level")
        return result

    listing_fees = data.get("listing_fees")
    if listing_fees is None:
        result.add(file, "<root>", "missing required key 'listing_fees'")
        return result

    if not isinstance(listing_fees, dict):
        result.add(file, "listing_fees", "expected a mapping")
        return result

    for duration, fee in listing_fees.items():
        path = f"listing_fees.{duration}"
        if not isinstance(fee, (int, float)):
            result.add(file, path, f"expected a number, got {type(fee).__name__}")
        elif fee < 0:
            result.add(file, path, f"fee must be >= 0, got {fee}")

    return result


# ---------------------------------------------------------------------------
# Schema: warehouse_production.yaml
# ---------------------------------------------------------------------------

VALID_WAREHOUSE_TECH_KEYS = {"equipment", "experience", "guard", "resource"}


def validate_warehouse_production(data: dict, *, file: str = "warehouse_production.yaml") -> ValidationResult:
    result = ValidationResult()

    if not isinstance(data, dict):
        result.add(file, "<root>", "expected a mapping at root level")
        return result

    for tech_key, tech_data in data.items():
        path = tech_key
        if tech_key not in VALID_WAREHOUSE_TECH_KEYS:
            result.add(file, path, f"unknown tech section '{tech_key}'")

        if not isinstance(tech_data, dict):
            result.add(file, path, "expected a mapping")
            continue

        levels = tech_data.get("levels")
        if levels is None:
            result.add(file, path, "missing required key 'levels'")
            continue

        if not isinstance(levels, dict):
            result.add(file, f"{path}.levels", "expected a mapping")
            continue

        for level_key, items in levels.items():
            level_path = f"{path}.levels.{level_key}"
            if not isinstance(items, list):
                result.add(file, level_path, "expected a list of items")
                continue

            for idx, item in enumerate(items):
                item_path = f"{level_path}[{idx}]"
                if not isinstance(item, dict):
                    result.add(file, item_path, "expected a mapping")
                    continue
                _check_required_fields(
                    item,
                    ["item_key", "quantity", "contribution_cost"],
                    result=result,
                    file=file,
                    path=item_path,
                )
                quantity = item.get("quantity")
                if quantity is not None:
                    _check_type(quantity, int, result=result, file=file, path=item_path, field_name="quantity")
                    _check_positive(
                        quantity, result=result, file=file, path=item_path, field_name="quantity", allow_zero=False
                    )
                contribution_cost = item.get("contribution_cost")
                if contribution_cost is not None:
                    _check_type(
                        contribution_cost, int, result=result, file=file, path=item_path, field_name="contribution_cost"
                    )
                    _check_positive(
                        contribution_cost,
                        result=result,
                        file=file,
                        path=item_path,
                        field_name="contribution_cost",
                        allow_zero=False,
                    )
                weekly_personal_limit = item.get("weekly_personal_limit")
                if weekly_personal_limit is not None:
                    _check_type(
                        weekly_personal_limit,
                        int,
                        result=result,
                        file=file,
                        path=item_path,
                        field_name="weekly_personal_limit",
                    )
                    _check_positive(
                        weekly_personal_limit,
                        result=result,
                        file=file,
                        path=item_path,
                        field_name="weekly_personal_limit",
                    )

    return result


# ---------------------------------------------------------------------------
# Schema: auction_items.yaml
# ---------------------------------------------------------------------------


def validate_auction_items(
    data: dict,
    *,
    file: str = "auction_items.yaml",
    item_keys: set[str] | None = None,
) -> ValidationResult:
    result = ValidationResult()

    if not isinstance(data, dict):
        result.add(file, "<root>", "expected a mapping at root level")
        return result

    settings_data = data.get("settings")
    if settings_data is not None:
        if not isinstance(settings_data, dict):
            result.add(file, "settings", "expected a mapping")
        else:
            cycle_days = settings_data.get("cycle_days")
            if cycle_days is not None:
                _check_type(cycle_days, int, result=result, file=file, path="settings", field_name="cycle_days")
                _check_positive(
                    cycle_days, result=result, file=file, path="settings", field_name="cycle_days", allow_zero=False
                )

    items = data.get("items")
    if items is None:
        result.add(file, "<root>", "missing required key 'items'")
        return result

    if not isinstance(items, list):
        result.add(file, "items", "expected a list")
        return result

    _check_unique_keys(items, "item_key", result=result, file=file, context="items")

    for idx, entry in enumerate(items):
        path = f"items[{idx}]"
        if not isinstance(entry, dict):
            result.add(file, path, "expected a mapping")
            continue

        _check_required_fields(
            entry, ["item_key", "slots", "quantity_per_slot", "starting_price"], result=result, file=file, path=path
        )

        item_key = entry.get("item_key")
        if item_key is not None and item_keys is not None:
            if item_key not in item_keys:
                result.add(file, path, f"item_key '{item_key}' not found in item_templates.yaml")

        for pos_int_field in ("slots", "quantity_per_slot", "starting_price", "min_increment"):
            val = entry.get(pos_int_field)
            if val is not None:
                _check_type(val, int, result=result, file=file, path=path, field_name=pos_int_field)
                _check_positive(val, result=result, file=file, path=path, field_name=pos_int_field, allow_zero=False)

        enabled = entry.get("enabled")
        if enabled is not None:
            _check_type(enabled, bool, result=result, file=file, path=path, field_name="enabled")

    return result


# ---------------------------------------------------------------------------
# Schema: guild_rules.yaml
# ---------------------------------------------------------------------------


def validate_guild_rules(data: dict, *, file: str = "guild_rules.yaml") -> ValidationResult:
    result = ValidationResult()

    if not isinstance(data, dict):
        result.add(file, "<root>", "expected a mapping at root level")
        return result

    for section in ("pagination", "creation", "contribution"):
        section_data = data.get(section)
        if section_data is None:
            result.add(file, "<root>", f"missing required section '{section}'")
        elif not isinstance(section_data, dict):
            result.add(file, section, "expected a mapping")

    pagination = data.get("pagination")
    if isinstance(pagination, dict):
        for int_field in ("guild_list_page_size", "guild_hall_display_limit"):
            val = pagination.get(int_field)
            if val is not None:
                _check_type(val, int, result=result, file=file, path="pagination", field_name=int_field)
                _check_positive(
                    val, result=result, file=file, path="pagination", field_name=int_field, allow_zero=False
                )

    contribution = data.get("contribution")
    if isinstance(contribution, dict):
        min_donation = contribution.get("min_donation_amount")
        if min_donation is not None:
            _check_type(
                min_donation,
                (int, float),
                result=result,
                file=file,
                path="contribution",
                field_name="min_donation_amount",
            )
            _check_positive(
                min_donation,
                result=result,
                file=file,
                path="contribution",
                field_name="min_donation_amount",
                allow_zero=False,
            )

        daily_limits = contribution.get("daily_limits")
        if daily_limits is not None:
            if not isinstance(daily_limits, dict):
                result.add(file, "contribution.daily_limits", "expected a mapping")
            else:
                for resource, limit in daily_limits.items():
                    if not isinstance(limit, (int, float)) or limit < 0:
                        result.add(
                            file,
                            f"contribution.daily_limits.{resource}",
                            f"expected a non-negative number, got {limit!r}",
                        )

    hero_pool = data.get("hero_pool")
    if hero_pool is not None:
        if not isinstance(hero_pool, dict):
            result.add(file, "hero_pool", "expected a mapping")
        else:
            for int_field in ("slot_limit", "battle_lineup_limit", "replace_cooldown_seconds"):
                val = hero_pool.get(int_field)
                if val is not None:
                    _check_type(val, int, result=result, file=file, path="hero_pool", field_name=int_field)
                    _check_positive(
                        val, result=result, file=file, path="hero_pool", field_name=int_field, allow_zero=False
                    )

    return result


# ---------------------------------------------------------------------------
# Schema: recruitment_rarity_weights.yaml
# ---------------------------------------------------------------------------

VALID_RARITY_WEIGHT_KEYS = {"orange", "hermit", "purple", "red", "blue", "green", "gray", "black"}


def validate_recruitment_rarity_weights(
    data: dict, *, file: str = "recruitment_rarity_weights.yaml"
) -> ValidationResult:
    result = ValidationResult()

    if not isinstance(data, dict):
        result.add(file, "<root>", "expected a mapping at root level")
        return result

    total_weight = data.get("total_weight")
    if total_weight is None:
        result.add(file, "<root>", "missing required key 'total_weight'")
    else:
        _check_type(total_weight, int, result=result, file=file, path="<root>", field_name="total_weight")
        _check_positive(
            total_weight, result=result, file=file, path="<root>", field_name="total_weight", allow_zero=False
        )

    weights = data.get("weights")
    if weights is None:
        result.add(file, "<root>", "missing required key 'weights'")
        return result

    if not isinstance(weights, dict):
        result.add(file, "weights", "expected a mapping")
        return result

    for rarity, weight in weights.items():
        path = f"weights.{rarity}"
        if rarity not in VALID_RARITY_WEIGHT_KEYS:
            result.add(file, path, f"unknown rarity '{rarity}'")
        if not isinstance(weight, int):
            result.add(file, path, f"expected int, got {type(weight).__name__}")
        elif weight < 0:
            result.add(file, path, f"weight must be >= 0, got {weight}")

    return result
