"""
门客装备系统单元测试
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.exceptions import BattlePreparationError, ItemNotFoundError
from gameplay.models import InventoryItem, ItemTemplate
from guests.models import GearItem, GearSlot, GearTemplate, Guest, GuestArchetype, GuestRarity, GuestTemplate
from guests.services.equipment import equip_guest, unequip_guest_item

User = get_user_model()


class TestEquipmentHealthManagement(TestCase):
    """测试装备对生命值的影响"""

    def setUp(self):
        """测试前准备"""
        from gameplay.models import InventoryItem, ItemTemplate

        # 创建测试用户（庄园会自动创建）
        self.user = User.objects.create_user(username="testuser", password="testpass")
        self.manor = self.user.manor

        # 创建测试用门客模板
        self.template = GuestTemplate.objects.create(
            key="test_guest",
            name="测试门客",
            rarity=GuestRarity.BLUE,
            archetype=GuestArchetype.MILITARY,
            base_hp=1000,
        )

        # 创建测试门客（10级）
        # 新系统HP计算：base_hp + defense × 50 = 1000 + 180 × 50 = 10,000
        self.guest = Guest.objects.create(
            template=self.template,
            manor=self.manor,
            level=10,
            force=200,
            intellect=150,
            defense_stat=180,
            current_hp=10000,  # 当前满血（新系统：base_hp + defense×50）
        )

        # 创建物品模板（用于背包同步）
        self.InventoryItem = InventoryItem
        self.item_template = ItemTemplate.objects.create(
            key="test_ornament_hp",
            name="生命护符",
            effect_type="ornament",
            effect_payload={
                "hp": 200,  # 提供200点生命值
                "force": 10,
            },
            rarity=GuestRarity.BLUE,
        )

        # 创建装备模板
        self.gear_template = GearTemplate.objects.create(
            key="test_ornament_hp",
            name="生命护符",
            slot=GearSlot.ORNAMENT,
            rarity=GuestRarity.BLUE,
            extra_stats={
                "hp": 200,
                "force": 10,
            },
        )

    def test_unequip_reduces_max_hp_and_adjusts_current_hp(self):
        """测试：卸下装备后最大生命值降低，当前生命值自动调整"""
        # 1. 创建装备并装备到门客
        gear = GearItem.objects.create(manor=self.manor, template=self.gear_template)

        # 添加到背包（模拟装备来源）
        self.InventoryItem.objects.create(manor=self.manor, template=self.item_template, quantity=1)

        # 装备前的最大生命值（新系统：1000 + 180×50 = 10,000）
        max_hp_before = self.guest.max_hp
        self.assertEqual(max_hp_before, 10000)
        self.assertEqual(self.guest.current_hp, 10000)

        # 2. 装备道具（额外+200 HP）
        gear = equip_guest(gear, self.guest)
        self.guest.refresh_from_db()

        # 装备后的最大生命值：10,000 + 200 = 10,200
        max_hp_equipped = self.guest.max_hp
        self.assertEqual(max_hp_equipped, 10200)

        # 假设门客受伤，当前生命值为10,100
        self.guest.current_hp = 10100
        self.guest.save(update_fields=["current_hp"])

        # 3. 卸下装备
        unequip_guest_item(gear, self.guest)
        self.guest.refresh_from_db()

        # 卸下后的最大生命值：10,000
        max_hp_after = self.guest.max_hp
        self.assertEqual(max_hp_after, 10000)

        # 当前生命值应该被调整为不超过最大生命值
        self.assertLessEqual(self.guest.current_hp, max_hp_after)
        self.assertEqual(self.guest.current_hp, 10000)

    def test_unequip_does_not_change_current_hp_if_below_max(self):
        """测试：卸下装备时，如果当前生命值低于新的最大生命值，则不改变"""
        # 1. 创建装备并装备
        gear = GearItem.objects.create(manor=self.manor, template=self.gear_template)
        self.InventoryItem.objects.create(manor=self.manor, template=self.item_template, quantity=1)

        gear = equip_guest(gear, self.guest)
        self.guest.refresh_from_db()

        # 装备后最大生命值10,200，当前生命值设为8,000（低于卸下后的10,000）
        self.guest.current_hp = 8000
        self.guest.save(update_fields=["current_hp"])

        # 2. 卸下装备
        unequip_guest_item(gear, self.guest)
        self.guest.refresh_from_db()

        # 当前生命值不应改变
        self.assertEqual(self.guest.current_hp, 8000)

    def test_unequip_handles_zero_hp_case(self):
        """测试：卸下装备时处理0生命值的情况"""
        # 1. 创建装备并装备
        gear = GearItem.objects.create(manor=self.manor, template=self.gear_template)
        self.InventoryItem.objects.create(manor=self.manor, template=self.item_template, quantity=1)

        gear = equip_guest(gear, self.guest)
        self.guest.refresh_from_db()

        # 门客阵亡，生命值为0
        self.guest.current_hp = 0
        self.guest.save(update_fields=["current_hp"])

        # 2. 卸下装备
        unequip_guest_item(gear, self.guest)
        self.guest.refresh_from_db()

        # 生命值应该保持为0
        self.assertEqual(self.guest.current_hp, 0)


class TestEquipmentTroopCapacity(TestCase):
    def setUp(self):
        from gameplay.models import InventoryItem, ItemTemplate

        self.InventoryItem = InventoryItem
        self.ItemTemplate = ItemTemplate
        self.user = User.objects.create_user(username="troop_capacity_user", password="testpass")
        self.manor = self.user.manor
        self.template = GuestTemplate.objects.create(
            key="troop_capacity_guest",
            name="带兵测试门客",
            rarity=GuestRarity.BLUE,
            archetype=GuestArchetype.MILITARY,
            base_hp=1000,
        )
        self.guest = Guest.objects.create(
            template=self.template,
            manor=self.manor,
            level=10,
            force=200,
            intellect=150,
            defense_stat=180,
            current_hp=10000,
        )

    def _create_gear(
        self,
        *,
        key: str,
        name: str,
        slot,
        effect_type: str,
        extra_stats: dict,
        set_key: str = "",
        set_bonus: dict | None = None,
    ) -> GearItem:
        payload = dict(extra_stats)
        if set_key:
            payload["set_key"] = set_key
            payload["set_description"] = "测试套装"
            payload["set_bonus"] = set_bonus or {}

        item_template = self.ItemTemplate.objects.create(
            key=key,
            name=name,
            effect_type=effect_type,
            effect_payload=payload,
            rarity=GuestRarity.BLUE,
        )
        self.InventoryItem.objects.create(manor=self.manor, template=item_template, quantity=1)

        gear_template = GearTemplate.objects.create(
            key=key,
            name=name,
            slot=slot,
            rarity=GuestRarity.BLUE,
            set_key=set_key,
            set_description="测试套装" if set_key else "",
            set_bonus=set_bonus or {},
            extra_stats=extra_stats,
        )
        return GearItem.objects.create(manor=self.manor, template=gear_template)

    def test_equipment_troop_capacity_bonus_applies_to_battle_validation(self):
        from battle.services import validate_troop_capacity

        gear = self._create_gear(
            key="test_troop_ornament",
            name="统军玉佩",
            slot=GearSlot.ORNAMENT,
            effect_type="equip_ornament",
            extra_stats={"troop_capacity": 30},
        )

        self.assertEqual(self.guest.troop_capacity, 200)
        with self.assertRaises(BattlePreparationError):
            validate_troop_capacity([self.guest], {"test_troop": 230})

        equip_guest(gear, self.guest)
        self.guest.refresh_from_db()

        self.assertEqual(self.guest.troop_capacity_bonus, 30)
        self.assertEqual(self.guest.troop_capacity, 230)
        validate_troop_capacity([self.guest], {"test_troop": 230})

        unequip_guest_item(gear, self.guest)
        self.guest.refresh_from_db()

        self.assertEqual(self.guest.troop_capacity_bonus, 0)
        self.assertEqual(self.guest.troop_capacity, 200)

    def test_set_bonus_troop_capacity_only_activates_when_piece_count_is_met(self):
        set_bonus = {"pieces": 4, "bonus": {"troop_capacity": 45}}
        gears = [
            self._create_gear(
                key="test_set_helmet",
                name="测试套头盔",
                slot=GearSlot.HELMET,
                effect_type="equip_helmet",
                extra_stats={"hp": 20},
                set_key="test_troop_set",
                set_bonus=set_bonus,
            ),
            self._create_gear(
                key="test_set_armor",
                name="测试套胸甲",
                slot=GearSlot.ARMOR,
                effect_type="equip_armor",
                extra_stats={"hp": 20},
                set_key="test_troop_set",
                set_bonus=set_bonus,
            ),
            self._create_gear(
                key="test_set_weapon",
                name="测试套武器",
                slot=GearSlot.WEAPON,
                effect_type="equip_weapon",
                extra_stats={"force": 10},
                set_key="test_troop_set",
                set_bonus=set_bonus,
            ),
            self._create_gear(
                key="test_set_shoes",
                name="测试套鞋子",
                slot=GearSlot.SHOES,
                effect_type="equip_shoes",
                extra_stats={"agility": 5},
                set_key="test_troop_set",
                set_bonus=set_bonus,
            ),
        ]

        for gear in gears[:3]:
            equip_guest(gear, self.guest)
        self.guest.refresh_from_db()

        self.assertEqual(self.guest.troop_capacity_bonus, 0)
        self.assertEqual(self.guest.troop_capacity, 200)

        equip_guest(gears[3], self.guest)
        self.guest.refresh_from_db()

        self.assertEqual(self.guest.troop_capacity_bonus, 45)
        self.assertEqual(self.guest.troop_capacity, 245)
        self.assertEqual(self.guest.gear_set_bonus.get("troop_capacity"), 45)

        unequip_guest_item(gears[3], self.guest)
        self.guest.refresh_from_db()

        self.assertEqual(self.guest.troop_capacity_bonus, 0)
        self.assertEqual(self.guest.troop_capacity, 200)
        self.assertNotIn("troop_capacity", self.guest.gear_set_bonus)

    def test_replacing_single_slot_item_removes_old_troop_capacity_bonus(self):
        old_gear = self._create_gear(
            key="test_old_helmet",
            name="旧统军盔",
            slot=GearSlot.HELMET,
            effect_type="equip_helmet",
            extra_stats={"troop_capacity": 20},
        )
        new_gear = self._create_gear(
            key="test_new_helmet",
            name="新统军盔",
            slot=GearSlot.HELMET,
            effect_type="equip_helmet",
            extra_stats={"force": 8},
        )

        equip_guest(old_gear, self.guest)
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.troop_capacity, 220)

        equip_guest(new_gear, self.guest)
        self.guest.refresh_from_db()
        old_gear.refresh_from_db()
        new_gear.refresh_from_db()

        self.assertIsNone(old_gear.guest)
        self.assertEqual(new_gear.guest_id, self.guest.id)
        self.assertEqual(self.guest.troop_capacity_bonus, 0)
        self.assertEqual(self.guest.troop_capacity, 200)


class TestEquipmentStringChoiceResolution(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="equip_choice_user", password="testpass")
        self.manor = self.user.manor
        self.guest_template = GuestTemplate.objects.create(
            key="equip_choice_guest_tpl",
            name="装备解析门客",
            rarity=GuestRarity.BLUE,
            archetype=GuestArchetype.MILITARY,
            base_hp=1000,
        )
        self.guest = Guest.objects.create(
            template=self.guest_template,
            manor=self.manor,
            level=10,
            force=120,
            intellect=100,
            defense_stat=90,
            current_hp=5500,
        )
        self.item_template = ItemTemplate.objects.create(
            key="equip_choice_weapon",
            name="装备解析刀",
            effect_type="equip_weapon",
            effect_payload={"force": 15},
            rarity=GuestRarity.GREEN,
        )

    def test_equip_guest_accepts_template_key_and_consumes_warehouse_stock(self):
        InventoryItem.objects.create(
            manor=self.manor,
            template=self.item_template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            quantity=1,
        )

        equipped = equip_guest(self.item_template.key, self.guest, slot=GearSlot.WEAPON)

        self.guest.refresh_from_db()
        equipped.refresh_from_db()
        self.assertEqual(equipped.guest_id, self.guest.id)
        self.assertEqual(equipped.template.key, self.item_template.key)
        self.assertEqual(self.guest.force, 135)
        self.assertFalse(
            InventoryItem.objects.filter(
                manor=self.manor,
                template=self.item_template,
                storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            ).exists()
        )

    def test_equip_guest_rejects_template_key_without_stock_and_leaves_no_dirty_gear(self):
        with self.assertRaises(ItemNotFoundError):
            equip_guest(self.item_template.key, self.guest, slot=GearSlot.WEAPON)

        self.guest.refresh_from_db()
        self.assertFalse(self.guest.gear_items.exists())
        self.assertFalse(GearItem.objects.filter(manor=self.manor, template__key=self.item_template.key).exists())
