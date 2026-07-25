import ast
import re
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.exceptions import (
    BuildingMaxLevelError,
    BuildingNotFoundError,
    GameError,
    GuestNotIdleError,
    GuestNotRequirementError,
    InsufficientResourceError,
    InsufficientSpaceError,
    ItemNotConfiguredError,
    NoTemplateAvailableError,
    TaskDispatchError,
    TaskRescheduleError,
    TechnologyNotFoundError,
    TroopRecruitmentNotFoundError,
    TroopRecruitmentNotReadyError,
    TroopTemplateNotFoundError,
)
from core.utils import require_positive_int


def test_guest_not_idle_error_localizes_arena_status():
    guest = SimpleNamespace(display_name="蜡笔小新", status="arena")

    error = GuestNotIdleError(guest)

    assert str(error) == "蜡笔小新 当前状态为「竞技中」，无法执行此操作"


def test_guest_not_idle_error_hides_unknown_internal_status():
    guest = SimpleNamespace(display_name="蜡笔小新", status="future_internal_status")

    error = GuestNotIdleError(guest)

    assert str(error) == "蜡笔小新 当前状态为「未知状态」，无法执行此操作"


@pytest.mark.parametrize(
    ("error", "expected_message"),
    [
        (BuildingMaxLevelError("城墙", 10), "城墙已达到最大等级（等级10）"),
        (BuildingNotFoundError("future_building"), "该建筑尚未建造"),
        (
            GuestNotRequirementError(
                SimpleNamespace(display_name="蜡笔小新"),
                "future_attribute",
                30,
                10,
            ),
            "蜡笔小新 相关属性不足，需要 30，当前 10",
        ),
        (InsufficientResourceError("future_resource", 20, 5), "资源不足，需要 20，当前 5"),
        (InsufficientSpaceError("future_location", 2, 8), "对应位置空间不足，剩余空间：2，需要空间：8"),
        (ItemNotConfiguredError("effect_payload 配置异常"), "物品配置异常，请联系管理员"),
        (NoTemplateAvailableError(), "缺少可用的门客模板，请联系管理员"),
        (TechnologyNotFoundError("future_technology"), "未找到对应科技"),
        (TroopRecruitmentNotFoundError(recruitment_id=123), "募兵记录不存在"),
        (
            TroopRecruitmentNotReadyError(complete_at="2026-04-09T00:00:00"),
            "募兵尚未完成",
        ),
        (TroopTemplateNotFoundError("future_troop"), "战斗兵种配置不存在"),
        (TaskDispatchError("complete_guest_training failed"), "任务调度失败，请稍后重试"),
        (TaskRescheduleError("complete_guest_training failed"), "任务需要重新调度，请稍后重试"),
    ],
)
def test_business_errors_hide_internal_english(error, expected_message):
    assert str(error) == expected_message


def test_internal_contract_error_keeps_diagnostics_out_of_player_message():
    with pytest.raises(GameError) as exc_info:
        require_positive_int(None, contract_name="battle troop strength")

    assert str(exc_info.value) == "数据异常，请稍后重试"
    assert exc_info.value.context == {
        "contract_name": "battle troop strength",
        "invalid_value": None,
    }


def test_resource_reward_message_hides_unknown_resource_key():
    from gameplay.services.inventory.use import _format_resource_parts

    assert _format_resource_parts({"future_resource": 3}) == ["未知资源+3"]


def test_forge_success_messages_hide_unknown_item_keys(monkeypatch):
    from gameplay.views import production_forge_handlers as forge_handlers

    class EmptyTemplateQuery(list):
        def only(self, *_fields):
            return self

    monkeypatch.setattr(
        forge_handlers,
        "ItemTemplate",
        SimpleNamespace(objects=SimpleNamespace(filter=lambda **_kwargs: EmptyTemplateQuery())),
    )

    assert forge_handlers._build_decompose_reward_text({"rewards": {"future_material": 2}}) == "，获得：未知物品×2"
    assert (
        forge_handlers._build_start_equipment_forging_success_message(
            SimpleNamespace(equipment_name="测试装备", quantity=2, actual_duration=30)
        )
        == "测试装备×2 开始锻造，预计 30 秒后完成"
    )
    assert (
        forge_handlers._build_blueprint_synthesize_success_message({"result_name": "测试装备", "quantity": 2})
        == "测试装备×2 合成完成"
    )


def test_message_attachment_labels_hide_unknown_keys(monkeypatch):
    from gameplay.utils import template_loader
    from gameplay.views.messages import _build_attachment_details, _format_claimed_summary

    monkeypatch.setattr(template_loader, "get_item_templates_by_keys", lambda _keys: {})
    message = SimpleNamespace(
        has_attachments=True,
        is_claimed=False,
        attachments={
            "resources": {"future_resource": 2},
            "items": {"future_item": 3},
        },
    )

    details = _build_attachment_details(message)
    assert details["resources"][0]["name"] == "未知资源"
    assert details["items"][0]["name"] == "未知物品"

    summary, payload = _format_claimed_summary({"future_resource": 2, "item_future_item": 3})
    assert summary == "未知资源×2、未知物品×3"
    assert [entry["name"] for entry in payload] == ["未知资源", "未知物品"]


def test_loot_box_reward_message_hides_unknown_item_key(monkeypatch):
    from gameplay.services.inventory import use as inventory_use

    empty_query = SimpleNamespace(first=lambda: None)
    monkeypatch.setattr(
        inventory_use,
        "ItemTemplate",
        SimpleNamespace(objects=SimpleNamespace(filter=lambda **_kwargs: empty_query)),
    )
    monkeypatch.setattr(inventory_use, "add_item_to_inventory", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(inventory_use.inventory_random, "randint", lambda _minimum, _maximum: 2)
    item = SimpleNamespace(
        id=1,
        manor=SimpleNamespace(id=1),
        template=SimpleNamespace(
            name="测试宝箱",
            effect_payload={"item_rewards": [{"item_key": "future_item", "min_quantity": 2, "max_quantity": 2}]},
        ),
    )

    result = inventory_use._apply_loot_box(item)

    assert result["rewards"] == ["物品【未知物品】×2"]
    assert result["_message"] == "打开宝箱获得：物品【未知物品】×2"


def test_guild_technology_cost_hides_unknown_resource_key(monkeypatch):
    from guilds.views import technology

    monkeypatch.setattr(
        technology.technology_service,
        "calculate_tech_upgrade_cost",
        lambda *_args: {"future_resource": 2},
    )

    assert technology._format_upgrade_cost(SimpleNamespace(tech_key="future_technology", level=0)) == "未知资源 ×2"


def test_guild_warehouse_error_hides_unknown_item_key(monkeypatch):
    from guilds.services import warehouse

    locked_items = SimpleNamespace(filter=lambda **_kwargs: [])
    warehouse_model = SimpleNamespace(objects=SimpleNamespace(select_for_update=lambda: locked_items))
    monkeypatch.setattr(warehouse, "GuildWarehouse", warehouse_model)

    with pytest.raises(GameError) as exc_info:
        warehouse.spend_guild_warehouse_items_locked(
            SimpleNamespace(),
            {"future_item": 2},
        )

    assert str(exc_info.value) == "帮会对应物品不足，需要2"


def test_guild_raid_summary_hides_unknown_item_key(monkeypatch):
    from guilds.services import guild_raid_messages

    monkeypatch.setattr(
        guild_raid_messages,
        "get_item_template_names_by_keys",
        lambda _keys: {},
        raising=False,
    )

    assert guild_raid_messages._format_item_summary({"future_item": 2}) == "未知物品 ×2"


def test_raid_reward_descriptions_hide_unknown_item_keys(monkeypatch):
    from gameplay.services.raid.combat import loot

    class EmptyTemplateQuery(list):
        def only(self, *_fields):
            return self

    monkeypatch.setattr(
        loot,
        "ItemTemplate",
        SimpleNamespace(objects=SimpleNamespace(filter=lambda **_kwargs: EmptyTemplateQuery())),
    )

    assert loot._format_loot_description({}, {"future_item": 2}) == "未知物品 ×2"
    assert (
        loot._format_battle_rewards_description({"exp_fruit": 3, "equipment": {"future_equipment": 2}})
        == "经验果 ×3\n未知装备 ×2"
    )


def test_inventory_success_fallback_hides_result_field_names():
    from gameplay.views.inventory_action_support import build_inventory_use_success_message

    message = build_inventory_use_success_message(
        {"future_internal_field": 2},
        item_name="测试道具",
    )

    assert message == "测试道具 使用成功：效果已生效"


def test_arena_coop_settlement_uses_chinese_boss_label():
    from gameplay.services.arena.coop_settlement import send_coop_settlement_messages

    created_messages = []
    send_coop_settlement_messages(
        locked_event=SimpleNamespace(boss_name="测试首领"),
        locked_manor=SimpleNamespace(),
        contribution=SimpleNamespace(
            total_damage=2000,
            boss_damage=1500,
            damage_rank=1,
            total_coins=30,
            rare_drop_item_key="",
        ),
        report=SimpleNamespace(),
        create_message_fn=lambda **kwargs: created_messages.append(kwargs),
    )

    assert "首领伤害 1500" in created_messages[1]["body"]


def test_spectator_battle_report_title_uses_chinese_separator():
    from battle.view_helpers import build_report_title

    report = SimpleNamespace(
        manor=SimpleNamespace(display_name="进攻庄园"),
        opponent_name="防守庄园",
    )

    assert (
        build_report_title(
            report,
            player_side="spectator",
            viewer_manor_id=999,
        )
        == "进攻庄园 对阵 防守庄园 战报"
    )


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLAYER_SOURCE_ROOTS = (
    "battle",
    "core",
    "gameplay",
    "guests",
    "guilds",
    "trade",
    "websocket",
)
ASCII_LETTER_RE = re.compile(r"[A-Za-z]")
MESSAGE_METHODS = {"error", "warning", "info", "success"}
INTERNAL_IDENTIFIER_RE = re.compile(
    r"\b(?:[A-Za-z_]+_key|status|rarity|resource_type|action|invalid_keys|invalid_nonzero|normalized_key)\b|\.key\b"
)
PLAYER_UI_ROOTS = (
    PROJECT_ROOT / "templates",
    PROJECT_ROOT / "battle" / "templates",
    PROJECT_ROOT / "gameplay" / "templates",
    PROJECT_ROOT / "guests" / "templates",
    PROJECT_ROOT / "guilds" / "templates",
    PROJECT_ROOT / "trade" / "templates",
    PROJECT_ROOT / "static" / "js",
)
PLAYER_TEMPLATE_ROOTS = tuple(root for root in PLAYER_UI_ROOTS if root.name == "templates")
PLAYER_UI_ENGLISH_SHORTHAND_RE = re.compile(r"PVP|Boss|BOSS|HP|Avatar|\bLv\.?|(?<![A-Za-z])x(?=\{\{|\$\{)")
DJANGO_TEMPLATE_TOKEN_RE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.DOTALL)
DJANGO_TEMPLATE_COMMENT_BLOCK_RE = re.compile(
    r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}",
    re.DOTALL,
)
DJANGO_TEMPLATE_CONTROL_TOKEN_RE = re.compile(r"\{%.*?%\}|\{#.*?#\}", re.DOTALL)
DJANGO_TEMPLATE_VARIABLE_RE = re.compile(r"\{\{\s*(.*?)\s*\}\}", re.DOTALL)
INTERNAL_KEY_EXPRESSION_RE = re.compile(r"(?:[A-Za-z_]\w*\.)*(?:key|[A-Za-z_]\w*_key)")
UNSAFE_TEMPLATE_KEY_FALLBACK_RE = re.compile(r"\|default(?:_if_none)?:\s*(?:[A-Za-z_]\w*\.)*(?:key|[A-Za-z_]\w*_key)\b")
VISIBLE_TEXT_ATTRIBUTES = {"title", "alt", "placeholder", "aria-label"}
PRESENTATION_FALLBACK_FILES = (
    "battle/troops.py",
    "battle/view_helpers.py",
    "gameplay/models/items.py",
    "gameplay/selectors/arena/common.py",
    "gameplay/selectors/home.py",
    "gameplay/selectors/troop_recruitment.py",
    "gameplay/services/arena/coop_battle.py",
    "gameplay/services/arena/coop_settlement.py",
    "gameplay/services/arena/rewards.py",
    "gameplay/services/buildings/forge_blueprints.py",
    "gameplay/services/buildings/forge_flow_helpers.py",
    "gameplay/services/buildings/forge_helpers.py",
    "gameplay/services/buildings/forge_runtime.py",
    "gameplay/services/buildings/ranch.py",
    "gameplay/services/buildings/smithy.py",
    "gameplay/services/buildings/stable.py",
    "gameplay/services/recruitment/recruitment.py",
    "gameplay/services/recruitment/templates.py",
    "gameplay/services/technology_helpers.py",
    "gameplay/templatetags/gameplay_extras.py",
    "gameplay/views/mission_helpers.py",
    "gameplay/views/mission_page_context.py",
    "guests/templatetags/guest_extras.py",
    "guilds/templatetags/guild_extras.py",
    "trade/bank_context_builder.py",
    "trade/selector_builders.py",
)
UNSAFE_DISPLAY_FALLBACK_RE = re.compile(
    r"\.get\(\s*(?P<key>[A-Za-z_]\w*)\s*,\s*(?P=key)\s*\)"
    r"|\.get\(\s*['\"](?:name|label)['\"]\s*,\s*(?:[A-Za-z_]\w*_key|key)\s*\)"
    r"|getattr\([^\n]+,\s*['\"]name['\"]\s*,\s*(?:[A-Za-z_]\w*_key|key)\s*\)"
    r"|\bor\s+(?:[A-Za-z_]\w*(?<!_by)_key|key)\b(?!\s+in\b)"
)


def _node_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _static_text(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(_static_text(part) for part in node.values)
    return ""


def _assignment_target_names(node):
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return {_node_name(target) for target in targets}


def _mask_django_template_tokens(source):
    def _mask(match):
        return "".join("\n" if char == "\n" else " " for char in match.group())

    source = DJANGO_TEMPLATE_COMMENT_BLOCK_RE.sub(_mask, source)
    return DJANGO_TEMPLATE_TOKEN_RE.sub(_mask, source)


def _mask_django_template_control_tokens(source):
    def _mask(match):
        return "".join("\n" if char == "\n" else " " for char in match.group())

    source = DJANGO_TEMPLATE_COMMENT_BLOCK_RE.sub(_mask, source)
    return DJANGO_TEMPLATE_CONTROL_TOKEN_RE.sub(_mask, source)


class _VisibleTemplateCopyParser(HTMLParser):
    def __init__(self, relative_path):
        super().__init__(convert_charrefs=True)
        self.relative_path = relative_path
        self.issues = []
        self._hidden_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self._hidden_depth += 1
            return
        if self._hidden_depth:
            return

        attributes = dict(attrs)
        visible_attributes = set(VISIBLE_TEXT_ATTRIBUTES)
        if tag == "input" and attributes.get("type", "").lower() in {"button", "reset", "submit"}:
            visible_attributes.add("value")
        for attribute in visible_attributes:
            value = attributes.get(attribute) or ""
            if ASCII_LETTER_RE.search(value):
                self._record(f'{attribute}="{value}"')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in {"script", "style"}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self._hidden_depth:
            self._hidden_depth -= 1

    def handle_data(self, data):
        if self._hidden_depth:
            return
        text = " ".join(data.split())
        if text and ASCII_LETTER_RE.search(text):
            self._record(text)

    def _record(self, text):
        line_number, _column = self.getpos()
        self.issues.append(f"{self.relative_path}:{line_number}: {text}")


class _VisibleTemplateVariableParser(HTMLParser):
    def __init__(self, relative_path):
        super().__init__(convert_charrefs=True)
        self.relative_path = relative_path
        self.issues = []
        self._hidden_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self._hidden_depth += 1
            return
        if self._hidden_depth:
            return
        attributes = dict(attrs)
        visible_attributes = set(VISIBLE_TEXT_ATTRIBUTES)
        if tag == "input" and attributes.get("type", "").lower() in {"button", "reset", "submit"}:
            visible_attributes.add("value")
        for attribute in visible_attributes:
            self._inspect(attributes.get(attribute) or "")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in {"script", "style"}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self._hidden_depth:
            self._hidden_depth -= 1

    def handle_data(self, data):
        if not self._hidden_depth:
            self._inspect(data)

    def _inspect(self, value):
        for match in DJANGO_TEMPLATE_VARIABLE_RE.finditer(value):
            expression = match.group(1).strip()
            base_expression, separator, filters = expression.partition("|")
            if not INTERNAL_KEY_EXPRESSION_RE.fullmatch(base_expression.strip()):
                continue
            filter_names = {part.strip().split(":", 1)[0] for part in filters.split("|") if part.strip()}
            if separator and filter_names - {"default", "default_if_none", "escape", "escapejs", "safe"}:
                continue
            line_number, _column = self.getpos()
            self.issues.append(f"{self.relative_path}:{line_number}: {expression}")


def _load_player_source_trees():
    trees = []
    for root_name in PLAYER_SOURCE_ROOTS:
        for path in (PROJECT_ROOT / root_name).rglob("*.py"):
            if "migrations" in path.parts or "management" in path.parts or "admin" in path.parts:
                continue
            trees.append((path, ast.parse(path.read_text(encoding="utf-8"))))
    return trees


def _game_error_metadata(trees):
    class_nodes = {}
    class_bases = {}
    own_message_positions = {}
    for _path, tree in trees:
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            class_nodes[node.name] = node
            class_bases[node.name] = {_node_name(base) for base in node.bases}
            initializer = next(
                (
                    child
                    for child in node.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == "__init__"
                ),
                None,
            )
            if initializer is None:
                continue
            positional = [*initializer.args.posonlyargs, *initializer.args.args]
            if positional and positional[0].arg == "self":
                positional = positional[1:]
            own_message_positions[node.name] = next(
                (index for index, argument in enumerate(positional) if argument.arg == "message"),
                None,
            )

    error_classes = {"GameError"}
    changed = True
    while changed:
        changed = False
        for class_name, bases in class_bases.items():
            if class_name not in error_classes and bases & error_classes:
                error_classes.add(class_name)
                changed = True

    message_positions = {"GameError": 0}
    unresolved = error_classes - {"GameError"}
    while unresolved:
        progressed = False
        for class_name in tuple(unresolved):
            if class_name in own_message_positions:
                message_positions[class_name] = own_message_positions[class_name]
                unresolved.remove(class_name)
                progressed = True
                continue
            inherited = next(
                (message_positions[base] for base in class_bases.get(class_name, ()) if base in message_positions),
                None,
            )
            if inherited is not None:
                message_positions[class_name] = inherited
                unresolved.remove(class_name)
                progressed = True
        if not progressed:
            break
    return error_classes, message_positions, class_nodes


def _dict_value(node, keys):
    if not isinstance(node, ast.Dict):
        return []
    values = []
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and key.value in keys:
            values.append(value)
    return values


def _player_message_nodes(call):
    func_name = _node_name(call.func)
    nodes = []
    if (
        isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "messages"
        and func_name in MESSAGE_METHODS
        and len(call.args) >= 2
    ):
        nodes.append(call.args[1])
    elif func_name == "json_error" and call.args:
        nodes.append(call.args[0])
    elif func_name == "json_success":
        nodes.extend(keyword.value for keyword in call.keywords if keyword.arg == "message")
    elif func_name == "JsonResponse" and call.args:
        nodes.extend(_dict_value(call.args[0], {"error", "message"}))
    elif func_name == "send_json" and call.args:
        nodes.extend(_dict_value(call.args[0], {"message"}))
    elif func_name in {"create_message", "safe_create_message"}:
        nodes.extend(keyword.value for keyword in call.keywords if keyword.arg in {"title", "body"})
        if len(call.args) >= 3:
            nodes.append(call.args[2])
        if len(call.args) >= 4:
            nodes.append(call.args[3])
    return nodes


def test_player_facing_python_literals_do_not_contain_english():
    trees = _load_player_source_trees()
    error_classes, message_positions, class_nodes = _game_error_metadata(trees)
    issues = []

    for class_name in error_classes:
        class_node = class_nodes.get(class_name)
        if class_node is None:
            continue
        for node in ast.walk(class_node):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            if not _assignment_target_names(node) & {"default_message", "message", "resolved_message"}:
                continue
            text = _static_text(node.value)
            if ASCII_LETTER_RE.search(text):
                issues.append(f"{class_name}:{node.lineno}: {text}")

    for path, tree in trees:
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            candidates = _player_message_nodes(call)
            error_name = _node_name(call.func)
            if error_name in error_classes:
                candidates.extend(keyword.value for keyword in call.keywords if keyword.arg == "message")
                message_position = message_positions.get(error_name)
                if isinstance(message_position, int) and len(call.args) > message_position:
                    candidates.append(call.args[message_position])
            for candidate in candidates:
                text = _static_text(candidate)
                if ASCII_LETTER_RE.search(text):
                    relative_path = path.relative_to(PROJECT_ROOT)
                    issues.append(f"{relative_path}:{call.lineno}: {text}")

    assert not issues, "玩家提示仍含英文：\n" + "\n".join(sorted(set(issues)))


def test_player_messages_do_not_interpolate_internal_identifiers():
    trees = _load_player_source_trees()
    error_classes, message_positions, _class_nodes = _game_error_metadata(trees)
    issues = []

    for path, tree in trees:
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            candidates = _player_message_nodes(call)
            error_name = _node_name(call.func)
            if error_name in error_classes:
                candidates.extend(keyword.value for keyword in call.keywords if keyword.arg == "message")
                message_position = message_positions.get(error_name)
                if isinstance(message_position, int) and len(call.args) > message_position:
                    candidates.append(call.args[message_position])
            for candidate in candidates:
                if not isinstance(candidate, ast.JoinedStr):
                    continue
                for part in candidate.values:
                    if not isinstance(part, ast.FormattedValue):
                        continue
                    expression = ast.unparse(part.value)
                    if INTERNAL_IDENTIFIER_RE.search(expression):
                        relative_path = path.relative_to(PROJECT_ROOT)
                        issues.append(f"{relative_path}:{call.lineno}: {expression}")

    assert not issues, "玩家提示仍直接插入内部标识：\n" + "\n".join(sorted(set(issues)))


def test_player_template_static_copy_does_not_contain_english():
    issues = []
    for root in PLAYER_TEMPLATE_ROOTS:
        for path in root.rglob("*.html"):
            parser = _VisibleTemplateCopyParser(path.relative_to(PROJECT_ROOT))
            parser.feed(_mask_django_template_tokens(path.read_text(encoding="utf-8")))
            issues.extend(parser.issues)

    assert not issues, "玩家模板静态文案仍含英文：\n" + "\n".join(issues)


def test_player_templates_do_not_render_internal_keys():
    issues = []
    for root in PLAYER_TEMPLATE_ROOTS:
        for path in root.rglob("*.html"):
            relative_path = path.relative_to(PROJECT_ROOT)
            source = path.read_text(encoding="utf-8")
            parser = _VisibleTemplateVariableParser(relative_path)
            parser.feed(_mask_django_template_control_tokens(source))
            issues.extend(parser.issues)
            for line_number, line in enumerate(source.splitlines(), start=1):
                match = UNSAFE_TEMPLATE_KEY_FALLBACK_RE.search(line)
                if match:
                    issues.append(f"{relative_path}:{line_number}: {match.group(0)}")

    assert not issues, "玩家模板仍直接展示内部键：\n" + "\n".join(issues)


def test_player_ui_copy_does_not_use_english_shorthand():
    issues = []
    for root in PLAYER_UI_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix not in {".html", ".js"} or "tests" in path.parts:
                continue
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                match = PLAYER_UI_ENGLISH_SHORTHAND_RE.search(line)
                if match:
                    issues.append(f"{path.relative_to(PROJECT_ROOT)}:{line_number}: {match.group(0)}")

    assert not issues, "玩家界面仍含英文简写：\n" + "\n".join(issues)


def test_presentation_code_does_not_fall_back_to_internal_keys():
    issues = []
    for relative_path in PRESENTATION_FALLBACK_FILES:
        path = PROJECT_ROOT / relative_path
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            match = UNSAFE_DISPLAY_FALLBACK_RE.search(line)
            if match:
                issues.append(f"{relative_path}:{line_number}: {match.group(0)}")

    assert not issues, "展示层仍使用内部键作为兜底：\n" + "\n".join(issues)
