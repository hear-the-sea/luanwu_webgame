# ADR-004: YAML configuration vs database configuration boundary

## Status: Accepted
## Date: 2026-03

## Context

The game requires a large volume of structured data to define buildings, troops,
technologies, missions, items, guests (heroes), equipment, shop inventories, arena
rewards, and production recipes.  This data is authored by designers, version-
controlled alongside code, and rarely changes at runtime.  Player-specific instances
of these templates (e.g. a player's level-5 farm, or a recruited hero) are dynamic
and must live in the database.

## Decision

Split configuration into two tiers:

### Static templates -- YAML files under `data/`

Read-only definitions loaded into the database via management commands
(`load_building_templates`, `load_troop_templates`, `load_guest_templates`,
`load_item_templates`, `load_mission_templates`, `load_technology_templates`, etc.).
Each command reads a YAML file through `core.utils.yaml_loader`, validates entries,
and upserts rows via `update_or_create` keyed on a stable identifier (`key` field).

Examples: `data/building_templates.yaml`, `data/guest_templates.yaml`,
`data/troop_templates.yaml`, `data/forge_blueprints.yaml`, `data/shop_items.yaml`,
`data/luanwu_shop.yaml`,
plus per-era guest files under `data/guests/`.

### Dynamic instances -- database models

Player-owned objects (buildings, troops, inventory items, quests, heroes) reference
template rows via foreign key or key string.  All mutable state -- levels, cooldowns,
quantities, ownership -- lives exclusively in the database.

### Import workflow

YAML changes are applied by running the corresponding `manage.py` command.
Commands use `update_or_create` so they are idempotent and safe to re-run.
After import, in-process template caches are cleared (e.g.
`clear_building_template_cache()`).

## Consequences

- **Positive:** Templates are diffable, reviewable, and versioned in Git.  Designers
  can edit YAML without database access.  Imports are idempotent and auditable.
- **Negative:** YAML changes do not take effect until the import command runs; a
  deployment that updates YAML without running the loader will serve stale data.
- **Mitigation:** Deployment pipelines should include import commands after
  migrations.  Schema validation in the loader (`ensure_mapping`, `ensure_list`,
  type coercion helpers) catches malformed files early.
