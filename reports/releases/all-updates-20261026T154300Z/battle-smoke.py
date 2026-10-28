from django.db import connection

from gameplay.models import Season
from gameplay.services.battle_snapshots import build_guest_battle_snapshots
from gameplay.services.season.combat import prepare_combat, simulate_combat
from gameplay.services.season.hitpoints import prepare_gate
from guests.models import Guest

connection.ensure_connection()


def forbid_writes(execute, sql, params, many, context):
    verb = sql.lstrip().split(None, 1)[0].upper()
    assert verb in {"SELECT", "SHOW", "EXPLAIN", "DESCRIBE"}, "Readonly smoke rejected SQL write"
    return execute(sql, params, many, context)


with connection.execute_wrapper(forbid_writes):
    guest = Guest.objects.filter(current_hp__gt=0).select_related("manor", "template").order_by("-current_hp").first()
    assert guest is not None
    lineup = {"guests": build_guest_battle_snapshots([guest]), "troops": {}, "technology": {}}
    normal = prepare_combat(guest.manor, lineup, lineup, "发布只读验证", 20261026, preserve_state=True)
    report = simulate_combat(guest.manor, normal, 20261026)
    assert report.pk is None
    site = Season.objects.filter(closed=False).first().sites.get(key="mountain:gate")
    gate = prepare_combat(
        guest.manor,
        lineup,
        {"guests": []},
        "发布只读城门验证",
        20261027,
        preserve_state=True,
        allow_empty_defenders=True,
    )
    metadata = prepare_gate(gate, site)
    gate_report = simulate_combat(guest.manor, gate, 20261027)
    assert gate_report.pk is None
    assert metadata["normal_round_limit"] == 8
    assert any(row["key"] == "wall" for row in gate_report.defender_city_defenses)
    print(
        json.dumps(
            {
                "snapshot_battle": True,
                "empty_guard_gate_battle": True,
                "gate_normal_round_limit": 8,
                "database_writes": 0,
                "persisted_reports": 0,
            }
        )
    )
