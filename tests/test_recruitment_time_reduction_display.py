from types import SimpleNamespace

import pytest
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from gameplay.selectors import troop_recruitment as selector
from gameplay.services.recruitment.recruitment import calculate_recruitment_duration


@pytest.mark.parametrize(
    ("training", "citang", "expected_percent"),
    [(1.0, 1.0, 0), (1.25, 1.0, 20), (1.0, 2.0, 50), (1.3, 2.2, 65)],
)
def test_recruitment_time_reduction_matches_actual_duration(monkeypatch, training, citang, expected_percent):
    manor = SimpleNamespace(
        guard_training_speed_multiplier=training,
        citang_recruitment_speed_multiplier=citang,
        get_building_level=lambda _key: 10,
    )
    for name, value in {
        "has_active_recruitment": False,
        "get_recruitment_options": [],
        "get_active_recruitments": [],
        "get_player_troops": [],
        "get_manor_troop_used_space": 0,
        "get_manor_troop_remaining_space": 5000,
        "get_troop_bank_used_space": 0,
        "get_troop_bank_remaining_space": 5000,
    }.items():
        monkeypatch.setattr(selector, name, lambda _manor, value=value: value)
    monkeypatch.setattr(
        "gameplay.services.recruitment.recruitment.scale_duration",
        lambda duration, **_kwargs: duration,
    )

    context = selector.get_troop_recruitment_context(manor, selected_category="all")
    duration = calculate_recruitment_duration(1_000_000, manor)

    assert context["speed_bonus_percent"] == expected_percent
    assert abs(context["speed_bonus_percent"] - (1 - duration / 1_000_000) * 100) < 1
    text = "".join(strip_tags(render_to_string("gameplay/troop_recruitment.html", context)).split())
    if expected_percent:
        assert f"时间-{expected_percent}%" in text
    else:
        assert "时间-" not in text
    assert "速度-" not in text
