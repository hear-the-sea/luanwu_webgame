from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from guests.avatar import (
    INSTANCE_AVATAR_TEMPLATE_KEYS,
    ensure_guest_avatar_asset,
    get_guest_avatar_catalog,
    pick_guest_avatar_filename,
)
from guests.models import Guest


class Command(BaseCommand):
    help = "Backfill missing base-gray guest portraits; defaults to a read-only preview."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--apply", action="store_true", help="Prepare media assets and persist avatar keys.")
        parser.add_argument("--manor-id", type=int, help="Only inspect guests belonging to this manor.")
        parser.add_argument("--limit", type=int, help="Maximum number of matching guests to inspect.")

    def handle(self, *args: Any, **options: Any) -> None:
        apply = bool(options["apply"])
        manor_id = options["manor_id"]
        limit = options["limit"]
        if manor_id is not None and manor_id <= 0:
            raise CommandError("--manor-id must be positive")
        if limit is not None and limit <= 0:
            raise CommandError("--limit must be positive")

        guests = (
            Guest.objects.filter(
                template__key__in=INSTANCE_AVATAR_TEMPLATE_KEYS,
                avatar_asset_key="",
                avatar_override_key="",
            )
            .select_related("template")
            .order_by("pk")
        )
        if manor_id is not None:
            guests = guests.filter(manor_id=manor_id)
        if limit is not None:
            guests = guests[:limit]

        catalog = get_guest_avatar_catalog()
        inspected = ready = updated = skipped = unavailable = 0
        for guest in guests.iterator(chunk_size=200):
            inspected += 1
            template_avatar = guest.template.avatar
            if template_avatar and template_avatar.storage.exists(template_avatar.name):
                skipped += 1
                continue
            filename = pick_guest_avatar_filename(
                catalog,
                guest.archetype,
                guest.gender,
                seed_key=f"guest-avatar-backfill:{guest.pk}",
                fallback_to_any=False,
            )
            if not filename:
                unavailable += 1
                self.stdout.write(
                    f"guest={guest.pk} status=unavailable archetype={guest.archetype} gender={guest.gender}"
                )
                continue
            ready += 1
            if not apply:
                continue
            asset_key = ensure_guest_avatar_asset(filename)
            if not asset_key:
                unavailable += 1
                self.stdout.write(f"guest={guest.pk} status=asset_failed source={filename}")
                continue
            # 保留并发分配的头像，门客归属或身份变化时跳过本次更新。
            changed = Guest.objects.filter(
                pk=guest.pk,
                manor_id=guest.manor_id,
                template_id=guest.template_id,
                gender=guest.gender,
                avatar_asset_key="",
                avatar_override_key="",
            ).update(avatar_asset_key=asset_key)
            updated += changed
            skipped += int(not changed)

        self.stdout.write(
            f"mode={'apply' if apply else 'dry-run'} inspected={inspected} "
            f"ready={ready} updated={updated} skipped={skipped} unavailable={unavailable}"
        )
        if unavailable:
            raise CommandError(
                f"{unavailable} guest avatars unavailable; check source images, gender tags and media permissions. "
                "Successful updates are preserved; rerun after correcting the errors."
            )
