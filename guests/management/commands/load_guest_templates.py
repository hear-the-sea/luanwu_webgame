from __future__ import annotations

import json
import logging
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import ForeignKey, OneToOneField
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from battle.combatants_pkg.cache import clear_guest_template_cache
from core.config import GUEST
from core.utils.image_utils import compress_and_resize_image
from core.utils.yaml_loader import ensure_mapping, load_yaml_data
from guests.constants import RARITY_CONVERSION_TEMPLATE_KEY_PREFIX
from guests.growth_rules import RARITY_HP_PROFILES
from guests.models import (
    Guest,
    GuestRarity,
    GuestStatus,
    GuestTemplate,
    RecruitmentPool,
    RecruitmentPoolEntry,
    Skill,
    SkillBook,
    SkillKind,
)
from guests.services.recruitment_templates import clear_template_cache
from guests.services.status import (
    GUEST_STATUS_UPDATE_FIELDS,
    prepare_guest_status_transition,
    schedule_resumed_guest_trainings,
)

logger = logging.getLogger(__name__)
AVATAR_LOAD_RECOVERABLE_EXCEPTIONS = (OSError,)
HERO_ROSTER_LOAD_RECOVERABLE_EXCEPTIONS = (CommandError, OSError, UnicodeDecodeError, json.JSONDecodeError)


class Command(BaseCommand):
    help = "Load guest templates and recruitment pools from a YAML/JSON file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=str,
            default=str(Path(settings.BASE_DIR) / "data" / "guest_templates.yaml"),
            help="Path to YAML/JSON file containing templates/pools definitions.",
        )
        parser.add_argument(
            "--assign-missing-avatars",
            action="store_true",
            help="Auto-assign avatars for templates missing avatar based on gender/archetype.",
        )
        parser.add_argument(
            "--skills-file",
            type=str,
            default="",
            help="Optional YAML/JSON file containing skills/skill_books definitions.",
        )
        parser.add_argument(
            "--heroes-dir",
            type=str,
            default="",
            help="Optional directory containing hero roster YAML files (e.g., gulong.yaml, huangyi.yaml).",
        )
        parser.add_argument(
            "--skip-images",
            action="store_true",
            help="Skip avatar compression/storage. Useful for CI/tests.",
        )

    def handle(self, *args, **options):
        verbosity = int(options.get("verbosity", 1) or 1)
        skip_images = bool(options.get("skip_images"))
        assign_missing_avatars = bool(options.get("assign_missing_avatars"))

        payload = self._load_main_payload(Path(options["file"]))
        if not payload:
            self.stdout.write(self.style.WARNING("Empty payload, nothing to import."))
            return

        skills_payload = self._load_skills_payload(options.get("skills_file") or "")
        heroes_dir_payload = self._load_heroes_payload(options.get("heroes_dir") or "")

        sections = self._build_sections(payload, skills_payload, heroes_dir_payload)
        template_data = sections["template_data"]
        pool_data = sections["pool_data"]
        skill_data = sections["skill_data"]
        book_data = sections["book_data"]

        self._append_hero_roster_templates(
            template_data,
            sections["hero_roster"],
            sections["attribute_profiles"],
            verbosity,
        )

        skill_map = self._sync_skills_and_books(skill_data, book_data, verbosity)
        template_keys, template_skill_keys, fallback_avatar_count = self._sync_templates(
            template_data,
            assign_missing_avatars,
            skip_images,
            verbosity,
        )
        desired_skill_keys = self._collect_desired_skill_keys(skill_data, book_data, template_skill_keys)
        book_keys = {data["key"] for data in book_data}

        key_to_template = {tpl.key: tpl for tpl in GuestTemplate.objects.filter(key__in=template_keys)}
        self._sync_template_skills(template_skill_keys, key_to_template, skill_map)

        pool_keys = self._sync_pools(pool_data, key_to_template, verbosity)
        self._cleanup_removed_templates(template_keys)
        self._cleanup_removed_books(book_keys)
        self._cleanup_removed_skills(desired_skill_keys)
        self._cleanup_removed_pools(pool_keys)
        self._finish_sync(verbosity, fallback_avatar_count)

    def _load_payload(self, path: Path) -> dict:
        if path.suffix.lower() in {".yaml", ".yml"}:
            raw = load_yaml_data(
                path,
                logger=logger,
                context="guest templates import file",
                default={},
            )
            return ensure_mapping(raw, logger=logger, context="guest templates import root")
        with path.open("r", encoding="utf-8") as f:
            if path.suffix.lower() == ".json":
                payload = json.load(f) or {}
                if isinstance(payload, dict):
                    return payload
                raise CommandError("JSON payload root must be an object.")
        raise CommandError("Unsupported file type. Use .yaml/.yml/.json")

    def _load_main_payload(self, file_path: Path) -> dict:
        if not file_path.exists():
            raise CommandError(f"File {file_path} does not exist.")
        return self._load_payload(file_path)

    def _load_skills_payload(self, skills_file: str) -> dict:
        if skills_file:
            skills_path = Path(skills_file)
            if not skills_path.exists():
                raise CommandError(f"Skills file {skills_path} does not exist.")
            return self._load_payload(skills_path)

        combined: dict[str, list[Any]] = {"skills": [], "skill_books": []}
        default_paths = [
            Path(settings.BASE_DIR) / "data" / "guest_skills.yaml",
            Path(settings.BASE_DIR) / "data" / "arena_coop_special_skills.yaml",
        ]
        for path in default_paths:
            if not path.exists():
                continue
            payload = self._load_payload(path)
            combined["skills"].extend(payload.get("skills") or [])
            combined["skill_books"].extend(payload.get("skill_books") or [])
        return combined

    def _load_heroes_payload(self, heroes_dir: str) -> dict[str, list]:
        if heroes_dir:
            heroes_path = Path(heroes_dir)
            if not heroes_path.exists() or not heroes_path.is_dir():
                raise CommandError(f"Heroes directory {heroes_path} does not exist.")
            return self._load_heroes_from_dir(heroes_path, self._load_payload)

        default_heroes_path = Path(settings.BASE_DIR) / "data" / "guests"
        if default_heroes_path.exists() and default_heroes_path.is_dir():
            return self._load_heroes_from_dir(default_heroes_path, self._load_payload)
        return {}

    def _build_sections(self, payload: dict, skills_payload: dict, heroes_dir_payload: dict[str, list]) -> dict:
        hero_roster = dict(payload.get("heroes") or payload.get("hero_roster") or {})
        for rarity, heroes in heroes_dir_payload.items():
            hero_roster.setdefault(rarity, []).extend(heroes)
        return {
            "template_data": list(payload.get("templates") or []),
            "pool_data": list(payload.get("pools") or []),
            "skill_data": (payload.get("skills") or []) + (skills_payload.get("skills") or []),
            "book_data": (payload.get("skill_books") or []) + (skills_payload.get("skill_books") or []),
            "attribute_profiles": payload.get("attribute_profiles") or {},
            "hero_roster": hero_roster,
        }

    def _build_template_from_stats(self, entry: dict, rarity: str, archetype: str, stats: dict) -> dict:
        stats = stats or {}
        hp_profile = RARITY_HP_PROFILES.get(rarity, {"base": 1200})
        base_hp = stats.get("base_hp", entry.get("base_hp", hp_profile["base"]))
        mapped = {
            "base_attack": stats.get("force", entry.get("base_attack", 100)),
            "base_intellect": stats.get("intellect", entry.get("base_intellect", 100)),
            "base_defense": stats.get("defense", entry.get("base_defense", 100)),
            "base_agility": stats.get("agility", entry.get("base_agility", 80)),
            "base_luck": stats.get("luck", entry.get("base_luck", 50)),
            "base_hp": base_hp,
        }
        built = {
            "key": entry["key"],
            "name": entry["name"],
            "archetype": archetype,
            "rarity": rarity,
            "flavor": entry.get("flavor", ""),
            "default_gender": entry.get("default_gender", "unknown"),
            "default_morality": entry.get("default_morality", 70),
            "recruitable": entry.get("recruitable", True),
            "is_hermit": entry.get("is_hermit", False),
            "skills": entry.get("skills") or [],
            "avatar": entry.get("avatar"),
            "growth_range": entry.get("growth_range") or [],
            "attribute_weights": entry.get("attribute_weights") or {},
        }
        built.update(mapped)
        return built

    def _append_hero_roster_templates(
        self,
        template_data: list[dict],
        hero_roster: dict,
        attribute_profiles: dict,
        verbosity: int,
    ) -> None:
        for rarity, heroes in hero_roster.items():
            profiles = attribute_profiles.get(rarity, {})
            for hero in heroes:
                archetype = hero["archetype"]
                stats = hero.get("custom_stats") or profiles.get(archetype)
                if not stats:
                    self.stdout.write(
                        self.style.WARNING(
                            f"Skip hero {hero['key']}: missing attribute profile for {rarity}/{archetype}"
                        )
                    )
                    continue
                template_data.append(self._build_template_from_stats(hero, rarity, archetype, stats))

    def _sync_skills_and_books(self, skill_data: list[dict], book_data: list[dict], verbosity: int) -> dict[str, Skill]:
        skill_map: dict[str, Skill] = {}
        for data in skill_data:
            defaults = {
                "name": data["name"],
                "rarity": data.get("rarity", GuestRarity.GRAY),
                "description": data.get("description", ""),
                "base_power": data.get("base_power", 100),
                "base_probability": data.get("base_probability", 0.1),
                "targets": data.get("targets", 1),
                "kind": data.get("kind", SkillKind.ACTIVE),
                "status_effect": data.get("status_effect", ""),
                "status_probability": data.get("status_probability", 0.0),
                "status_duration": data.get("status_duration", 1),
                "damage_formula": data.get("damage_formula", {}),
                "passive_config": data.get("passive_config") or {},
                "required_level": data.get("required_level", 0),
                "required_force": data.get("required_force", 0),
                "required_intellect": data.get("required_intellect", 0),
                "required_defense": data.get("required_defense", 0),
                "required_agility": data.get("required_agility", 0),
            }
            obj, created = Skill.objects.update_or_create(key=data["key"], defaults=defaults)
            skill_map[obj.key] = obj
            if verbosity >= 1:
                self.stdout.write(f"{'Created' if created else 'Updated'} skill {obj.key}")

        unresolved_book_skill_keys = {data["skill"] for data in book_data if data["skill"] not in skill_map}
        if unresolved_book_skill_keys:
            skill_map.update({skill.key: skill for skill in Skill.objects.filter(key__in=unresolved_book_skill_keys)})

        for data in book_data:
            skill_key = data["skill"]
            skill_obj = skill_map.get(skill_key)
            if not skill_obj:
                self.stdout.write(self.style.WARNING(f"Skip book {data['key']}: missing skill {skill_key}"))
                continue
            defaults = {
                "name": data["name"],
                "description": data.get("description", ""),
                "skill": skill_obj,
            }
            book_obj, created = SkillBook.objects.update_or_create(key=data["key"], defaults=defaults)
            if verbosity >= 1:
                self.stdout.write(f"{'Created' if created else 'Updated'} book {book_obj.key}")

        return skill_map

    def _template_defaults(self, data: dict) -> dict:
        hp_profile = RARITY_HP_PROFILES.get(data["rarity"], {"base": 1200})
        return {
            "name": data["name"],
            "archetype": data["archetype"],
            "rarity": data["rarity"],
            "base_attack": data.get("base_attack", 100),
            "base_intellect": data.get("base_intellect", 100),
            "base_defense": data.get("base_defense", 100),
            "base_agility": data.get("base_agility", 80),
            "base_luck": data.get("base_luck", 50),
            "base_hp": data.get("base_hp", hp_profile["base"]),
            "flavor": data.get("flavor", ""),
            "default_gender": data.get("default_gender", "unknown"),
            "default_morality": data.get("default_morality", 50),
            "recruitable": data.get("recruitable", True),
            "is_hermit": data.get("is_hermit", False),
            "growth_range": data.get("growth_range") or [],
            "attribute_weights": data.get("attribute_weights") or {},
        }

    def _prepare_avatar_context(
        self, assign_missing_avatars: bool, skip_images: bool
    ) -> tuple[Path, dict, dict[str, str]]:
        image_source_dir = Path(settings.BASE_DIR) / "data" / "images" / "guests"
        avatar_catalog = (
            self._build_avatar_catalog(image_source_dir) if assign_missing_avatars and not skip_images else {}
        )
        return image_source_dir, avatar_catalog, {}

    def _ensure_avatar_saved(
        self,
        obj: GuestTemplate,
        avatar_filename: str,
        avatar_path: Path,
        avatar_cache: dict[str, str],
        verbosity: int,
    ) -> tuple[str, bool]:
        stored_name = avatar_cache.get(avatar_filename)
        saved_now = False
        if stored_name:
            return stored_name, saved_now

        target_name = f"{avatar_path.stem}.webp"
        storage = obj.avatar.storage
        if storage.exists(target_name):
            stored_name = target_name
        else:
            compressed_file, new_filename = compress_and_resize_image(
                avatar_path,
                max_size=(400, 400),
                quality=85,
                convert_to_webp=True,
            )
            old_name = obj.avatar.name if obj.avatar and obj.avatar.name else ""
            obj.avatar.save(new_filename, compressed_file, save=True)
            stored_name = obj.avatar.name if obj.avatar and obj.avatar.name else new_filename
            self._safe_delete_old_avatar(obj, old_name, stored_name)
            if verbosity >= 1:
                self.stdout.write(
                    self.style.SUCCESS(f"  [OK] Compressed and loaded avatar: {avatar_filename} -> {new_filename}")
                )
            saved_now = True
        avatar_cache[avatar_filename] = stored_name
        return stored_name, saved_now

    def _sync_template_avatar(
        self,
        obj: GuestTemplate,
        data: dict,
        assign_missing_avatars: bool,
        image_source_dir: Path,
        avatar_catalog: dict,
        avatar_cache: dict[str, str],
        verbosity: int,
    ) -> int:
        avatar_filename = data.get("avatar")
        fallback_count = 0
        if not avatar_filename and assign_missing_avatars and not obj.avatar:
            avatar_filename = self._pick_fallback_avatar(
                avatar_catalog,
                data.get("archetype"),
                data.get("default_gender"),
                data.get("key"),
            )
            if avatar_filename:
                fallback_count = 1

        if not avatar_filename:
            return fallback_count

        avatar_path = image_source_dir / avatar_filename
        if not avatar_path.exists():
            if verbosity >= 1:
                self.stdout.write(self.style.WARNING(f"  [NOT FOUND] Avatar not found: {avatar_path}"))
            return fallback_count

        try:
            stored_name, saved_now = self._ensure_avatar_saved(
                obj,
                avatar_filename,
                avatar_path,
                avatar_cache,
                verbosity,
            )
            if not saved_now:
                old_name = obj.avatar.name if obj.avatar and obj.avatar.name else ""
                if old_name != stored_name:
                    obj.avatar.name = stored_name
                    obj.save(update_fields=["avatar"])
                    self._safe_delete_old_avatar(obj, old_name, stored_name)
        except AVATAR_LOAD_RECOVERABLE_EXCEPTIONS as exc:
            if verbosity >= 1:
                self.stdout.write(self.style.WARNING(f"  [FAIL] Failed to load avatar {avatar_filename}: {exc}"))

        return fallback_count

    def _sync_templates(
        self,
        template_data: list[dict],
        assign_missing_avatars: bool,
        skip_images: bool,
        verbosity: int,
    ) -> tuple[set[str], dict[str, list[str]], int]:
        image_source_dir, avatar_catalog, avatar_cache = self._prepare_avatar_context(
            assign_missing_avatars, skip_images
        )
        template_keys: set[str] = set()
        template_skill_keys: dict[str, list[str]] = {}
        fallback_avatar_count = 0
        synced_guest_hp_count = 0

        for data in template_data:
            existing = GuestTemplate.objects.filter(key=data["key"]).only("id", "base_hp").first()
            previous_base_hp = int(existing.base_hp or 0) if existing is not None else None
            obj, created = GuestTemplate.objects.update_or_create(
                key=data["key"], defaults=self._template_defaults(data)
            )
            synced_guest_hp_count += self._sync_existing_guest_current_hp_for_template_base_hp(
                template=obj,
                previous_base_hp=previous_base_hp,
            )
            if not skip_images:
                fallback_avatar_count += self._sync_template_avatar(
                    obj,
                    data,
                    assign_missing_avatars,
                    image_source_dir,
                    avatar_catalog,
                    avatar_cache,
                    verbosity,
                )

            template_keys.add(obj.key)
            template_skill_keys[obj.key] = data.get("skills") or []
            if verbosity >= 1:
                self.stdout.write(f"{'Created' if created else 'Updated'} template {obj.key}")
        if verbosity >= 1 and synced_guest_hp_count > 0:
            self.stdout.write(f"Synchronized current_hp for {synced_guest_hp_count} guests after template HP updates")

        return template_keys, template_skill_keys, fallback_avatar_count

    def _sync_existing_guest_current_hp_for_template_base_hp(
        self,
        *,
        template: GuestTemplate,
        previous_base_hp: int | None,
    ) -> int:
        if previous_base_hp is None:
            return 0

        new_base_hp = int(template.base_hp or 0)
        if previous_base_hp == new_base_hp:
            return 0

        hp_floor = int(GUEST.MIN_HP_FLOOR)
        defense_to_hp = int(GUEST.DEFENSE_TO_HP_MULTIPLIER)
        dirty_guests: list[Guest] = []
        guest_qs = Guest.objects.filter(template=template).only(
            "id",
            "current_hp",
            "hp_bonus",
            "defense_stat",
            "status",
            "injury_loyalty_processed_at",
            "training_complete_at",
            "training_remaining_seconds",
        )

        now = timezone.now()
        resumed_guests: list[Guest] = []
        for guest in guest_qs:
            old_max_hp = max(
                hp_floor,
                int(previous_base_hp)
                + int(getattr(guest, "hp_bonus", 0) or 0)
                + int(guest.defense_stat or 0) * defense_to_hp,
            )
            new_max_hp = max(
                hp_floor,
                new_base_hp + int(getattr(guest, "hp_bonus", 0) or 0) + int(guest.defense_stat or 0) * defense_to_hp,
            )
            current_hp = int(getattr(guest, "current_hp", 0) or 0)
            if current_hp <= 0:
                target_hp = 0
            elif current_hp >= old_max_hp:
                target_hp = new_max_hp
            else:
                target_hp = max(1, min(new_max_hp, int(new_max_hp * current_hp / old_max_hp)))

            target_status = guest.status
            target_injury_loyalty_processed_at = guest.injury_loyalty_processed_at
            if target_hp >= new_max_hp and target_status == GuestStatus.INJURED:
                target_status = GuestStatus.IDLE
                target_injury_loyalty_processed_at = None

            transition = None
            if target_status != guest.status:
                transition = prepare_guest_status_transition(guest, target_status, now=now)
                if transition.resumed_training:
                    resumed_guests.append(guest)

            if (
                target_hp == current_hp
                and target_status == guest.status
                and target_injury_loyalty_processed_at == guest.injury_loyalty_processed_at
                and not (transition and transition.changed)
            ):
                continue
            guest.current_hp = target_hp
            if transition is None:
                guest.status = target_status
            guest.injury_loyalty_processed_at = target_injury_loyalty_processed_at
            dirty_guests.append(guest)

        if not dirty_guests:
            return 0

        Guest.objects.bulk_update(
            dirty_guests,
            list(
                dict.fromkeys(
                    [
                        "current_hp",
                        "injury_loyalty_processed_at",
                        *GUEST_STATUS_UPDATE_FIELDS,
                    ]
                )
            ),
        )
        schedule_resumed_guest_trainings(
            resumed_guests,
            source="template_hp_sync",
        )
        return len(dirty_guests)

    def _sync_template_skills(
        self,
        template_skill_keys: dict[str, list[str]],
        key_to_template: dict[str, GuestTemplate],
        skill_map: dict[str, Skill],
    ) -> None:
        unresolved_skill_keys = {
            skill_key for keys in template_skill_keys.values() for skill_key in keys if skill_key not in skill_map
        }
        if unresolved_skill_keys:
            skill_map.update({skill.key: skill for skill in Skill.objects.filter(key__in=unresolved_skill_keys)})

        for template_key, skill_keys in template_skill_keys.items():
            tpl = key_to_template.get(template_key)
            if not tpl:
                continue
            if not skill_keys:
                tpl.initial_skills.clear()
                continue

            skill_objs = []
            for skill_key in skill_keys:
                skill_obj = skill_map.get(skill_key)
                if not skill_obj:
                    self.stdout.write(
                        self.style.WARNING(f"Skip template skill {skill_key} for {template_key}: skill not found")
                    )
                    continue
                skill_objs.append(skill_obj)
            tpl.initial_skills.set(skill_objs)

    def _sync_pools(
        self,
        pool_data: list[dict],
        key_to_template: dict[str, GuestTemplate],
        verbosity: int,
    ) -> set[str]:
        template_keys_in_pools = {
            entry.get("template")
            for pool in pool_data
            for entry in (pool.get("entries") or [])
            if entry.get("template")
        }
        missing_keys = {key for key in template_keys_in_pools if key not in key_to_template}
        if missing_keys:
            key_to_template.update({tpl.key: tpl for tpl in GuestTemplate.objects.filter(key__in=missing_keys)})

        pool_keys: set[str] = set()
        for pool in pool_data:
            entries = pool.get("entries") or []
            tier = pool.get("tier", RecruitmentPool.Tier.CUNMU)
            raw_cooldown = pool.get("cooldown_seconds")
            try:
                cooldown_seconds = max(0, int(raw_cooldown or 0))
            except (TypeError, ValueError):
                cooldown_seconds = 0
            defaults = {
                "name": pool["name"],
                "tier": tier,
                "description": pool.get("description", ""),
                "cost": pool.get("cost", {}),
                "cooldown_seconds": cooldown_seconds,
                "draw_count": pool.get("draw_count", 1),
            }
            pool_obj, _ = RecruitmentPool.objects.update_or_create(key=pool["key"], defaults=defaults)
            pool_keys.add(pool_obj.key)
            RecruitmentPoolEntry.objects.filter(pool=pool_obj).delete()

            new_entries: list[RecruitmentPoolEntry] = []
            for entry in entries:
                template_key = entry.get("template")
                rarity = entry.get("rarity")
                archetype = entry.get("archetype")
                weight = entry.get("weight", 1)

                tpl = None
                if template_key:
                    tpl = key_to_template.get(template_key)
                    if not tpl:
                        self.stdout.write(
                            self.style.WARNING(f"Skip pool {pool_obj.key} entry {template_key}: template not found")
                        )
                        continue
                elif not rarity:
                    self.stdout.write(
                        self.style.WARNING(
                            f"Skip pool {pool_obj.key} entry with weight {weight}: must specify template or rarity."
                        )
                    )
                    continue

                new_entries.append(
                    RecruitmentPoolEntry(
                        pool=pool_obj,
                        template=tpl,
                        rarity=rarity,
                        archetype=archetype,
                        weight=weight,
                    )
                )

            if new_entries:
                RecruitmentPoolEntry.objects.bulk_create(new_entries)
            if verbosity >= 1:
                self.stdout.write(f"Configured pool {pool_obj.key} with {len(new_entries)} entries")

        return pool_keys

    def _collect_desired_skill_keys(
        self,
        skill_data: list[dict],
        book_data: list[dict],
        template_skill_keys: dict[str, list[str]],
    ) -> set[str]:
        desired_skill_keys = {data["key"] for data in skill_data}
        desired_skill_keys.update({data["skill"] for data in book_data})
        for skill_keys in template_skill_keys.values():
            desired_skill_keys.update({skill_key for skill_key in skill_keys if skill_key})
        return desired_skill_keys

    def _cleanup_removed_templates(self, template_keys: set[str]) -> None:
        base_queryset = (
            GuestTemplate.objects.exclude(key__in=template_keys) if template_keys else GuestTemplate.objects.all()
        )
        conversion_ids = list(
            base_queryset.filter(key__startswith=RARITY_CONVERSION_TEMPLATE_KEY_PREFIX).values_list("pk", flat=True)
        )
        referenced_conversion_ids = self._referenced_guest_template_ids(conversion_ids)
        queryset = base_queryset.exclude(pk__in=referenced_conversion_ids)
        try:
            removed, _ = queryset.delete()
        except ProtectedError as exc:
            protected_templates = self._protected_guest_templates_from_error(exc)
            if not protected_templates:
                raise
            protected_keys = sorted(protected_templates.values_list("key", flat=True))
            queryset = queryset.exclude(pk__in=protected_templates.values("pk"))
            removed, _ = queryset.delete()
            self.stdout.write(
                self.style.WARNING(
                    "Skipped "
                    f"{len(protected_keys)} removed templates still referenced by protected records: "
                    f"{', '.join(protected_keys)}"
                )
            )
        if removed:
            self.stdout.write(self.style.WARNING(f"Removed {removed} templates not defined in payload"))

    def _referenced_guest_template_ids(self, template_ids: list[int]) -> set[int]:
        if not template_ids:
            return set()
        referenced_ids: set[int] = set()
        for related in GuestTemplate._meta.related_objects:
            field = getattr(related, "field", None)
            if not isinstance(field, (ForeignKey, OneToOneField)):
                continue
            related_model = getattr(related, "related_model", None)
            if related_model is None:
                continue
            filter_name = field.attname
            referenced_ids.update(
                related_model.objects.filter(**{f"{filter_name}__in": template_ids}).values_list(
                    filter_name,
                    flat=True,
                )
            )
        return referenced_ids

    def _protected_guest_templates_from_error(self, exc: ProtectedError):
        template_ids = {
            template_id
            for obj in exc.protected_objects
            for template_id in [getattr(obj, "guest_template_id", None)]
            if template_id is not None
        }
        return GuestTemplate.objects.filter(pk__in=template_ids)

    def _cleanup_removed_books(self, book_keys: set[str]) -> None:
        queryset = SkillBook.objects.exclude(key__in=book_keys) if book_keys else SkillBook.objects.all()
        removed, _ = queryset.delete()
        if removed:
            self.stdout.write(self.style.WARNING(f"Removed {removed} skill books not defined in payload"))

    def _cleanup_removed_skills(self, skill_keys: set[str]) -> None:
        queryset = Skill.objects.exclude(key__in=skill_keys) if skill_keys else Skill.objects.all()
        removed, _ = queryset.delete()
        if removed:
            self.stdout.write(self.style.WARNING(f"Removed {removed} skills not defined in payload"))

    def _cleanup_removed_pools(self, pool_keys: set[str]) -> None:
        queryset = RecruitmentPool.objects.exclude(key__in=pool_keys) if pool_keys else RecruitmentPool.objects.all()
        removed, _ = queryset.delete()
        if removed:
            self.stdout.write(self.style.WARNING(f"Removed {removed} pools not defined in payload"))

    def _finish_sync(self, verbosity: int, fallback_avatar_count: int) -> None:
        clear_template_cache()
        clear_guest_template_cache()
        if verbosity >= 1:
            if fallback_avatar_count:
                self.stdout.write(self.style.SUCCESS(f"Assigned {fallback_avatar_count} fallback avatars."))
            self.stdout.write(self.style.SUCCESS("Guest templates and pools synced."))

    def _load_heroes_from_dir(
        self,
        dir_path: Path,
        load_payload: Callable[[Path], dict[str, Any]],
    ) -> dict[str, list]:
        """Load and merge hero roster from all YAML/JSON files in a directory.

        Each file should contain a 'heroes' key with rarity-based hero lists.
        Files are loaded in alphabetical order and merged together.
        """
        merged: dict[str, list] = {}
        files = sorted(dir_path.glob("*.yaml")) + sorted(dir_path.glob("*.yml")) + sorted(dir_path.glob("*.json"))
        for file_path in files:
            try:
                payload = load_payload(file_path)
                heroes = payload.get("heroes") or payload.get("hero_roster") or {}
                if not isinstance(heroes, dict):
                    self.stdout.write(
                        self.style.WARNING(f"Failed to load {file_path.name}: heroes payload root must be a mapping")
                    )
                    continue
                for rarity, hero_list in heroes.items():
                    if rarity in merged:
                        merged[rarity].extend(hero_list)
                    else:
                        merged[rarity] = list(hero_list)
                self.stdout.write(f"Loaded heroes from {file_path.name}")
            except HERO_ROSTER_LOAD_RECOVERABLE_EXCEPTIONS as exc:
                self.stdout.write(self.style.WARNING(f"Failed to load {file_path.name}: {exc}"))
        return merged

    def _safe_delete_old_avatar(self, template: GuestTemplate, old_name: str, new_name: str) -> None:
        if not old_name or old_name == new_name:
            return
        if GuestTemplate.objects.filter(avatar=old_name).exclude(pk=template.pk).exists():
            return
        template.avatar.storage.delete(old_name)

    def _build_avatar_catalog(self, image_source_dir: Path) -> dict[tuple[str, str], list[str]]:
        catalog: dict[tuple[str, str], list[str]] = {
            ("civil", "male"): [],
            ("civil", "female"): [],
            ("military", "male"): [],
            ("military", "female"): [],
        }
        if not image_source_dir.exists():
            return catalog
        for path in image_source_dir.iterdir():
            if not path.is_file():
                continue
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            archetype, gender = self._extract_avatar_tags(path.name)
            if not archetype or not gender:
                continue
            catalog[(archetype, gender)].append(path.name)
        for key in catalog:
            catalog[key].sort()
        return catalog

    def _extract_avatar_tags(self, filename: str) -> tuple[str | None, str | None]:
        normalized = filename.lower().replace("-", "_")
        archetype = None
        if "_wen_" in normalized or "_wen." in normalized:
            archetype = "civil"
        elif "_wu_" in normalized or "_wu." in normalized:
            archetype = "military"
        gender = None
        if "_female" in normalized:
            gender = "female"
        elif "_male" in normalized:
            gender = "male"
        return archetype, gender

    def _pick_fallback_avatar(
        self,
        avatar_catalog: dict[tuple[str, str], list[str]],
        archetype: str | None,
        gender: str | None,
        seed_key: str | None,
    ) -> str | None:
        if not avatar_catalog:
            return None
        candidates: list[str] = []
        if archetype in {"civil", "military"}:
            if gender in {"male", "female"}:
                candidates = avatar_catalog.get((archetype, gender), [])
            else:
                candidates = avatar_catalog.get((archetype, "male"), []) + avatar_catalog.get((archetype, "female"), [])
        if not candidates:
            for items in avatar_catalog.values():
                candidates.extend(items)
        if not candidates:
            return None
        rng = random.Random(seed_key or "")
        return rng.choice(candidates)
