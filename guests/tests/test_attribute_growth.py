"""
门客属性成长系统单元测试
"""

import random

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.config import GUEST
from guests.models import Guest, GuestArchetype, GuestRarity, GuestTemplate
from guests.utils.attribute_growth import (
    RARITY_ATTRIBUTE_GROWTH_RANGE,
    allocate_level_up_attributes,
    apply_attribute_growth,
    get_expected_growth,
)

User = get_user_model()


class TestAttributeGrowth(TestCase):
    """属性成长系统单元测试"""

    def setUp(self):
        """测试前准备"""
        # 创建测试用户（庄园会自动创建）
        self.user = User.objects.create_user(username="testuser", password="testpass")
        self.manor = self.user.manor

        # 创建测试用武将模板
        self.military_template = GuestTemplate.objects.create(
            key="test_military",
            name="测试武将",
            rarity=GuestRarity.ORANGE,
            archetype=GuestArchetype.MILITARY,
            base_attack=230,
            base_defense=185,
            base_agility=135,
            base_luck=95,
        )

        # 创建测试用文官模板
        self.civil_template = GuestTemplate.objects.create(
            key="test_civil",
            name="测试文官",
            rarity=GuestRarity.PURPLE,
            archetype=GuestArchetype.CIVIL,
            base_attack=175,
            base_defense=155,
            base_agility=110,
            base_luck=145,
        )

    def test_allocation_total_points(self):
        """测试：总属性点数在稀有度区间内"""
        guest = Guest.objects.create(
            template=self.military_template,
            manor=self.manor,
            level=1,
        )

        allocation = allocate_level_up_attributes(guest, levels=1)
        total = sum(allocation.values())

        min_growth, max_growth = RARITY_ATTRIBUTE_GROWTH_RANGE["orange"]
        self.assertGreaterEqual(total, min_growth, f"橙色门客每级应至少获得{min_growth}属性点")
        self.assertLessEqual(total, max_growth, f"橙色门客每级应最多获得{max_growth}属性点")

    def test_allocation_multiple_levels(self):
        """测试：多级升级属性点在合理区间内"""
        guest = Guest.objects.create(
            template=self.civil_template,
            manor=self.manor,
            level=1,
        )

        levels = 10
        allocation = allocate_level_up_attributes(guest, levels=levels)
        total = sum(allocation.values())

        min_growth, max_growth = RARITY_ATTRIBUTE_GROWTH_RANGE["purple"]
        min_expected = min_growth * levels
        max_expected = max_growth * levels

        self.assertGreaterEqual(total, min_expected, f"紫色门客升{levels}级应至少获得{min_expected}属性点")
        self.assertLessEqual(total, max_expected, f"紫色门客升{levels}级应最多获得{max_expected}属性点")

    def test_military_bias_toward_force(self):
        """测试：武将倾向武力"""
        guest = Guest.objects.create(
            template=self.military_template,
            manor=self.manor,
            level=1,
        )

        # 固定随机种子以便测试可重现
        rng = random.Random(42)
        allocation = allocate_level_up_attributes(guest, levels=100, rng=rng)

        # 武将权重：force 40% > defense 23% > agility 22% > intellect 15%
        self.assertGreater(allocation["force"], allocation["defense"], "武将的武力应该最高（权重40%）")
        self.assertGreater(allocation["defense"], allocation["intellect"], "武将的防御应该高于智力")
        self.assertGreater(allocation["agility"], allocation["intellect"], "武将的敏捷应该高于智力")

    def test_civil_bias_toward_intellect(self):
        """测试：文官倾向智力"""
        guest = Guest.objects.create(
            template=self.civil_template,
            manor=self.manor,
            level=1,
        )

        rng = random.Random(42)
        allocation = allocate_level_up_attributes(guest, levels=100, rng=rng)

        # 文官权重：intellect 40% > force 20% = defense 20% = agility 20%
        # 智力应该是最高的
        self.assertGreater(allocation["intellect"], allocation["force"], "文官的智力应该高于武力")
        self.assertGreater(allocation["intellect"], allocation["defense"], "文官的智力应该高于防御")
        self.assertGreater(allocation["intellect"], allocation["agility"], "文官的智力应该高于敏捷")

    def test_apply_growth(self):
        """测试：属性增长应用正确"""
        guest = Guest.objects.create(
            template=self.military_template,
            manor=self.manor,
            level=1,
            force=100,
            intellect=80,
            defense_stat=90,
            agility=85,
        )

        allocation = {"force": 10, "intellect": 5, "defense": 8, "agility": 7}
        apply_attribute_growth(guest, allocation)

        self.assertEqual(guest.force, 110)
        self.assertEqual(guest.intellect, 85)
        self.assertEqual(guest.defense_stat, 98)
        self.assertEqual(guest.agility, 92)

    def test_expected_growth_calculation(self):
        """测试：期望值计算正确（基于区间均值）"""
        expected = get_expected_growth("orange", "military", 1)

        # 橙色6-14点，均值10点，武将权重：force 40%, intellect 15%, defense 23%, agility 22%
        # 总权重 100，每点期望 = 9 * weight / 100
        self.assertAlmostEqual(expected["force"], 10 * 0.40, places=1)  # 4.0
        self.assertAlmostEqual(expected["intellect"], 10 * 0.15, places=2)  # 1.50
        self.assertAlmostEqual(expected["defense"], 10 * 0.23, places=2)  # 2.30
        self.assertAlmostEqual(expected["agility"], 10 * 0.22, places=2)  # 2.20

    def test_expected_growth_civil(self):
        """测试：文官期望值计算正确（基于区间均值）"""
        expected = get_expected_growth("purple", "civil", 1)

        # 紫色6-11点，均值8.5点，文官权重：force 20%, intellect 40%, defense 20%, agility 20%
        # 总权重 100，每点期望 = 8 * weight / 100
        self.assertAlmostEqual(expected["force"], 8.5 * 0.20, places=2)  # 1.70
        self.assertAlmostEqual(expected["intellect"], 8.5 * 0.40, places=1)  # 3.40
        self.assertAlmostEqual(expected["defense"], 8.5 * 0.20, places=1)  # 1.70
        self.assertAlmostEqual(expected["agility"], 8.5 * 0.20, places=2)  # 1.70

    def test_rarity_differences(self):
        """测试：不同稀有度属性点差异（区间成长）"""
        black_guest = Guest.objects.create(
            template=GuestTemplate.objects.create(
                key="test_black",
                name="黑色门客",
                rarity=GuestRarity.BLACK,
                archetype=GuestArchetype.MILITARY,
            ),
            manor=self.manor,
            level=1,
        )

        orange_guest = Guest.objects.create(
            template=self.military_template,
            manor=self.manor,
            level=1,
        )

        # 固定种子确保可重现
        rng = random.Random(42)
        black_alloc = allocate_level_up_attributes(black_guest, levels=1, rng=rng)

        rng = random.Random(42)
        orange_alloc = allocate_level_up_attributes(orange_guest, levels=1, rng=rng)

        black_total = sum(black_alloc.values())
        orange_total = sum(orange_alloc.values())

        # 检查每个稀有度的点数在其区间内
        black_min, black_max = RARITY_ATTRIBUTE_GROWTH_RANGE["black"]
        orange_min, orange_max = RARITY_ATTRIBUTE_GROWTH_RANGE["orange"]

        self.assertGreaterEqual(black_total, black_min, "黑色每级至少{black_min}点")
        self.assertLessEqual(black_total, black_max, "黑色每级最多{black_max}点")
        self.assertGreaterEqual(orange_total, orange_min, "橙色每级至少{orange_min}点")
        self.assertLessEqual(orange_total, orange_max, "橙色每级最多{orange_max}点")

    def test_random_distribution(self):
        """测试：随机分配在期望值附近"""
        guest = Guest.objects.create(
            template=self.military_template,
            manor=self.manor,
            level=1,
        )

        # 多次测试，检查平均值是否接近期望
        trials = 100
        total_force = 0

        for i in range(trials):
            rng = random.Random(i)  # 不同的种子
            allocation = allocate_level_up_attributes(guest, levels=10, rng=rng)
            total_force += allocation["force"]

        avg_force = total_force / trials
        expected = get_expected_growth("orange", "military", 10)["force"]

        # 平均值应在期望值±10%范围内
        self.assertAlmostEqual(
            avg_force, expected, delta=expected * 0.1, msg=f"100次测试平均值{avg_force}应接近期望值{expected}"
        )


class TestStatBlockWithoutMultiplier(TestCase):
    """测试战斗属性计算（无倍率）"""

    def setUp(self):
        """测试前准备"""
        # 创建测试用户（庄园会自动创建）
        self.user = User.objects.create_user(username="testuser2", password="testpass")
        self.manor = self.user.manor

        self.template = GuestTemplate.objects.create(
            key="test_guest",
            name="测试门客",
            rarity=GuestRarity.GREEN,
            archetype=GuestArchetype.MILITARY,
        )

    def test_stat_block_military(self):
        """测试：武将战斗属性直接计算"""
        guest = Guest.objects.create(
            template=self.template,
            manor=self.manor,
            level=50,
            force=400,
            intellect=200,
            defense_stat=300,
            agility=250,
        )

        stats = guest.stat_block()

        # 武将攻击 = 武力×MILITARY_FORCE_WEIGHT + 智力×MILITARY_INTELLECT_WEIGHT
        expected_attack = int(400 * GUEST.MILITARY_FORCE_WEIGHT + 200 * GUEST.MILITARY_INTELLECT_WEIGHT)
        self.assertEqual(stats["attack"], expected_attack)
        self.assertEqual(stats["defense"], 300)
        self.assertEqual(stats["intellect"], 200)

    def test_stat_block_civil(self):
        """测试：文官战斗属性直接计算"""
        civil_template = GuestTemplate.objects.create(
            key="test_civil",
            name="测试文官",
            rarity=GuestRarity.BLUE,
            archetype=GuestArchetype.CIVIL,
        )

        guest = Guest.objects.create(
            template=civil_template,
            manor=self.manor,
            level=50,
            force=200,
            intellect=500,
            defense_stat=300,
        )

        stats = guest.stat_block()

        # 文官攻击 = 武力×CIVIL_FORCE_WEIGHT + 智力×CIVIL_INTELLECT_WEIGHT
        expected_attack = int(200 * GUEST.CIVIL_FORCE_WEIGHT + 500 * GUEST.CIVIL_INTELLECT_WEIGHT)
        self.assertEqual(stats["attack"], expected_attack)

    def test_no_multiplier_applied(self):
        """测试：不再应用成长倍率"""
        guest = Guest.objects.create(
            template=self.template,
            manor=self.manor,
            level=1,
            force=100,
            intellect=80,
            defense_stat=90,
        )

        stats_level_1 = guest.stat_block()

        # 升级到100级，但不改变属性
        guest.level = 100
        stats_level_100 = guest.stat_block()

        # 战斗属性应该相同（因为属性值没变）
        self.assertEqual(stats_level_1["attack"], stats_level_100["attack"], "属性值不变，战斗属性也应不变")


class TestCustomTemplateGrowthConfig(TestCase):
    """测试模板自定义成长配置"""

    def setUp(self):
        """测试前准备"""
        self.user = User.objects.create_user(username="testuser3", password="testpass")
        self.manor = self.user.manor

        # 创建带自定义成长配置的模板
        self.custom_template = GuestTemplate.objects.create(
            key="test_custom_growth",
            name="自定义成长门客",
            rarity=GuestRarity.GREEN,  # 绿色默认 3-7 点
            archetype=GuestArchetype.MILITARY,
            growth_range=[10, 15],  # 自定义 10-15 点
            attribute_weights={
                "force": 10,
                "intellect": 10,
                "defense": 10,
                "agility": 70,  # 极端敏捷型
            },
        )

        # 创建无自定义配置的模板（使用默认）
        self.default_template = GuestTemplate.objects.create(
            key="test_default_growth",
            name="默认成长门客",
            rarity=GuestRarity.GREEN,
            archetype=GuestArchetype.MILITARY,
        )

    def test_custom_growth_range(self):
        """测试：模板自定义成长点数区间生效"""
        guest = Guest.objects.create(
            template=self.custom_template,
            manor=self.manor,
            level=1,
        )

        rng = random.Random(42)
        allocation = allocate_level_up_attributes(guest, levels=1, rng=rng)
        total = sum(allocation.values())

        # 应该在自定义区间 10-15 内，而非绿色默认 3-7
        self.assertGreaterEqual(total, 10, "自定义成长点数应至少10点")
        self.assertLessEqual(total, 15, "自定义成长点数应最多15点")

    def test_fixed_growth_range_always_allocates_exact_total(self):
        """测试：相同的成长区间上下限会固定每级成长点数"""
        fixed_template = GuestTemplate.objects.create(
            key="test_fixed_growth",
            name="固定成长门客",
            rarity=GuestRarity.ORANGE,
            archetype=GuestArchetype.MILITARY,
            growth_range=[14, 14],
        )
        guest = Guest.objects.create(
            template=fixed_template,
            manor=self.manor,
            level=1,
        )

        for seed in range(5):
            allocation = allocate_level_up_attributes(guest, levels=1, rng=random.Random(seed))
            self.assertEqual(sum(allocation.values()), 14)

    def test_default_growth_range(self):
        """测试：无自定义配置时使用稀有��默认值"""
        guest = Guest.objects.create(
            template=self.default_template,
            manor=self.manor,
            level=1,
        )

        rng = random.Random(42)
        allocation = allocate_level_up_attributes(guest, levels=1, rng=rng)
        total = sum(allocation.values())

        # 绿色默认 3-7 点
        self.assertGreaterEqual(total, 3, "绿色默认成长点数应至少3点")
        self.assertLessEqual(total, 7, "绿色默认成长点数应最多7点")

    def test_custom_attribute_weights(self):
        """测试：模板自定义属性权重生效"""
        guest = Guest.objects.create(
            template=self.custom_template,
            manor=self.manor,
            level=1,
        )

        # 大量样本测试权重分布
        rng = random.Random(42)
        allocation = allocate_level_up_attributes(guest, levels=100, rng=rng)

        # 自定义权重敏捷70%，其他各10%
        # 敏捷应该远高于其他属性
        self.assertGreater(allocation["agility"], allocation["force"], "自定义敏捷权重70%，应该远高于武力10%")
        self.assertGreater(allocation["agility"], allocation["intellect"], "自定义敏捷权重70%，应该远高于智力10%")
        self.assertGreater(allocation["agility"], allocation["defense"], "自定义敏捷权重70%，应该远高于防御10%")

        # 敏捷应该占总量的大约70%
        total = sum(allocation.values())
        agility_ratio = allocation["agility"] / total
        self.assertGreater(agility_ratio, 0.6, "敏捷占比应大于60%")

    def test_get_expected_growth_with_custom_config(self):
        """测试：期望值计算支持自定义配置"""
        # 使用自定义配置
        expected = get_expected_growth(
            "green",
            "military",
            levels=1,
            growth_range=[10, 15],
            attribute_weights={"force": 10, "intellect": 10, "defense": 10, "agility": 70},
        )

        # 10-15 均值 12.5，敏捷占70%
        total_weight = 100
        expected_agility = 12.5 * 70 / total_weight  # 8.75
        self.assertAlmostEqual(expected["agility"], expected_agility, places=1)

        # 其他属性各占10%
        expected_force = 12.5 * 10 / total_weight  # 1.25
        self.assertAlmostEqual(expected["force"], expected_force, places=1)

    def test_partial_attribute_weights(self):
        """测试：部分属性权重配置（缺失属性权重为0）"""
        # 只配置 force 和 agility，不配置 intellect 和 defense
        partial_template = GuestTemplate.objects.create(
            key="test_partial_weights",
            name="部分权重门客",
            rarity=GuestRarity.GREEN,
            archetype=GuestArchetype.MILITARY,
            attribute_weights={
                "force": 50,
                "agility": 50,
                # intellect 和 defense 未配置，应该为 0
            },
        )

        guest = Guest.objects.create(
            template=partial_template,
            manor=self.manor,
            level=1,
        )

        rng = random.Random(42)
        allocation = allocate_level_up_attributes(guest, levels=100, rng=rng)

        # intellect 和 defense 应该为 0（因为权重为0）
        self.assertEqual(allocation["intellect"], 0, "未配置权重的智力应为0")
        self.assertEqual(allocation["defense"], 0, "未配置权重的防御应为0")

        # force 和 agility 应该各占约 50%
        total = allocation["force"] + allocation["agility"]
        self.assertGreater(total, 0, "配置了权重的属性应有分配")

    def test_empty_attribute_weights_fallback(self):
        """测试：空权重配置回退到职业默认"""
        empty_weights_template = GuestTemplate.objects.create(
            key="test_empty_weights",
            name="空权重门客",
            rarity=GuestRarity.GREEN,
            archetype=GuestArchetype.MILITARY,
            attribute_weights={},  # 空字典
        )

        guest = Guest.objects.create(
            template=empty_weights_template,
            manor=self.manor,
            level=1,
        )

        rng = random.Random(42)
        allocation = allocate_level_up_attributes(guest, levels=100, rng=rng)

        # 应该回退到武将默认权重，所有属性都应有分配
        self.assertGreater(allocation["force"], 0, "回退默认权重后武力应有分配")
        self.assertGreater(allocation["defense"], 0, "回退默认权重后防御应有分配")
