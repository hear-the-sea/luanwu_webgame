"""
Battle Debugger Views

安全说明：
- 所有视图都需要 staff 权限
- 仅在 DEBUG 模式下可用
- 生产环境应禁用此模块
"""

import logging
import uuid
from functools import wraps

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from core.utils import safe_float, safe_int
from guests.services.custom_guest import CUSTOM_GUEST_TEMPLATE_KEYS

from .config import BattleConfig, BattleDebuggerInputError, ConfigLoader, GuestConfig, InvalidPresetError, PartyConfig
from .simulator import BattleSimulator

logger = logging.getLogger(__name__)

# 安全常量
MAX_REPEAT = 100  # 最大重复次数，防止 DoS
MAX_TUNE_VALUES = 20  # 调优参数最大数量


def _render_debugger_error(request, message: str):
    return render(request, "battle_debugger/error.html", {"error": message})


def _validate_config_or_raise(loader: ConfigLoader, config: BattleConfig) -> None:
    errors = loader.validate(config)
    if errors:
        raise BattleDebuggerInputError("；".join(errors))


def debug_only(view_func):
    """仅在 DEBUG 模式下允许访问的装饰器"""

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not settings.DEBUG:
            return HttpResponseForbidden("此功能仅在开发环境可用")
        return view_func(request, *args, **kwargs)

    return _wrapped


def debugger_view(view_func):
    """
    组合装饰器：登录 + staff + DEBUG 模式
    用于保护所有 battle_debugger 视图

    执行顺序：debug_only -> staff_member_required -> login_required -> view_func
    这样确保：1) 先检查登录 2) 再检查staff权限 3) 最后检查DEBUG模式
    """

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        return view_func(request, *args, **kwargs)

    # 正确的装饰器顺序：从内到外应用
    # 最终执行顺序是 login_required -> staff_member_required -> debug_only -> view
    _wrapped = debug_only(_wrapped)
    _wrapped = staff_member_required(_wrapped)
    _wrapped = login_required(_wrapped)
    return _wrapped


@debugger_view
def index(request):
    """首页 - 显示所有预设配置"""
    loader = ConfigLoader()
    presets = loader.list_presets()

    preset_info = []
    for preset_name in presets:
        info = loader.get_preset_info(preset_name)
        preset_info.append(
            {
                "name": preset_name,
                "title": info.get("name", preset_name),
                "description": info.get("description", "无描述"),
            }
        )

    return render(
        request,
        "battle_debugger/index.html",
        {
            "presets": preset_info,
        },
    )


@debugger_view
def preset_detail(request, preset_name):
    """预设配置详情"""
    loader = ConfigLoader()

    try:
        config = loader.load_preset(preset_name)
        info = loader.get_preset_info(preset_name)
    except FileNotFoundError:
        return _render_debugger_error(request, f"预设配置不存在: {preset_name}")
    except InvalidPresetError as exc:
        return _render_debugger_error(request, str(exc))

    return render(
        request,
        "battle_debugger/preset_detail.html",
        {
            "preset_name": preset_name,
            "title": info.get("name", preset_name),
            "description": info.get("description", ""),
            "config": config,
        },
    )


@debugger_view
@require_http_methods(["GET", "POST"])
def simulate(request):
    """战斗模拟页面"""
    loader = ConfigLoader()

    if request.method == "GET":
        # 显示模拟表单
        presets = loader.list_presets()
        preset_info = [{"name": p, "title": loader.get_preset_info(p).get("name", p)} for p in presets]

        # 可调参数列表
        tunable_params = [
            {"key": "slaughter_multiplier", "label": "屠戮倍率", "default": 30, "min": 1, "max": 100},
            {
                "key": "troop_attack_divisor_vs_guest",
                "label": "小兵打门客攻击除数",
                "default": 4.0,
                "min": 1,
                "max": 10,
                "step": 0.1,
            },
            {
                "key": "troop_attack_divisor_vs_troop",
                "label": "小兵打小兵攻击除数",
                "default": 1.0,
                "min": 0.5,
                "max": 5,
                "step": 0.1,
            },
            {
                "key": "troop_defense_divisor",
                "label": "小兵防御除数",
                "default": 2.0,
                "min": 0.5,
                "max": 10,
                "step": 0.1,
            },
            {"key": "counter_multiplier", "label": "五行相克倍率", "default": 1.5, "min": 1, "max": 3, "step": 0.1},
            {"key": "crit_chance", "label": "暴击率", "default": 0.05, "min": 0, "max": 1, "step": 0.01},
        ]

        return render(
            request,
            "battle_debugger/simulate.html",
            {
                "presets": preset_info,
                "tunable_params": tunable_params,
            },
        )

    # POST - 运行模拟
    try:
        preset_name = request.POST.get("preset")
        seed = request.POST.get("seed")
        repeat = min(safe_int(request.POST.get("repeat", 1), default=1, min_val=1), MAX_REPEAT)  # 限制最大重复次数

        # 加载配置
        config = loader.load_preset(preset_name)

        # 应用参数覆盖
        overrides = {}
        for key in request.POST:
            if key.startswith("param_"):
                param_name = key[6:]  # 去掉 'param_' 前缀
                value = request.POST[key]
                if value:
                    parsed = safe_float(value, default=None)
                    if parsed is not None:
                        overrides[param_name] = parsed

        if overrides:
            config = loader.merge_config(config, overrides)
        _validate_config_or_raise(loader, config)

        # 运行模拟
        simulator = BattleSimulator(config)
        seed_value = safe_int(seed, default=None) if seed else None

        results = []
        for i in range(repeat):
            current_seed = seed_value + i if seed_value is not None else None
            result = simulator.run_battle(seed=current_seed)
            results.append(result)

        # 生成结果ID并缓存
        result_id = str(uuid.uuid4())
        cache.set(
            f"battle_result_{result_id}",
            {
                "config": config,
                "results": results,
                "preset_name": preset_name,
            },
            timeout=3600,
        )  # 1小时过期

        return redirect("battle_debugger:result_detail", result_id=result_id)

    except FileNotFoundError:
        logger.warning(f"预设配置不存在: {request.POST.get('preset')}")
        return _render_debugger_error(request, "预设配置不存在")
    except BattleDebuggerInputError as exc:
        logger.warning("战斗模拟输入无效: %s", exc)
        return _render_debugger_error(request, str(exc))


@debugger_view
def result_detail(request, result_id):
    """战斗结果详情"""
    # 从缓存获取结果
    data = cache.get(f"battle_result_{result_id}")

    if not data:
        return render(request, "battle_debugger/error.html", {"error": "结果不存在或已过期"})

    config = data["config"]
    results = data["results"]
    preset_name = data.get("preset_name", "")

    # 计算统计信息
    total = len(results)
    attacker_wins = sum(1 for r in results if r["winner"] == "attacker")
    defender_wins = total - attacker_wins

    stats = {
        "total": total,
        "attacker_wins": attacker_wins,
        "attacker_win_rate": attacker_wins / total * 100 if total > 0 else 0,
        "defender_wins": defender_wins,
        "defender_win_rate": defender_wins / total * 100 if total > 0 else 0,
        "avg_rounds": sum(len(r["combat_log"]) for r in results) / total if total > 0 else 0,
    }

    # 如果只有一次模拟，显示详细战斗日志
    detailed_log = None
    if total == 1:
        detailed_log = results[0]["combat_log"]

    return render(
        request,
        "battle_debugger/result_detail.html",
        {
            "result_id": result_id,
            "config": config,
            "results": results,
            "stats": stats,
            "detailed_log": detailed_log,
            "preset_name": preset_name,
        },
    )


@debugger_view
@require_http_methods(["GET", "POST"])
def tune(request):
    """参数调优页面"""
    loader = ConfigLoader()

    if request.method == "GET":
        # 显示调优表单
        presets = loader.list_presets()
        preset_info = [{"name": p, "title": loader.get_preset_info(p).get("name", p)} for p in presets]

        tunable_params = [
            {"key": "slaughter_multiplier", "label": "屠戮倍率"},
            {"key": "troop_attack_divisor_vs_guest", "label": "小兵打门客攻击除数"},
            {"key": "troop_attack_divisor_vs_troop", "label": "小兵打小兵攻击除数"},
            {"key": "troop_defense_divisor", "label": "小兵防御除数"},
            {"key": "counter_multiplier", "label": "五行相克倍率"},
            {"key": "crit_chance", "label": "暴击率"},
        ]

        return render(
            request,
            "battle_debugger/tune.html",
            {
                "presets": preset_info,
                "tunable_params": tunable_params,
            },
        )

    # POST - 运行调优
    try:
        preset_name = request.POST.get("preset")
        param_name = request.POST.get("param")
        values_str = request.POST.get("values", "")
        repeat = min(safe_int(request.POST.get("repeat", 10), default=10, min_val=1), MAX_REPEAT)

        # 解析参数值，限制数量
        values = []
        for v in values_str.split(",")[:MAX_TUNE_VALUES]:
            v = v.strip()
            if v:
                parsed = safe_float(v, default=None)
                if parsed is not None:
                    values.append(parsed)
        if not values:
            return _render_debugger_error(request, "请提供有效的参数值")

        # 加载配置
        config = loader.load_preset(preset_name)
        _validate_config_or_raise(loader, config)

        # 运行调优
        tune_results = []
        for value in values:
            config.tunable_params[param_name] = value

            results = []
            for _ in range(repeat):
                simulator = BattleSimulator(config)
                result = simulator.run_battle()
                results.append(result)

            attacker_wins = sum(1 for r in results if r["winner"] == "attacker")
            win_rate = attacker_wins / repeat * 100
            avg_rounds = sum(len(r["combat_log"]) for r in results) / repeat

            tune_results.append(
                {
                    "value": value,
                    "win_rate": win_rate,
                    "avg_rounds": avg_rounds,
                    "attacker_wins": attacker_wins,
                    "defender_wins": repeat - attacker_wins,
                }
            )

        return render(
            request,
            "battle_debugger/tune_result.html",
            {
                "preset_name": preset_name,
                "param_name": param_name,
                "repeat": repeat,
                "results": tune_results,
            },
        )

    except FileNotFoundError:
        logger.warning(f"预设配置不存在: {request.POST.get('preset')}")
        return _render_debugger_error(request, "预设配置不存在")
    except BattleDebuggerInputError as exc:
        logger.warning("参数调优输入无效: %s", exc)
        return _render_debugger_error(request, str(exc))


# ============ API接口 ============


@debugger_view
def api_guests(request):
    """获取所有门客模板"""
    from guests.models import GuestTemplate

    # 优化：使用 only() 只获取需要的字段，避免加载不必要的数据
    guests = (
        GuestTemplate.objects.exclude(key__in=CUSTOM_GUEST_TEMPLATE_KEYS)
        .only(
            "key",
            "name",
            "rarity",
            "archetype",
            "base_attack",
            "base_intellect",
            "base_defense",
            "base_agility",
            "base_luck",
            "base_hp",
        )
        .order_by("rarity", "name")
    )
    data = [
        {
            "key": g.key,
            "name": g.name,
            "rarity": g.rarity,
            "archetype": g.archetype,
            "base_force": g.base_attack,  # Map attack to force for consistency
            "base_intellect": g.base_intellect,
            "base_defense": g.base_defense,
            "base_agility": g.base_agility,
            "base_luck": g.base_luck,
            "base_hp": g.base_hp,
        }
        for g in guests
    ]
    return JsonResponse(data, safe=False)


@debugger_view
def api_skills(request):
    """获取所有技能"""
    from guests.models import Skill

    # 优化：使用 only() 只获取需要的字段
    skills = Skill.objects.only("key", "name", "kind", "description").order_by("name")
    data = [
        {
            "key": s.key,
            "name": s.name,
            "kind": s.kind,
            "description": s.description or "",
        }
        for s in skills
    ]
    return JsonResponse(data, safe=False)


@debugger_view
def api_troops(request):
    """获取所有小兵类型"""
    from battle.models import TroopTemplate

    # 优化：使用 only() 只获取需要的字段
    troops = TroopTemplate.objects.only(
        "key", "name", "base_attack", "base_defense", "base_hp", "description", "priority"
    ).order_by("priority")
    data = [
        {
            "key": t.key,
            "name": t.name,
            "base_attack": t.base_attack,
            "base_defense": t.base_defense,
            "base_hp": t.base_hp,
            "description": t.description or "",
        }
        for t in troops
    ]
    return JsonResponse(data, safe=False)


# ============ 自定义配置 ============


@debugger_view
@require_http_methods(["GET", "POST"])
def custom_config(request):
    """自定义配置页面"""
    loader = ConfigLoader()
    from battle.models import TroopTemplate
    from guests.models import GuestTemplate, Skill

    if request.method == "GET":
        # 获取所有可用数据（预加载关联对象避免 N+1 查询）
        guests = (
            GuestTemplate.objects.exclude(key__in=CUSTOM_GUEST_TEMPLATE_KEYS)
            .prefetch_related("initial_skills")
            .order_by("rarity", "name")
        )
        skills = Skill.objects.all().order_by("name")
        troops = TroopTemplate.objects.all().order_by("priority")

        # 可调参数列表
        tunable_params = [
            {"key": "slaughter_multiplier", "label": "屠戮倍率", "default": 30, "min": 1, "max": 100},
            {
                "key": "troop_attack_divisor_vs_guest",
                "label": "小兵打门客攻击除数",
                "default": 4.0,
                "min": 1,
                "max": 10,
                "step": 0.1,
            },
            {
                "key": "troop_attack_divisor_vs_troop",
                "label": "小兵打小兵攻击除数",
                "default": 1.0,
                "min": 0.5,
                "max": 5,
                "step": 0.1,
            },
            {
                "key": "troop_defense_divisor",
                "label": "小兵防御除数",
                "default": 2.0,
                "min": 0.5,
                "max": 10,
                "step": 0.1,
            },
            {"key": "counter_multiplier", "label": "五行相克倍率", "default": 1.5, "min": 1, "max": 3, "step": 0.1},
            {"key": "crit_chance", "label": "暴击率", "default": 0.05, "min": 0, "max": 1, "step": 0.01},
        ]

        return render(
            request,
            "battle_debugger/custom_config.html",
            {
                "guests": guests,
                "skills": skills,
                "troops": troops,
                "tunable_params": tunable_params,
            },
        )

    # POST - 运行自定义配置模拟
    try:
        # 解析攻方配置
        attacker_config = _parse_party_config(request.POST, "attacker")
        defender_config = _parse_party_config(request.POST, "defender")

        # 创建配置对象
        config = BattleConfig(
            name="自定义配置",
            description="用户自定义战斗配置",
            attacker=attacker_config,
            defender=defender_config,
            tunable_params=_parse_tunable_params(request.POST),
        )
        _validate_config_or_raise(loader, config)

        # 运行模拟
        simulator = BattleSimulator(config)
        seed = request.POST.get("seed")
        seed_value = safe_int(seed, default=None) if seed else None
        repeat = min(safe_int(request.POST.get("repeat", 1), default=1, min_val=1), MAX_REPEAT)  # 限制最大重复次数

        results = []
        for i in range(repeat):
            current_seed = seed_value + i if seed_value is not None else None
            result = simulator.run_battle(seed=current_seed)
            results.append(result)

        # 生成结果ID并缓存
        result_id = str(uuid.uuid4())
        cache.set(
            f"battle_result_{result_id}",
            {
                "config": config,
                "results": results,
                "preset_name": "自定义配置",
            },
            timeout=3600,
        )

        return redirect("battle_debugger:result_detail", result_id=result_id)

    except BattleDebuggerInputError as exc:
        logger.warning("自定义配置输入无效: %s", exc)
        return _render_debugger_error(request, str(exc))


def _parse_party_config(post_data, side: str) -> PartyConfig:
    """解析阵营配置"""
    # 解析门客
    guests = []
    guest_count = safe_int(post_data.get(f"{side}_guest_count", 0), default=0, min_val=0) or 0
    for i in range(guest_count):
        template = post_data.get(f"{side}_guest_{i}_template")
        if template:
            level = safe_int(post_data.get(f"{side}_guest_{i}_level", 1), default=1, min_val=1) or 1

            # 属性（可选）
            force = post_data.get(f"{side}_guest_{i}_force")
            intellect = post_data.get(f"{side}_guest_{i}_intellect")
            defense = post_data.get(f"{side}_guest_{i}_defense")
            agility = post_data.get(f"{side}_guest_{i}_agility")
            luck = post_data.get(f"{side}_guest_{i}_luck")

            # 技能
            skills_str = post_data.get(f"{side}_guest_{i}_skills", "")
            skills = [s.strip() for s in skills_str.split(",") if s.strip()]

            guest_config = GuestConfig(
                template=template,
                level=level,
                force=safe_int(force, default=None) if force else None,
                intellect=safe_int(intellect, default=None) if intellect else None,
                defense=safe_int(defense, default=None) if defense else None,
                agility=safe_int(agility, default=None) if agility else None,
                luck=safe_int(luck, default=None) if luck else None,
                skills=skills,
            )
            guests.append(guest_config)

    # 解析小兵
    troops = {}
    troop_types = post_data.getlist(f"{side}_troop_types")
    for troop_type in troop_types:
        count = post_data.get(f"{side}_troop_{troop_type}")
        if count:
            parsed_count = safe_int(count, default=0, min_val=0) or 0
            if parsed_count > 0:
                troops[troop_type] = parsed_count

    # 科技等级
    tech_level = safe_int(post_data.get(f"{side}_tech_level", 0), default=0, min_val=0) or 0

    return PartyConfig(
        guests=guests,
        troops=troops,
        technology_level=tech_level,
    )


def _parse_tunable_params(post_data) -> dict:
    """解析可调参数"""
    params = {}
    param_keys = [
        "slaughter_multiplier",
        "troop_attack_divisor_vs_guest",
        "troop_attack_divisor_vs_troop",
        "troop_defense_divisor",
        "counter_multiplier",
        "crit_chance",
    ]
    for key in param_keys:
        value = post_data.get(f"param_{key}")
        if value:
            parsed = safe_float(value, default=None)
            if parsed is not None:
                params[key] = parsed
    return params
