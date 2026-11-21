"""把重复的程知节门客模板并入程咬金，避免同一人物保留两份。"""

from __future__ import annotations

from django.db import migrations

CANONICAL_KEY = "hero_cheng_yaojin"
DUPLICATE_KEY = "hist_suitang_0057_chengzhijie"


def merge_duplicate_template_pairs(apps, schema_editor, pairs):
    """将重复门客模板的所有关联记录安全迁至保留模板，再删除重复模板。"""
    database = schema_editor.connection.alias if schema_editor is not None else "default"
    GuestTemplate = apps.get_model("guests", "GuestTemplate")
    Guest = apps.get_model("guests", "Guest")
    RecruitmentCandidate = apps.get_model("guests", "RecruitmentCandidate")
    RecruitmentPoolEntry = apps.get_model("guests", "RecruitmentPoolEntry")
    WorldUniqueGuest = apps.get_model("guests", "WorldUniqueGuest")
    JailPrisoner = apps.get_model("gameplay", "JailPrisoner")

    for duplicate_key, canonical_key in pairs:
        duplicate = GuestTemplate.objects.using(database).filter(key=duplicate_key).first()
        if duplicate is None:
            continue

        canonical = GuestTemplate.objects.using(database).filter(key=canonical_key).first()
        if canonical is None:
            # 旧库仅有重复模板时直接改用正式 key，既有门客与俘虏记录不会丢失。
            duplicate.key = canonical_key
            duplicate.save(update_fields=["key"])
            continue

        Guest.objects.using(database).filter(template_id=duplicate.pk).update(template_id=canonical.pk)
        JailPrisoner.objects.using(database).filter(guest_template_id=duplicate.pk).update(
            guest_template_id=canonical.pk
        )
        RecruitmentCandidate.objects.using(database).filter(template_id=duplicate.pk).update(
            template_id=canonical.pk,
            rarity=canonical.rarity,
            archetype=canonical.archetype,
            display_name=canonical.name,
        )

        for entry in RecruitmentPoolEntry.objects.using(database).filter(template_id=duplicate.pk):
            existing = (
                RecruitmentPoolEntry.objects.using(database)
                .filter(pool_id=entry.pool_id, template_id=canonical.pk)
                .first()
            )
            if existing is None:
                entry.template_id = canonical.pk
                entry.save(update_fields=["template"])
            else:
                existing.weight += entry.weight
                existing.save(update_fields=["weight"])
                entry.delete()

        for state in WorldUniqueGuest.objects.using(database).filter(template_id=duplicate.pk):
            existing_state = WorldUniqueGuest.objects.using(database).filter(template_id=canonical.pk).first()
            if existing_state is None:
                state.template_id = canonical.pk
                state.save(update_fields=["template"])
            else:
                should_promote = existing_state.status == "wild" and state.status != "wild"
                promoted_values = (
                    state.status,
                    state.owner_manor_id,
                    state.owner_guest_id,
                    state.owner_prisoner_id,
                )
                # 先删除旧状态以释放 owner_guest / owner_prisoner 的 OneToOne 唯一约束。
                state.delete()
                if should_promote:
                    (
                        existing_state.status,
                        existing_state.owner_manor_id,
                        existing_state.owner_guest_id,
                        existing_state.owner_prisoner_id,
                    ) = promoted_values
                    existing_state.save(update_fields=["status", "owner_manor", "owner_guest", "owner_prisoner"])
        duplicate.delete()


def merge_duplicate_template(apps, schema_editor):
    merge_duplicate_template_pairs(apps, schema_editor, ((DUPLICATE_KEY, CANONICAL_KEY),))


class Migration(migrations.Migration):
    dependencies = [("guests", "0095_alter_guesttemplate_avatar")]

    operations = [
        migrations.RunPython(merge_duplicate_template, migrations.RunPython.noop),
    ]
