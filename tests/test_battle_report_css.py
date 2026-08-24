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


def test_report_uses_one_pale_yellow_surface_and_round_roster_column_dividers():
    css = Path("battle/templates/battle/partials/report_detail_styles.html").read_text(encoding="utf-8")

    page_rule = _rule_body(css, ".battle-report-page")
    assert "--battle-report-background: #fff6d8" in page_rule
    assert "background: var(--battle-report-background)" in page_rule

    title_rule = _rule_body(css, ".round-title-row")
    assert "justify-content: center" in title_rule
    assert "text-align: center" in title_rule

    for selector in (
        ".battle-rounds-card .battle-round",
        ".round-title-row",
        ".round-side-heading.attacker",
        ".round-side-heading.defender",
    ):
        assert "background: var(--battle-report-background)" in _rule_body(css, selector)

    surface_rules = re.findall(r"\.round-roster-cell,\s*\.round-action-cell\s*\{([^}]*)\}", css)
    assert any("background: var(--battle-report-background)" in rule for rule in surface_rules)

    result_rule = _rule_body(
        css,
        ".battle-report-page .battle-result,\n.battle-report-page .battle-result.victory,\n.battle-report-page .battle-result.defeat",
    )
    assert "background: var(--battle-report-background)" in result_rule
    assert "border-color: #c8a36d" in result_rule
    assert "color: #4b3420" in result_rule

    roster_rule = _rule_body(css, ".round-roster-cell")
    assert "gap: 0" in roster_rule
    assert "padding: 0" in roster_rule

    label_rule = _rule_body(css, ".round-row-label")
    assert "border-right: 1px solid #c8a36d" in label_rule

    assert "font-style: italic" not in css


def test_report_title_overview_and_settlement_use_flat_table_layouts():
    css = Path("battle/templates/battle/partials/report_detail_styles.html").read_text(encoding="utf-8")

    title_wrap_rule = _rule_body(css, ".battle-report-title")
    assert "margin-top: 0.5rem" in title_wrap_rule

    title_rule = _rule_body(css, ".battle-report-page .battle-report-title h1")
    assert "font-size: 1.45rem" in title_rule
    assert "text-align: center" in title_rule

    section_title_rule = _rule_body(css, ".battle-report-page .tw-card > h2")
    assert "font-size: 1.18rem" in section_title_rule

    for selector in (".battle-overview-table", ".battle-settlement-table"):
        table_rule = _rule_body(css, selector)
        assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in table_rule
        assert "border: 1px solid #c8a36d" in table_rule
        assert "background: var(--battle-report-background)" in table_rule

    label_rule = _rule_body(css, ".battle-table-label")
    assert "border-right: 1px solid #c8a36d" in label_rule

    assert ".lineup-comparison" not in css

    assert ".round-action-list.attacker li" not in css
    assert ".round-action-list.defender li" not in css
