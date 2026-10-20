"""清理旧版普通杨铮；保留真实玩家装备并补偿，删除虚拟玩家装备。"""

from django.core.cache import cache
from django.db import migrations, transaction
from django.utils import timezone

OLD_KEY = "hero_yang_zheng"
UNIQUE_KEY = "hero_yang_zheng_purple"
REAL_PLAYER_COMPENSATION_ITEM_KEY = "diaochan_guest_scroll"


def remove_ordinary_yang_zheng(apps, schema_editor):
    database = schema_editor.connection.alias
    GuestTemplate = apps.get_model("guests", "GuestTemplate")
    Guest = apps.get_model("guests", "Guest")
    GearItem = apps.get_model("guests", "GearItem")
    BotProfile = apps.get_model("gameplay", "BotProfile")
    Manor = apps.get_model("gameplay", "Manor")
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")
    InventoryItem = apps.get_model("gameplay", "InventoryItem")
    with transaction.atomic(using=database):
        old = GuestTemplate.objects.using(database).select_for_update().filter(key=OLD_KEY).first()
        if old is None:
            return
        unique = (
            GuestTemplate.objects.using(database).filter(key=UNIQUE_KEY, is_world_unique=True, rarity="purple").first()
        )
        if unique is None:
            raise RuntimeError("移除普通杨铮前必须先同步全服唯一紫色杨铮。")

        manor_ids = list(
            Guest.objects.using(database)
            .filter(template_id=old.pk)
            .order_by("manor_id")
            .values_list("manor_id", flat=True)
            .distinct()
        )
        # 遵循游戏服务的 Manor -> Guest -> inventory 锁顺序。
        list(Manor.objects.using(database).select_for_update().filter(pk__in=manor_ids).order_by("pk"))
        legacy_guests = list(
            Guest.objects.using(database).select_for_update().filter(template_id=old.pk).order_by("pk")
        )
        if any(guest.status != "idle" for guest in legacy_guests):
            raise RuntimeError("普通杨铮仍处于非闲置状态，已保留全部数据。")

        bot_manor_ids = set(
            BotProfile.objects.using(database).filter(manor_id__in=manor_ids).values_list("manor_id", flat=True)
        )
        has_real_player_guest = any(guest.manor_id not in bot_manor_ids for guest in legacy_guests)
        compensation_item = None
        if has_real_player_guest:
            compensation_item = (
                ItemTemplate.objects.using(database).filter(key=REAL_PLAYER_COMPENSATION_ITEM_KEY).first()
            )
            if compensation_item is None:
                raise RuntimeError("缺少貂蝉招募卷轴模板，已保留全部普通杨铮数据。")

        for guest in legacy_guests:
            if guest.manor_id in bot_manor_ids:
                # 虚拟庄园的旧角色与其专属装备一起清理。
                GearItem.objects.using(database).filter(guest_id=guest.pk).delete()
            else:
                # 删除真实玩家门客时 GearItem.guest 使用 SET_NULL，装备实例留在原庄园。
                inventory_item = (
                    InventoryItem.objects.using(database)
                    .select_for_update()
                    .filter(
                        manor_id=guest.manor_id,
                        template_id=compensation_item.pk,
                        storage_location="warehouse",
                    )
                    .first()
                )
                if inventory_item is None:
                    InventoryItem.objects.using(database).create(
                        manor_id=guest.manor_id,
                        template_id=compensation_item.pk,
                        quantity=1,
                        bound_quantity=0,
                        storage_location="warehouse",
                    )
                else:
                    inventory_item.quantity += 1
                    inventory_item.updated_at = timezone.now()
                    inventory_item.save(update_fields=["quantity", "updated_at"])
            guest.delete(using=database)

        # Keep other historical references intact; the operation only removes
        # the explicitly classified old guest instances above.
        for relation in GuestTemplate._meta.related_objects:
            if relation.one_to_many or relation.one_to_one:
                if relation.related_model.objects.using(database).filter(**{relation.field.attname: old.pk}).exists():
                    raise RuntimeError(f"普通杨铮仍有 {relation.related_model._meta.label} 引用，已保留模板。")
        old.delete(using=database)
        transaction.on_commit(
            lambda: cache.delete_many(["battle:guest_templates", "guest_templates:by_rarity"]),
            using=database,
            robust=True,
        )


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0224_zhanlong_zhangfei_portraits")]
    operations = [migrations.RunPython(remove_ordinary_yang_zheng, migrations.RunPython.noop, atomic=True)]
