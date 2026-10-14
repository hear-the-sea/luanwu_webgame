from __future__ import annotations

import json

from scripts.collect_runtime_metrics import access_metrics, disk_counters, disk_delta, request_summary, warnings_for


def test_verification_path_tokens_are_not_retained_in_metric_endpoints():
    result = request_summary(
        [
            {
                "request": {"uri": "/accounts/verify-email/private-one/?token=query-secret"},
                "status": 200,
                "duration": 0.1,
            },
            {"request": {"uri": "/accounts/verify-email/private-two/"}, "status": 200, "duration": 0.2},
        ]
    )
    assert list(result["endpoints"]) == ["/accounts/verify-email/:token/"]
    assert result["endpoints"]["/accounts/verify-email/:token/"]["count"] == 2
    assert "private" not in str(result)
    assert "query-secret" not in str(result)


def test_disk_delta_tracks_latency_and_rejects_reset():
    before = disk_counters("8 16 vdb 10 0 100 20 20 0 200 40 0 30 60", "vdb")
    after = disk_counters("8 16 vdb 12 0 120 100 22 0 220 160 0 70 260", "vdb")
    assert disk_delta(after, before, 2) == {
        "operations_per_second": 2.0,
        "average_io_ms": 50.0,
        "busy_percent": 2.0,
        "average_queue_depth": 0.1,
    }
    assert disk_delta(before, after, 2) == {}


def test_ws_lifetimes_and_assets_are_not_page_latency_or_private_payload():
    result = request_summary(
        [
            {"request": {"uri": "/ws/chat/world/"}, "status": 101, "duration": 86400},
            {"request": {"uri": "/static/private.png"}, "status": 200, "duration": 100},
            {"request": {"uri": "/guests/234/?token=private"}, "status": 500, "duration": 2},
        ]
    )
    assert result["websocket_status"] == {"101": 1}
    assert result["endpoints"] == {"/guests/:id/": {"count": 1, "p95_ms": 2000, "max_ms": 2000, "over_1s": 1}}
    assert "private" not in json.dumps(result)


def test_rotated_access_logs_are_read_once_and_budget_does_not_lose_tail(tmp_path):
    path = tmp_path / "docker-json.log"
    row = (
        json.dumps(
            {
                "log": json.dumps(
                    {"logger": "http.log.access.log0", "request": {"uri": "/"}, "status": 200, "duration": 0.1}
                )
            }
        )
        + "\n"
    )
    path.write_text(row)
    result, cursors = access_metrics(str(path), {}, 10000)
    assert result["first_run"] is True
    assert result["http_status"] == {}
    with path.open("a") as output:
        output.write(row * 2)
    result, cursors = access_metrics(str(path), cursors, len(row.encode()))
    assert result["http_status"] == {"200": 1}
    assert result["complete"] is False
    path.rename(path.with_name(path.name + ".1"))
    path.write_text(row)
    result, cursors = access_metrics(str(path), cursors, 10000)
    assert result["http_status"] == {"200": 2}
    result, _ = access_metrics(str(path), cursors, 10000)
    assert result["http_status"] == {}


def test_warnings_use_counter_deltas_and_memory_headroom():
    previous = {"redis": {"broker": {"aof_delayed_fsync": "10"}}}
    current = {
        "memory_available_mb": 900,
        "redis": {"broker": {"aof_delayed_fsync": "11"}},
        "disk_interval": {"average_io_ms": 40},
        "containers": {"web": {"limit_bytes": 100, "used_bytes": 95, "status": "running"}},
    }
    assert set(warnings_for(current, previous)) == {
        "host_memory_headroom_low",
        "broker:aof_delayed_fsync_increased",
        "data_disk_average_io_over_25ms",
        "web:memory_over_85_percent",
    }


def test_runtime_restart_and_page_latency_raise_warnings_without_health_noise():
    previous = {"redis": {"runtime": {"uptime_in_seconds": "500"}}}
    current = {
        "redis": {"runtime": {"uptime_in_seconds": "3"}},
        "access": {
            "http_status": {"503": 1},
            "websocket_status": {"403": 200},
            "endpoints": {
                "/health/ready": {"count": 100, "p95_ms": 3000},
                "/manor/tasks/": {"count": 30, "p95_ms": 800},
            },
        },
    }
    assert set(warnings_for(current, previous)) == {
        "runtime:instance_restarted",
        "http_5xx_observed",
        "http_p95_over_500ms:/manor/tasks/",
        "websocket_rejections_over_100",
    }
