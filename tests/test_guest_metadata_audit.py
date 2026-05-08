from __future__ import annotations

from scripts.audit_guest_metadata import GuestRecord, audit_records


def _record(*, flavor: str, file: str = "history_xianqin_10.yaml") -> GuestRecord:
    return GuestRecord(
        file=file,
        rarity="green",
        index=0,
        key="hist_test_0001",
        name="测试人物",
        gender="male",
        flavor=flavor,
        archetype="civil",
        source_text="",
        batch_id=1,
    )


def test_history_flavor_with_explicit_source_limit_is_not_padded_for_length() -> None:
    record = _record(
        flavor="桓叔捷，先秦人物，史籍记载有限。除姓名外，其身份、事功与结局难以详考。传世材料只留下列国时代人物的朴素轮廓。"
    )

    issues = audit_records([record], min_flavor_len=100)

    assert [issue.code for issue in issues] == []


def test_history_flavor_without_source_limit_still_requires_minimum_length() -> None:
    record = _record(flavor="范宽，北宋画家，与李成、关仝并称三家。")

    issues = audit_records([record], min_flavor_len=100)

    assert [issue.code for issue in issues] == ["short_flavor"]


def test_history_flavor_with_concise_factual_summary_can_be_under_global_minimum() -> None:
    record = _record(
        flavor="范宽（约960—约1030），华原人。传世《溪山行旅图》气象浑穆，山势逼人，峰峦浑厚而草木华滋。与李成、关仝并称三家，为北宋山水画开宗立派者。",
        file="history_songliaojinyuan_04.yaml",
    )

    issues = audit_records([record], min_flavor_len=100)

    assert [issue.code for issue in issues] == []
