import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext

from gameplay.models import InventoryItem, ItemTemplate
from gameplay.selectors.warehouse import _distinct_effect_types, get_warehouse_context
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestStatus, GuestTemplate

User = get_user_model()


@pytest.mark.django_db
def test_get_warehouse_context_guest_lists_filter_and_order():
    user = User.objects.create_user(username="warehouse_selector_user", password="pass123")
    manor = ensure_manor(user)

    tpl_alpha = GuestTemplate.objects.create(key="ws_alpha", name="Alpha", rarity="gray", archetype="civil")
    tpl_beta = GuestTemplate.objects.create(key="ws_beta", name="Beta", rarity="gray", archetype="civil")
    tpl_gamma = GuestTemplate.objects.create(key="ws_gamma", name="Gamma", rarity="gray", archetype="civil")
    tpl_zeta = GuestTemplate.objects.create(key="ws_zeta", name="Zeta", rarity="gray", archetype="civil")
    tpl_delta = GuestTemplate.objects.create(key="ws_delta", name="Delta", rarity="gray", archetype="civil")

    g_idle_low = Guest.objects.create(manor=manor, template=tpl_zeta, status=GuestStatus.IDLE, level=50)
    g_injured_100 = Guest.objects.create(
        manor=manor,
        template=tpl_alpha,
        status=GuestStatus.INJURED,
        level=100,
        xisuidan_used=3,
    )
    g_idle_100_xisui_ok = Guest.objects.create(
        manor=manor,
        template=tpl_beta,
        status=GuestStatus.IDLE,
        level=100,
        xisuidan_used=1,
        allocated_intellect=2,
    )
    g_idle_100_xisui_limit = Guest.objects.create(
        manor=manor,
        template=tpl_gamma,
        status=GuestStatus.IDLE,
        level=100,
        xisuidan_used=10,
        allocated_force=1,
    )
    Guest.objects.create(
        manor=manor,
        template=tpl_delta,
        status=GuestStatus.WORKING,
        level=100,
        xisuidan_used=0,
        allocated_force=5,
    )

    context = get_warehouse_context(manor, current_tab="warehouse", selected_category="all", page=1)

    assert [guest.id for guest in context["guests_for_rebirth"]] == [
        g_injured_100.id,
        g_idle_100_xisui_ok.id,
        g_idle_100_xisui_limit.id,
        g_idle_low.id,
    ]
    assert [guest.id for guest in context["guests_for_xisuidan"]] == [
        g_idle_100_xisui_ok.id,
        g_injured_100.id,
    ]
    assert [guest.id for guest in context["guests_for_xidianka"]] == [
        g_idle_100_xisui_ok.id,
        g_idle_100_xisui_limit.id,
    ]


@pytest.mark.django_db
def test_get_warehouse_context_exposes_avatar_choices_for_guest_disguise_card(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="warehouse_avatar_choices", password="pass123")
    manor = ensure_manor(user)
    guest_template = GuestTemplate.objects.create(
        key="warehouse_avatar_guest",
        name="头像易容测试门客",
        rarity="gray",
        archetype="civil",
    )
    Guest.objects.create(manor=manor, template=guest_template, status=GuestStatus.IDLE)
    template = ItemTemplate.objects.create(
        key="warehouse_avatar_disguise_card",
        name="门客易容卡",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={"action": "disguise_guest_avatar"},
    )
    InventoryItem.objects.create(manor=manor, template=template, quantity=1)
    choices = [{"key": "monkey_wen_male1.png", "label": "猴·男1", "url": "/media/guest.webp"}]
    monkeypatch.setattr("gameplay.selectors.warehouse.get_guest_disguise_avatar_options", lambda: choices)

    context = get_warehouse_context(manor, current_tab="warehouse", selected_category="all", page=1)

    assert context["guest_disguise_avatar_options"] == choices


@pytest.mark.django_db
def test_warehouse_context_does_not_expose_ordinary_capacity(django_user_model):
    user = django_user_model.objects.create_user(username="warehouse_capacity_context", password="pass123")
    manor = ensure_manor(user)
    template = ItemTemplate.objects.create(
        key="warehouse_capacity_context_item",
        name="仓库容量展示道具",
        storage_space=3,
    )
    InventoryItem.objects.create(manor=manor, template=template, quantity=2)

    context = get_warehouse_context(manor, current_tab="warehouse", selected_category="all", page=1)

    assert "warehouse_capacity" not in context
    assert "warehouse_used" not in context
    assert "warehouse_remaining" not in context
    assert "warehouse_over_capacity" not in context
    assert "warehouse_usage_percent" not in context


@pytest.mark.django_db
def test_distinct_effect_types_clears_order_by_for_distinct():
    user = User.objects.create_user(username="warehouse_effect_types_user", password="pass123")
    manor = ensure_manor(user)
    tpl_a = ItemTemplate.objects.create(key="ws_item_a", name="A", effect_type="tool")
    tpl_b = ItemTemplate.objects.create(key="ws_item_b", name="B", effect_type="skill_book")
    InventoryItem.objects.create(manor=manor, template=tpl_a, quantity=1)
    InventoryItem.objects.create(manor=manor, template=tpl_b, quantity=1)

    ordered_items = manor.inventory_items.select_related("template").order_by("template__name")
    effect_types_qs = _distinct_effect_types(ordered_items)
    sql = str(effect_types_qs.query).upper()

    assert "ORDER BY" not in sql
    assert set(effect_types_qs) == {"tool", "skill_book"}


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("item_key", "item_name"),
    [("grain", "粮食"), ("chunqiu_coin", "春秋币")],
)
def test_warehouse_context_marks_protected_resource_items_as_not_depositable(django_user_model, item_key, item_name):
    user = django_user_model.objects.create_user(username=f"warehouse_block_{item_key}", password="pass123")
    manor = ensure_manor(user)
    template, _created = ItemTemplate.objects.get_or_create(key=item_key, defaults={"name": item_name})
    InventoryItem.objects.create(manor=manor, template=template, quantity=1)

    context = get_warehouse_context(manor, current_tab="warehouse", selected_category="all", page=1)

    entry = next(item for item in context["inventory_items"] if item.template.key == item_key)
    assert entry.treasury_deposit_blocked is True
    assert entry.treasury_deposit_hint == f"{item_name}不可存入藏宝阁"


@pytest.mark.django_db
def test_warehouse_categories_prioritize_equipment_then_common_items():
    user = User.objects.create_user(username="warehouse_category_order", password="pass123")
    manor = ensure_manor(user)
    effect_types = (
        ItemTemplate.EffectType.RESOURCE,
        ItemTemplate.EffectType.SKILL_BOOK,
        "equip_helmet",
        ItemTemplate.EffectType.MEDICINE,
        "equip_weapon",
        ItemTemplate.EffectType.EXPERIENCE_ITEM,
        "equip_armor",
        ItemTemplate.EffectType.TOOL,
    )
    for index, effect_type in enumerate(effect_types):
        template = ItemTemplate.objects.create(
            key=f"warehouse_category_order_{index}",
            name=f"分类排序物品{index}",
            effect_type=effect_type,
        )
        InventoryItem.objects.create(manor=manor, template=template, quantity=1)

    context = get_warehouse_context(manor, current_tab="warehouse", selected_category="all", page=1)

    assert [category["key"] for category in context["categories"]] == [
        "equip_weapon",
        "equip_armor",
        "equip_helmet",
        ItemTemplate.EffectType.MEDICINE,
        ItemTemplate.EffectType.EXPERIENCE_ITEM,
        ItemTemplate.EffectType.SKILL_BOOK,
        "mission_chest",
        "tool",
        ItemTemplate.EffectType.RESOURCE,
    ]


@pytest.mark.django_db
def test_get_warehouse_context_queries_guest_table_once():
    user = User.objects.create_user(username="warehouse_query_user", password="pass123")
    manor = ensure_manor(user)
    template = GuestTemplate.objects.create(key="ws_query_tpl", name="QueryGuest", rarity="gray", archetype="civil")
    Guest.objects.create(manor=manor, template=template, status=GuestStatus.IDLE, level=10)
    Guest.objects.create(manor=manor, template=template, status=GuestStatus.INJURED, level=20)

    with CaptureQueriesContext(connection) as captured:
        get_warehouse_context(manor, current_tab="warehouse", selected_category="all", page=1)

    guest_queries = [q for q in captured.captured_queries if 'from "guests_guest"' in q["sql"].lower()]
    assert len(guest_queries) == 1


@pytest.mark.django_db
def test_get_warehouse_context_rarity_upgrade_list_only_contains_supported_idle_guests():
    user = User.objects.create_user(username="warehouse_rarity_upgrade_user", password="pass123")
    manor = ensure_manor(user)

    supported_a, _ = GuestTemplate.objects.get_or_create(
        key="hist_sljnbc_0589",
        defaults={
            "name": "邢道荣",
            "rarity": "green",
            "archetype": "military",
        },
    )
    supported_b, _ = GuestTemplate.objects.get_or_create(
        key="hist_sljnbc_0590",
        defaults={
            "name": "潘凤",
            "rarity": "green",
            "archetype": "military",
        },
    )
    unsupported = GuestTemplate.objects.create(
        key="warehouse_other_guest_tpl",
        name="其他门客",
        rarity="green",
        archetype="civil",
    )

    guest_idle_supported_a = Guest.objects.create(manor=manor, template=supported_a, status=GuestStatus.IDLE, level=30)
    guest_idle_supported_b = Guest.objects.create(manor=manor, template=supported_b, status=GuestStatus.IDLE, level=40)
    Guest.objects.create(manor=manor, template=supported_a, status=GuestStatus.INJURED, level=50)
    Guest.objects.create(manor=manor, template=unsupported, status=GuestStatus.IDLE, level=60)

    context = get_warehouse_context(manor, current_tab="warehouse", selected_category="all", page=1)
    assert [guest.id for guest in context["guests_for_rarity_upgrade"]] == [
        guest_idle_supported_b.id,
        guest_idle_supported_a.id,
    ]


@pytest.mark.django_db
def test_get_warehouse_context_rarity_upgrade_reads_source_keys_from_item_templates():
    user = User.objects.create_user(username="warehouse_rarity_payload_user", password="pass123")
    manor = ensure_manor(user)

    supported = GuestTemplate.objects.create(
        key="warehouse_rarity_payload_src",
        name="升阶测试门客",
        rarity="green",
        archetype="military",
    )
    unsupported = GuestTemplate.objects.create(
        key="warehouse_rarity_payload_other",
        name="普通门客",
        rarity="green",
        archetype="civil",
    )

    supported_guest = Guest.objects.create(manor=manor, template=supported, status=GuestStatus.IDLE, level=30)
    Guest.objects.create(manor=manor, template=supported, status=GuestStatus.INJURED, level=20)
    Guest.objects.create(manor=manor, template=unsupported, status=GuestStatus.IDLE, level=40)

    upgrade_token = ItemTemplate.objects.create(
        key="warehouse_rarity_upgrade_token_test",
        name="升阶残卷测试",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "upgrade_guest_rarity",
            "source_template_keys": ["warehouse_rarity_payload_src"],
            "target_template_map": {
                "warehouse_rarity_payload_src": "warehouse_rarity_payload_target",
            },
        },
    )
    InventoryItem.objects.create(manor=manor, template=upgrade_token, quantity=1)

    context = get_warehouse_context(manor, current_tab="warehouse", selected_category="all", page=1)
    assert [guest.id for guest in context["guests_for_rarity_upgrade"]] == [supported_guest.id]


@pytest.mark.django_db
def test_get_warehouse_context_rarity_upgrade_reads_source_rarity_from_item_templates():
    user = User.objects.create_user(username="warehouse_rarity_source_rarity_user", password="pass123")
    manor = ensure_manor(user)
    green_template = GuestTemplate.objects.create(
        key="warehouse_rarity_source_rarity_green",
        name="绿色门客",
        rarity="green",
        archetype="civil",
    )
    blue_template = GuestTemplate.objects.create(
        key="warehouse_rarity_source_rarity_blue",
        name="蓝色门客",
        rarity="blue",
        archetype="civil",
    )
    eligible_guest = Guest.objects.create(manor=manor, template=green_template, status=GuestStatus.IDLE, level=12)
    Guest.objects.create(manor=manor, template=green_template, status=GuestStatus.INJURED, level=20)
    Guest.objects.create(manor=manor, template=blue_template, status=GuestStatus.IDLE, level=20)

    pill_template = ItemTemplate.objects.create(
        key="warehouse_rarity_source_rarity_pill",
        name="绿转蓝丸子",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "upgrade_guest_rarity",
            "source_rarity": "green",
            "target_rarity": "blue",
        },
    )
    InventoryItem.objects.create(manor=manor, template=pill_template, quantity=1)

    context = get_warehouse_context(manor, current_tab="warehouse", selected_category="all", page=1)

    assert [guest.id for guest in context["guests_for_rarity_upgrade"]] == [eligible_guest.id]


@pytest.mark.django_db
def test_get_warehouse_context_blue_to_purple_pill_selects_idle_blue_guests():
    user = User.objects.create_user(username="warehouse_blue_to_purple_user", password="pass123")
    manor = ensure_manor(user)
    blue_template = GuestTemplate.objects.create(
        key="warehouse_blue_to_purple_source",
        name="蓝色门客",
        rarity="blue",
        archetype="civil",
    )
    green_template = GuestTemplate.objects.create(
        key="warehouse_blue_to_purple_other",
        name="绿色门客",
        rarity="green",
        archetype="civil",
    )
    eligible_guest = Guest.objects.create(manor=manor, template=blue_template, status=GuestStatus.IDLE, level=20)
    Guest.objects.create(manor=manor, template=blue_template, status=GuestStatus.INJURED, level=20)
    Guest.objects.create(manor=manor, template=green_template, status=GuestStatus.IDLE, level=20)

    pill_template = ItemTemplate.objects.create(
        key="warehouse_blue_to_purple_pill",
        name="蓝转紫丸子",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "upgrade_guest_rarity",
            "source_rarity": "blue",
            "target_rarity": "purple",
        },
    )
    InventoryItem.objects.create(manor=manor, template=pill_template, quantity=1)

    context = get_warehouse_context(manor, current_tab="warehouse", selected_category="all", page=1)

    assert [guest.id for guest in context["guests_for_rarity_upgrade"]] == [eligible_guest.id]


@pytest.mark.django_db
def test_get_warehouse_context_rarity_pills_explain_non_recruitable_and_previously_used_guests():
    user = User.objects.create_user(username="warehouse_pill_restrictions", password="pass123")
    manor = ensure_manor(user)
    recruitable = GuestTemplate.objects.create(key="pill_recruitable", name="可招募门客", rarity="blue")
    special = GuestTemplate.objects.create(
        key="pill_non_recruitable", name="非招募门客", rarity="blue", recruitable=False
    )
    converted = GuestTemplate.objects.create(
        key=f"guest_rarity_conversion_blue_{recruitable.pk}", name="已升阶门客", rarity="blue", recruitable=False
    )
    available_guest = Guest.objects.create(manor=manor, template=recruitable, status=GuestStatus.IDLE)
    special_guest = Guest.objects.create(manor=manor, template=special, status=GuestStatus.IDLE)
    used_guest = Guest.objects.create(manor=manor, template=recruitable, status=GuestStatus.IDLE, rarity_pill_used=True)
    legacy_guest = Guest.objects.create(manor=manor, template=converted, status=GuestStatus.IDLE)
    pill = ItemTemplate.objects.create(
        key="pill_restrictions",
        name="蓝转紫丸子",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={"action": "upgrade_guest_rarity", "source_rarity": "blue", "target_rarity": "purple"},
    )
    InventoryItem.objects.create(manor=manor, template=pill, quantity=1)

    context = get_warehouse_context(manor, current_tab="warehouse", selected_category="all")
    reasons = {guest.pk: guest.rarity_upgrade_block_reason for guest in context["guests_for_rarity_upgrade"]}

    assert reasons[available_guest.pk] is None
    assert "非招募门客，无法使用" in reasons[special_guest.pk]
    assert "已使用过升阶丸子" in reasons[used_guest.pk]
    assert "已使用过升阶丸子" in reasons[legacy_guest.pk]


@pytest.mark.django_db
def test_get_warehouse_context_soul_fusion_list_contains_idle_and_vacation_high_level_guests():
    user = User.objects.create_user(username="warehouse_soul_fusion_user", password="pass123")
    manor = ensure_manor(user)

    green_tpl = GuestTemplate.objects.create(
        key="warehouse_soul_green", name="绿门客", rarity="green", archetype="military"
    )
    blue_tpl = GuestTemplate.objects.create(key="warehouse_soul_blue", name="蓝门客", rarity="blue", archetype="civil")
    purple_tpl = GuestTemplate.objects.create(
        key="warehouse_soul_purple",
        name="紫门客",
        rarity="purple",
        archetype="military",
    )
    gray_tpl = GuestTemplate.objects.create(key="warehouse_soul_gray", name="灰门客", rarity="gray", archetype="civil")

    eligible_high = Guest.objects.create(manor=manor, template=purple_tpl, status=GuestStatus.IDLE, level=70)
    eligible_mid = Guest.objects.create(manor=manor, template=green_tpl, status=GuestStatus.IDLE, level=45)
    vacation_guest = Guest.objects.create(manor=manor, template=blue_tpl, status=GuestStatus.VACATION, level=50)
    Guest.objects.create(manor=manor, template=blue_tpl, status=GuestStatus.INJURED, level=80)
    Guest.objects.create(manor=manor, template=blue_tpl, status=GuestStatus.IDLE, level=29)
    Guest.objects.create(manor=manor, template=gray_tpl, status=GuestStatus.IDLE, level=90)

    soul_container = ItemTemplate.objects.create(
        key="warehouse_soul_container_test",
        name="灵魂容器",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "soul_fusion",
            "min_level": 30,
            "allowed_rarities": ["green", "blue", "purple"],
        },
    )
    InventoryItem.objects.create(manor=manor, template=soul_container, quantity=1)

    context = get_warehouse_context(manor, current_tab="warehouse", selected_category="all", page=1)
    assert [guest.id for guest in context["guests_for_soul_fusion"]] == [
        eligible_high.id,
        vacation_guest.id,
        eligible_mid.id,
    ]


@pytest.mark.django_db
def test_get_warehouse_context_soul_fusion_reads_requirements_from_item_templates():
    user = User.objects.create_user(username="warehouse_soul_payload_user", password="pass123")
    manor = ensure_manor(user)

    blue_tpl = GuestTemplate.objects.create(
        key="warehouse_payload_blue", name="蓝门客", rarity="blue", archetype="civil"
    )
    green_tpl = GuestTemplate.objects.create(
        key="warehouse_payload_green",
        name="绿门客",
        rarity="green",
        archetype="military",
    )
    supported_guest = Guest.objects.create(manor=manor, template=blue_tpl, status=GuestStatus.IDLE, level=60)
    Guest.objects.create(manor=manor, template=blue_tpl, status=GuestStatus.IDLE, level=59)
    Guest.objects.create(manor=manor, template=green_tpl, status=GuestStatus.IDLE, level=90)

    soul_container = ItemTemplate.objects.create(
        key="warehouse_soul_payload_container",
        name="蓝魂容器",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "soul_fusion",
            "min_level": 60,
            "allowed_rarities": ["blue"],
        },
    )
    InventoryItem.objects.create(manor=manor, template=soul_container, quantity=1)

    context = get_warehouse_context(manor, current_tab="warehouse", selected_category="all", page=1)
    assert [guest.id for guest in context["guests_for_soul_fusion"]] == [supported_guest.id]


@pytest.mark.django_db
def test_get_warehouse_context_soul_fusion_merges_multiple_container_requirements_for_client_filtering():
    user = User.objects.create_user(username="warehouse_soul_payload_merge_user", password="pass123")
    manor = ensure_manor(user)

    green_tpl = GuestTemplate.objects.create(
        key="warehouse_merge_green", name="绿门客", rarity="green", archetype="military"
    )
    blue_tpl = GuestTemplate.objects.create(key="warehouse_merge_blue", name="蓝门客", rarity="blue", archetype="civil")
    green_guest = Guest.objects.create(manor=manor, template=green_tpl, status=GuestStatus.IDLE, level=40)
    blue_guest = Guest.objects.create(manor=manor, template=blue_tpl, status=GuestStatus.IDLE, level=68)

    green_container = ItemTemplate.objects.create(
        key="warehouse_merge_green_container",
        name="青魂容器",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "soul_fusion",
            "min_level": 30,
            "allowed_rarities": ["green"],
        },
    )
    blue_container = ItemTemplate.objects.create(
        key="warehouse_merge_blue_container",
        name="玄魂容器",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "soul_fusion",
            "min_level": 60,
            "allowed_rarities": ["blue"],
        },
    )
    InventoryItem.objects.create(manor=manor, template=green_container, quantity=1)
    InventoryItem.objects.create(manor=manor, template=blue_container, quantity=1)

    context = get_warehouse_context(manor, current_tab="warehouse", selected_category="all", page=1)
    assert [guest.id for guest in context["guests_for_soul_fusion"]] == [blue_guest.id, green_guest.id]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("current_tab", "storage_location"),
    [
        ("warehouse", InventoryItem.StorageLocation.WAREHOUSE),
        ("treasury", InventoryItem.StorageLocation.TREASURY),
    ],
)
def test_get_warehouse_context_paginates_items_to_twenty(current_tab, storage_location):
    user = User.objects.create_user(username=f"warehouse_page_{current_tab}", password="pass123")
    manor = ensure_manor(user)

    for idx in range(21):
        template = ItemTemplate.objects.create(
            key=f"warehouse_page_{current_tab}_{idx}",
            name=f"分页物品{idx:02d}",
        )
        InventoryItem.objects.create(manor=manor, template=template, quantity=1, storage_location=storage_location)

    first_page = get_warehouse_context(manor, current_tab=current_tab, selected_category="all", page=1)
    second_page = get_warehouse_context(manor, current_tab=current_tab, selected_category="all", page=2)

    expected_total = 22 if storage_location == InventoryItem.StorageLocation.WAREHOUSE else 21
    assert len(first_page["inventory_items"]) == 20
    assert first_page["pagination"]["total_count"] == expected_total
    assert first_page["pagination"]["total_pages"] == 2
    assert first_page["pagination"]["has_next"] is True
    assert len(second_page["inventory_items"]) == expected_total - 20


@pytest.mark.django_db
def test_get_warehouse_context_groups_loot_box_and_resource_pack_under_tool_category():
    user = User.objects.create_user(username="warehouse_loot_box_tool_category", password="pass123")
    manor = ensure_manor(user)

    loot_box = ItemTemplate.objects.create(
        key="warehouse_work_chest",
        name="打工宝箱",
        effect_type=ItemTemplate.EffectType.LOOT_BOX,
        is_usable=True,
    )
    resource_pack = ItemTemplate.objects.create(
        key="warehouse_resource_pack",
        name="资源包",
        effect_type=ItemTemplate.EffectType.RESOURCE_PACK,
        is_usable=True,
    )
    InventoryItem.objects.create(manor=manor, template=loot_box, quantity=1)
    InventoryItem.objects.create(manor=manor, template=resource_pack, quantity=1)

    context = get_warehouse_context(
        manor,
        current_tab="warehouse",
        selected_category=ItemTemplate.EffectType.RESOURCE_PACK,
        page=1,
    )

    assert [item.template.key for item in context["inventory_items"]] == [
        "warehouse_work_chest",
        "custom_guest_card",
        "warehouse_resource_pack",
    ]
    assert all(item.category_display == "道具" for item in context["inventory_items"])
    assert context["selected_category"] == "tool"
    assert any(category["key"] == "tool" for category in context["categories"])
    assert all(category["key"] != ItemTemplate.EffectType.LOOT_BOX for category in context["categories"])
    assert all(category["key"] != ItemTemplate.EffectType.RESOURCE_PACK for category in context["categories"])


@pytest.mark.django_db
@pytest.mark.parametrize("storage_location", ["warehouse", "treasury"])
def test_task_chest_category_separates_versions_and_preserves_pagination(storage_location):
    user = User.objects.create_user(username=f"task_chest_category_{storage_location}")
    manor = ensure_manor(user)
    chest_keys = set()
    for index in range(21):
        key = f"mission_chest_{'v3_' if index % 2 else ''}category_{index}"
        chest_keys.add(key)
        template = ItemTemplate.objects.create(
            key=key, name=f"任务宝箱{index:02d}", effect_type=ItemTemplate.EffectType.LOOT_BOX, is_usable=True
        )
        InventoryItem.objects.create(manor=manor, template=template, quantity=1, storage_location=storage_location)
    for key in [
        "mid_autumn_mooncake",
        "passive_skill_book_box",
        "civil_skill_book_box",
        "saiwai_equipment_box",
        "fengshen_equipment_box",
        "huashan_wulin_chest",
    ]:
        chest_keys.add(key)
        template = ItemTemplate.objects.create(key=key, name=key, effect_type="loot_box", is_usable=True)
        InventoryItem.objects.create(manor=manor, template=template, quantity=1, storage_location=storage_location)
    tool_keys = {
        "work_chest_small",
        "work_chest_medium",
        "work_chest_large",
        "guild_gear_box_master",
        "category_resource_pack",
        "mission_chest_not_a_box",
    }
    for key in tool_keys:
        effect_type = (
            ItemTemplate.EffectType.RESOURCE_PACK
            if key == "category_resource_pack"
            else ItemTemplate.EffectType.TOOL if key == "mission_chest_not_a_box" else ItemTemplate.EffectType.LOOT_BOX
        )
        template = ItemTemplate.objects.create(key=key, name=key, effect_type=effect_type)
        InventoryItem.objects.create(manor=manor, template=template, quantity=1, storage_location=storage_location)
    empty = ItemTemplate.objects.create(key="mission_chest_empty", name="已用完", effect_type="loot_box")
    InventoryItem.objects.create(manor=manor, template=empty, quantity=0, storage_location=storage_location)

    first = get_warehouse_context(manor, storage_location, "mission_chest", page=1)
    second = get_warehouse_context(manor, storage_location, "mission_chest", page=2)
    displayed = first["inventory_items"] + second["inventory_items"]
    assert {item.template.key for item in displayed} == chest_keys
    assert all(item.category_display == "任务宝箱" and item.template.is_usable for item in displayed)
    assert first["selected_category"] == "mission_chest"
    assert first["pagination"]["total_count"] == 27
    assert len(first["inventory_items"]) == 20 and len(second["inventory_items"]) == 7
    assert {"key": "mission_chest", "label": "任务宝箱"} in first["categories"]
    expected_tool_keys = tool_keys | ({"custom_guest_card"} if storage_location == "warehouse" else set())
    for category in ["tool", "tools", "loot_box", "resource_pack"]:
        tools = get_warehouse_context(manor, storage_location, category)
        assert {item.template.key for item in tools["inventory_items"]} == expected_tool_keys
        assert all(item.category_display == "道具" for item in tools["inventory_items"])


@pytest.mark.django_db
def test_empty_task_chest_category_stays_selected_without_changing_inventory(client, django_user_model):
    from django.urls import reverse

    user = django_user_model.objects.create_user(username="empty_task_chest_category")
    manor = ensure_manor(user)
    client.force_login(user)
    count_before = InventoryItem.objects.filter(manor=manor).count()
    response = client.get(reverse("gameplay:warehouse"), {"category": "mission_chest"})
    assert response.status_code == 200
    assert response.context["selected_category"] == "mission_chest"
    assert not response.context["inventory_items"]
    assert response.context["pagination"]["total_count"] == 0
    assert '<option value="mission_chest" selected>任务宝箱</option>' in response.content.decode()
    assert InventoryItem.objects.filter(manor=manor).count() == count_before
