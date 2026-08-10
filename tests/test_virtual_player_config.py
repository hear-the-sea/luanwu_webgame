from __future__ import annotations

import pytest
from django.test import override_settings

from gameplay.services.virtual_player_core import config as config_module
from gameplay.services.virtual_player_core.config import (
    BootstrapMode,
    MaintenanceMode,
    RoutingTransitionError,
    V2RoutingConfig,
    VirtualPlayerConfigError,
    clear_virtual_player_config_cache,
    load_virtual_player_config,
    parse_bot_development_v2,
    policy_checksum,
    validate_routing_transition,
)
from tests.yaml_schema_new_configs.virtual_players import _minimal_v2_config, _policy_checksum


@pytest.fixture(autouse=True)
def _clear_config_cache():
    clear_virtual_player_config_cache()
    yield
    clear_virtual_player_config_cache()


def test_repository_virtual_player_config_is_valid_at_runtime() -> None:
    config = load_virtual_player_config()

    assert config["enabled"] is True
    assert config["population"]["hard_cap"] == 1000
    assert list(config["prestige_bands"]) == ["newbie", "junior", "middle", "senior", "veteran"]
    v2_config = parse_bot_development_v2(config["bot_development_v2"])
    assert v2_config.routing.bootstrap_mode is BootstrapMode.V2_ACTIVE
    assert v2_config.routing.maintenance_mode is MaintenanceMode.V2_ACTIVE
    assert v2_config.policy_rollout.enabled is False
    assert v2_config.policy_rollout.target_version == 2
    assert v2_config.policy_rollout.rollout_percent == 0
    assert dict(v2_config.reference_snapshot_catalog) == {}
    assert tuple(v2_config.policies) == (2,)


@override_settings(VIRTUAL_PLAYER_CONFIG={"population": {"region_floor": 11}})
def test_runtime_settings_support_strict_partial_overrides() -> None:
    config = load_virtual_player_config()

    assert config["population"]["region_floor"] == 11
    assert config["population"]["global_floor"] == 32


@override_settings(VIRTUAL_PLAYER_CONFIG={"populaton": {"region_floor": 11}})
def test_runtime_settings_reject_unknown_root_fields() -> None:
    with pytest.raises(VirtualPlayerConfigError, match="<root>.populaton: unknown field"):
        load_virtual_player_config()


@override_settings(VIRTUAL_PLAYER_CONFIG=False)
def test_runtime_settings_reject_non_mapping_overrides() -> None:
    with pytest.raises(VirtualPlayerConfigError, match="must be a mapping"):
        load_virtual_player_config()


def test_runtime_loader_fails_closed_on_malformed_yaml(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "virtual_players.yaml"
    config_path.write_text("bot_development_v2: [", encoding="utf-8")
    monkeypatch.setattr(config_module, "VIRTUAL_PLAYER_CONFIG_PATH", config_path)

    with pytest.raises(VirtualPlayerConfigError, match="Unable to load virtual-player config"):
        load_virtual_player_config()


def test_runtime_loader_fails_closed_on_incomplete_v2_config(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "virtual_players.yaml"
    config_path.write_text(
        "bot_development_v2:\n  environment_mode: test\n  engine_version: 2\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "VIRTUAL_PLAYER_CONFIG_PATH", config_path)

    with pytest.raises(VirtualPlayerConfigError) as exc_info:
        load_virtual_player_config()

    assert any("missing required field 'policies'" in error for error in exc_info.value.errors)


def test_typed_v2_config_is_immutable_and_resolves_all_band_boundaries() -> None:
    raw = _minimal_v2_config()
    config = parse_bot_development_v2(raw)

    assert config.engine_version == 2
    assert config.routing.bootstrap_mode is BootstrapMode.V2_ACTIVE
    assert config.routing.maintenance_mode is MaintenanceMode.V2_ACTIVE
    assert config.policy().version == 2
    assert config.policy().max_development_actions == 16
    assert dict(config.reference_snapshot_catalog) == {}
    assert [config.band_for_prestige(value).name for value in (0, 499, 500, 119999, 120000, 240000, 10**9)] == [
        "newbie",
        "newbie",
        "junior",
        "elite",
        "legend",
        "mythic",
        "mythic",
    ]
    with pytest.raises(TypeError):
        config.policies[2] = config.policy()  # type: ignore[index]
    with pytest.raises(TypeError):
        config.policy().payload["anchor_k"] = 9  # type: ignore[index]
    with pytest.raises(TypeError):
        config.policy().payload["strength_safety"]["no_reference"] = {}  # type: ignore[index]


def test_typed_v2_config_retires_gate_d2_evidence_registry() -> None:
    raw = _minimal_v2_config()
    raw["reference_snapshot_catalog"] = {
        "3": {
            "schema_version": 1,
            "digest": "a" * 64,
            "artifact_path": "data/virtual_player_reference_snapshots/v3.json",
            "gate_d2_evidence": {
                "1": {
                    "junior": {
                        "schema_version": 3,
                        "digest": "b" * 64,
                    }
                }
            },
        }
    }

    with pytest.raises(VirtualPlayerConfigError, match="reference_snapshot_catalog: unknown field"):
        parse_bot_development_v2(raw)


def test_typed_policy_checksum_matches_the_independent_validator_vector() -> None:
    raw_policy = _minimal_v2_config()["policies"]["2"]

    assert policy_checksum(raw_policy) == _policy_checksum(raw_policy) == raw_policy["checksum"]


def test_typed_v2_config_rejects_negative_prestige_and_missing_policy() -> None:
    config = parse_bot_development_v2(_minimal_v2_config())

    with pytest.raises(VirtualPlayerConfigError, match="prestige must be non-negative"):
        config.band_for_prestige(-1)
    assert config.policy(2).version == 2
    with pytest.raises(VirtualPlayerConfigError, match="policy 1 is retired"):
        config.policy(1)


def _routing(bootstrap: BootstrapMode, maintenance: MaintenanceMode) -> V2RoutingConfig:
    return V2RoutingConfig(
        activation_mode="direct_after_gate",
        bootstrap_mode=bootstrap,
        maintenance_mode=maintenance,
    )


def test_routing_transition_accepts_v2_activation_and_pause_recovery() -> None:
    validate_routing_transition(
        _routing(BootstrapMode.LEGACY_BEFORE_GATE, MaintenanceMode.LEGACY_BEFORE_GATE),
        _routing(BootstrapMode.V2_ACTIVE, MaintenanceMode.V2_ACTIVE),
    )
    validate_routing_transition(
        _routing(BootstrapMode.V2_ACTIVE, MaintenanceMode.V2_ACTIVE),
        _routing(BootstrapMode.V2_PAUSED, MaintenanceMode.V2_PAUSED),
    )
    validate_routing_transition(
        _routing(BootstrapMode.V2_PAUSED, MaintenanceMode.V2_PAUSED),
        _routing(BootstrapMode.V2_ACTIVE, MaintenanceMode.V2_ACTIVE),
    )


@pytest.mark.parametrize(
    ("current", "proposed", "message"),
    [
        (
            _routing(BootstrapMode.V2_ACTIVE, MaintenanceMode.V2_ACTIVE),
            _routing(BootstrapMode.LEGACY_BEFORE_GATE, MaintenanceMode.V2_ACTIVE),
            "Illegal Bootstrap",
        ),
        (
            _routing(BootstrapMode.LEGACY_BEFORE_GATE, MaintenanceMode.LEGACY_BEFORE_GATE),
            _routing(BootstrapMode.LEGACY_BEFORE_GATE, MaintenanceMode.V2_ACTIVE),
            "before Bootstrap leaves legacy mode",
        ),
        (
            _routing(BootstrapMode.V2_ACTIVE, MaintenanceMode.V2_ACTIVE),
            _routing(BootstrapMode.V2_ACTIVE, MaintenanceMode.V2_CUTOVER),
            "Illegal Maintenance",
        ),
    ],
)
def test_routing_transition_rejects_fallback_and_cutover_skips(current, proposed, message) -> None:
    with pytest.raises(RoutingTransitionError, match=message):
        validate_routing_transition(current, proposed)
