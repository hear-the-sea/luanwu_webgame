"""Sync presentation fields without reimporting gameplay attributes or player guests."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from PIL import Image

from battle.combatants_pkg.cache import clear_guest_template_cache
from core.utils.image_utils import compress_and_resize_image
from core.utils.yaml_loader import load_yaml_data
from guests.models import GuestTemplate
from guests.services.recruitment_templates import clear_template_cache

logger = logging.getLogger(__name__)
IMAGE_VERSION = b"guest-portrait-v1:webp:400x400:quality85:"


class Command(BaseCommand):
    help = "Safely sync only existing guest template avatars and biographies; never modify player guests."

    def add_arguments(self, parser):
        parser.add_argument("--file", default=str(Path(settings.BASE_DIR) / "data/guest_templates.yaml"))
        parser.add_argument("--heroes-dir", default=str(Path(settings.BASE_DIR) / "data/guests"))
        parser.add_argument("--images-dir", default=str(Path(settings.BASE_DIR) / "data/images/guests"))
        parser.add_argument("--dry-run", action="store_true")

    def _read_entries(self, file_path: Path, heroes_dir: Path) -> list[dict]:
        if not heroes_dir.is_dir():
            raise CommandError(f"Missing heroes directory: {heroes_dir}")
        files = [file_path, *sorted(p for p in heroes_dir.iterdir() if p.suffix.lower() in {".yaml", ".yml", ".json"})]
        entries = []
        for path in files:
            payload = load_yaml_data(
                path, logger=logger, context="guest portrait sync", default={}, raise_on_error=True
            )
            if not isinstance(payload, dict):
                raise CommandError(f"Invalid mapping in {path}")
            templates = payload.get("templates") or []
            heroes = payload.get("heroes") or payload.get("hero_roster") or {}
            if not isinstance(templates, list) or not isinstance(heroes, dict):
                raise CommandError(f"Invalid templates/heroes in {path}")
            entries.extend(templates)
            for hero_list in heroes.values():
                if not isinstance(hero_list, list):
                    raise CommandError(f"Invalid hero list in {path}")
                entries.extend(hero_list)
        return entries

    def _validate(self, entries: list[dict], images_dir: Path) -> tuple[dict[str, dict], dict[str, tuple[Path, str]]]:
        root = images_dir.resolve()
        records = {}
        assets = {}
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("key"), str) or not entry["key"]:
                raise CommandError("Every template must have a nonempty string key")
            key = entry["key"]
            if key in records:
                raise CommandError(f"Duplicate template key: {key}")
            flavor = entry.get("flavor", "")
            max_length = GuestTemplate._meta.get_field("flavor").max_length
            if not isinstance(flavor, str) or (max_length is not None and len(flavor) > max_length):
                raise CommandError(f"Invalid biography: {key}")
            records[key] = entry
            filename = entry.get("avatar")
            if not filename:
                continue
            if not isinstance(filename, str):
                raise CommandError(f"Invalid avatar filename: {key}")
            if filename in assets:
                continue
            path = (root / filename).resolve()
            if not path.is_relative_to(root) or Path(filename).is_absolute() or "\\" in filename:
                raise CommandError(f"Unsafe avatar path: {filename}")
            if not path.is_file():
                raise CommandError(f"Missing avatar: {filename}")
            with Image.open(path) as image:
                image.verify()
            digest = hashlib.sha256(IMAGE_VERSION + path.read_bytes()).hexdigest()
            assets[filename] = (path, f"guests/portrait_{digest}.webp")
        if not records:
            raise CommandError("No template records found")
        return records, assets

    def handle(self, *args, **options):
        try:
            records, assets = self._validate(
                self._read_entries(Path(options["file"]), Path(options["heroes_dir"])),
                Path(options["images_dir"]),
            )
        except (OSError, ValueError, TypeError) as exc:
            raise CommandError(f"Portrait validation failed: {exc}") from exc
        found = set(GuestTemplate.objects.filter(key__in=records).values_list("key", flat=True))
        missing = sorted(set(records) - found)
        if missing:
            raise CommandError(f"Templates missing from database ({len(missing)}): {', '.join(missing[:20])}")
        self.stdout.write(f"Validated {len(records)} templates and {len(assets)} source images.")
        if options["dry_run"]:
            self.stdout.write("Dry run: no database or storage changes.")
            return

        storage = GuestTemplate._meta.get_field("avatar").storage
        stored = {}
        # Upload immutable assets first; a failure leaves all database fields unchanged.
        for filename, (path, target) in assets.items():
            if not storage.exists(target):
                content, _ = compress_and_resize_image(path, max_size=(400, 400), quality=85, convert_to_webp=True)
                target = storage.save(target, content)
            stored[filename] = target
        changed = 0
        with transaction.atomic():
            for template in GuestTemplate.objects.select_for_update().filter(key__in=records):
                entry = records[template.key]
                updates = {}
                if "flavor" in entry and template.flavor != entry["flavor"]:
                    updates["flavor"] = entry["flavor"]
                avatar = stored.get(entry.get("avatar"))
                if avatar and template.avatar.name != avatar:
                    updates["avatar"] = avatar
                if updates:
                    GuestTemplate.objects.filter(pk=template.pk).update(**updates)
                    changed += 1
            transaction.on_commit(clear_template_cache)
            transaction.on_commit(clear_guest_template_cache)
        self.stdout.write(self.style.SUCCESS(f"Updated {changed} templates; player guests and old images untouched."))
