import math

from django import forms

from core.utils.yaml_validators import validate_guest_skills

from .models import Skill


def _finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise forms.ValidationError("配置中的数值必须是有限数字。")
    try:
        number = float(value)
    except (ValueError, OverflowError):
        raise forms.ValidationError("配置中的数值必须是有限数字。") from None
    if not math.isfinite(number):
        raise forms.ValidationError("配置中的数值必须是有限数字。")
    return number


def _check_finite_numbers(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        _finite_number(value)
    elif isinstance(value, dict):
        for item in value.values():
            _check_finite_numbers(item)
    elif isinstance(value, list):
        for item in value:
            _check_finite_numbers(item)


class SkillAdminForm(forms.ModelForm):
    class Meta:
        model = Skill
        exclude = ("base_probability",)

    def clean_status_probability(self):
        value = _finite_number(self.cleaned_data["status_probability"])
        if not 0 <= value <= 1:
            raise forms.ValidationError("状态施加概率必须在零到一之间。")
        return value

    def clean_damage_formula(self):
        formula = self.cleaned_data["damage_formula"]
        if formula is None:
            return {}
        if not isinstance(formula, dict):
            raise forms.ValidationError("伤害公式必须是对象，空配置请填写空对象。")
        _check_finite_numbers(formula)
        if "base" in formula:
            _finite_number(formula["base"])
        for side in ("ally", "enemy"):
            stats = formula.get(side)
            if stats is None:
                continue
            if not isinstance(stats, dict):
                raise forms.ValidationError("伤害公式的己方和敌方属性加成必须是对象。")
            for coefficient in stats.values():
                _finite_number(coefficient)
        return formula

    def clean_passive_config(self):
        config = self.cleaned_data["passive_config"]
        if config is None:
            return {}
        if not isinstance(config, dict):
            raise forms.ValidationError("被动技能配置必须是对象，空配置请填写空对象。")
        _check_finite_numbers(config)
        # 复用游戏配置校验器，保持后台与导入命令的结构规则一致。
        try:
            result = validate_guest_skills(
                {
                    "skills": [
                        {
                            "key": "admin_validation",
                            "name": "后台校验",
                            "rarity": "gray",
                            "passive_config": config,
                        }
                    ]
                }
            )
        except (TypeError, ValueError, OverflowError):
            raise forms.ValidationError("被动技能配置的字段类型不正确。") from None
        if not result.is_valid:
            raise forms.ValidationError("被动技能配置不符合游戏规则，请检查触发时机、条件、效果及作用域。")
        for trigger in config.get("triggers") or []:
            conditions = trigger.get("conditions") or {}
            for key, value in conditions.items():
                if key.startswith("hp_ratio_"):
                    _finite_number(value)
                elif key.startswith("ally_alive_template_count_"):
                    counts = value.values() if isinstance(value, dict) else [value]
                    for count in counts:
                        number = _finite_number(count)
                        if number < 0 or not number.is_integer():
                            raise forms.ValidationError("存活门客数量条件必须使用非负整数。")
                        try:
                            int(count)
                        except (ValueError, TypeError, OverflowError):
                            raise forms.ValidationError("存活门客数量条件必须使用整数。") from None
                elif key == "self_template_in" and not isinstance(value, list):
                    raise forms.ValidationError("自身门客模板条件必须使用列表。")
                elif key in {"state_present", "state_absent"} and not isinstance(value, (list, str)):
                    raise forms.ValidationError("状态条件必须使用文字或列表。")
            for effect in trigger.get("effects") or []:
                numeric_fields = (
                    "troop_value_multiplier",
                    "threshold",
                    "overflow_ratio",
                    "ratio",
                    "cap",
                )
                if effect.get("type") not in {"set_state", "emit_log"}:
                    numeric_fields += ("value",)
                for field in numeric_fields:
                    if field in effect:
                        _finite_number(effect[field])
        return config
