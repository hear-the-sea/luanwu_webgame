from pathlib import Path


def test_battle_report_state_bar_css_keeps_name_bar_and_skills_on_one_line():
    css = Path("static/css/style.css").read_text(encoding="utf-8")

    assert ".event-unit-summary" in css
    assert ".battle-unit-state" in css
    assert "minmax(min(100%, 300px), 1fr)" in css
    assert ".event-unit-summary > .event-unit-name," in css
    assert ".event-actor-settlement-summary > .event-unit-name" in css
    assert ".event-target-summary > .event-unit-name" not in css
    assert "flex: 0 1 auto" in css
    assert "flex: 0 1 9rem" in css
    assert "width: 9rem" in css
    assert "flex: 0 1 72px" in css
    assert "min-width: 40px" in css
    assert "flex: 1 1 3.5rem" in css
    assert "--battle-unit-state-percent" in css
    assert "white-space: nowrap" in css
    assert "text-overflow: ellipsis" in css
    assert "clamp(3.5rem, 20vw, 4.5rem)" in css
    assert "clamp(40px, 16vw, 52px)" in css
    assert "flex: 0 1 clamp(3.5rem, 20vw, 4.5rem)" in css
    assert "flex: 0 1 clamp(40px, 16vw, 52px)" in css
