from pathlib import Path


def test_battle_report_state_bar_css_keeps_name_bar_and_skills_on_one_line():
    css = Path("static/css/style.css").read_text(encoding="utf-8")

    assert ".event-unit-summary" in css
    assert ".battle-unit-state" in css
    assert "flex: 0 0 72px" in css
    assert "--battle-unit-state-percent" in css
    assert "white-space: nowrap" in css
    assert "text-overflow: ellipsis" in css
    assert "flex: 0 0 52px" in css
