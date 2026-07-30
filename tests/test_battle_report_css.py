import re
from pathlib import Path


def _rule_body(css: str, selector: str) -> str:
    match = re.search(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", css)
    assert match is not None, f"missing CSS rule for {selector}"
    return match.group(1)


def test_battle_report_state_bar_and_skills_use_non_overlapping_grid():
    css = Path("static/css/style.css").read_text(encoding="utf-8")

    assert "minmax(min(100%, 300px), 1fr)" in css
    assert ".event-passive-entry > .event-unit-summary" in css
    assert '"unit-name skills"' in css
    assert '"unit-state skills"' in css

    state_rule = _rule_body(css, ".battle-unit-state")
    assert "display: block" in state_rule
    assert "box-sizing: border-box" in state_rule
    assert "--battle-unit-state-percent" in css

    summary_state_rule = _rule_body(css, ".event-unit-summary > .battle-unit-state")
    assert "grid-area: unit-state" in summary_state_rule
    assert "width: 100%" in summary_state_rule

    hidden_status_rule = _rule_body(
        css,
        ".event-unit-summary.event-status-layout:not(:has(> .battle-unit-state))",
    )
    assert 'grid-template-areas: "unit-name skills"' in hidden_status_rule
    hidden_status_name_rule = _rule_body(
        css,
        ".event-unit-summary.event-status-layout:not(:has(> .battle-unit-state)) > .event-unit-name",
    )
    assert "align-self: center" in hidden_status_name_rule

    skills_rule = _rule_body(css, ".event-unit-skills")
    assert "flex-wrap: wrap" in skills_rule
    assert "overflow: visible" in skills_rule
    assert "white-space: normal" in skills_rule

    passive_extra_rule = _rule_body(css, ".event-unit-skills > .event-passive-extra")
    assert "flex: 0 1 auto" in passive_extra_rule
    assert "flex-wrap: wrap" in passive_extra_rule
    assert "width: auto" in passive_extra_rule
    assert "overflow: visible" in passive_extra_rule
    assert "overflow-wrap: anywhere" in css

    damage_summary_rule = _rule_body(css, ".event-target-summary.event-damage-summary")
    assert "display: grid" in damage_summary_rule
    assert "grid-template-columns: auto minmax(0, 1fr);" in damage_summary_rule
    assert ".event-damage-summary > .battle-unit-state" not in css

    damage_detail_rule = _rule_body(css, ".event-damage-summary .event-target-detail")
    assert "overflow: visible" in damage_detail_rule
    assert "white-space: normal" in damage_detail_rule
    assert "overflow-wrap: anywhere" in damage_detail_rule
