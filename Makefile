PYTHON ?= python3
MANAGE ?= $(PYTHON) manage.py
BLACK ?= black
ISORT ?= isort
LOCAL_STATE_DIR ?= .local
FLAKE8_TARGETS ?= accounts battle gameplay guests guilds trade core websocket config tests
MYPY_TARGETS ?= accounts battle common config core gameplay guests guilds tasks trade websocket
PYTHON_SOURCE_TARGETS ?= accounts battle common config core gameplay guests guilds tasks trade websocket tests
CRITICAL_INTEGRATION_TESTS ?= \
	tests/test_raid_concurrency_integration.py \
	tests/test_raid_scout_concurrency_integration.py \
	tests/test_mission_concurrency_integration.py \
	tests/test_guest_recruitment_concurrency_integration.py \
	tests/test_guest_equipment_concurrency_integration.py \
	tests/test_equipment_template_sync_concurrency_integration.py \
	tests/test_arena_coop_concurrency_integration.py \
	tests/test_arena_resolution_concurrency_integration.py \
	tests/test_guild_raid_failure_concurrency_integration.py \
	tests/test_trade_auction_concurrency_integration.py \
	tests/test_work_service_concurrency.py \
	tests/test_manor_coordinate_concurrency_integration.py \
	tests/test_virtual_player_lock_integration.py \
	tests/test_virtual_player_external_reconciliation_concurrency_integration.py \
	tests/test_virtual_player_maintenance_concurrency_integration.py \
	tests/test_arena_virtual_population_concurrency_integration.py \
	tests/test_virtual_player_baseline_audit.py \
	tests/test_message_claim_delete_concurrency_integration.py
VIRTUAL_PLAYER_GATE_A_CONTRACT_TESTS ?= \
	tests/test_virtual_player_architecture_gate.py \
	tests/test_virtual_player_gate_acceptance_config.py \
	tests/test_virtual_player_evidence_recorder.py \
	tests/test_virtual_player_maintenance_contracts.py \
	tests/test_virtual_player_random_context.py \
	tests/test_pytest_configuration.py
VIRTUAL_PLAYER_GATE_A_REAL_SERVICE_TESTS ?= \
	tests/test_virtual_player_baseline_audit.py \
	tests/raid_concurrency_integration/h01_cross_races.py
VIRTUAL_PLAYER_GATE_D1_CONTRACT_TESTS ?= \
	tests/test_virtual_player_bootstrap_v2.py \
	tests/test_virtual_player_reference_snapshots_v2.py \
	tests/test_virtual_player_bootstrap_routing.py \
	tests/test_virtual_player_population_consumer.py \
	tests/test_virtual_player_population_demand.py \
	tests/test_virtual_player_registration_population.py \
	tests/test_virtual_player_prestige_transitions.py \
	tests/test_virtual_player_projection.py \
	tests/test_virtual_player_strength_budget.py \
	tests/test_virtual_player_economy.py \
	tests/test_virtual_player_config.py \
	tests/test_virtual_player_maintenance_rules.py \
	tests/test_virtual_player_backfill.py \
	tests/arena_services/test_virtual_reserve.py \
	tests/test_arena_schedule.py \
	tests/test_arena_tasks.py \
	tests/test_virtual_player_health.py \
	tests/test_virtual_player_gate_d1_automation.py \
	tests/test_virtual_player_gate_e_automation.py
VIRTUAL_PLAYER_GATE_D1_CORE_REAL_SERVICE_TESTS ?= \
	tests/test_virtual_player_gate_d1_concurrency_integration.py \
	tests/test_virtual_player_health_mysql_integration.py
VIRTUAL_PLAYER_GATE_D1_ADJACENT_REAL_SERVICE_TESTS ?= \
	tests/test_virtual_player_bootstrap_routing_concurrency_integration.py \
	tests/test_arena_virtual_population_concurrency_integration.py \
	tests/test_manor_coordinate_concurrency_integration.py
VIRTUAL_PLAYER_GATE_E_CONTRACT_TESTS ?= \
	tests/test_virtual_player_maintenance_v2.py \
	tests/test_virtual_player_admin_maintenance.py \
	tests/test_virtual_player_arena_shortage_baselines.py \
	tests/test_virtual_player_external_reconciliation.py \
	tests/test_virtual_player_gate_c_persistence.py \
	tests/test_virtual_player_gate_c_reconciliation.py \
	tests/test_virtual_player_gate_exit_workflows.py \
	tests/test_virtual_player_jail_cleanup.py \
	tests/test_virtual_player_maintenance_contracts.py \
	tests/test_virtual_player_operational_fixes.py \
	tests/test_virtual_player_projection.py \
	tests/test_virtual_player_reference_snapshots_v2.py \
	tests/test_virtual_player_safety_metrics.py \
	tests/test_virtual_player_safety_monitor.py \
	tests/test_virtual_player_safety_preflight.py \
	tests/test_virtual_player_safety_provider.py \
	tests/test_virtual_player_safety_routing.py \
	tests/test_virtual_player_safety_tasks.py \
	tests/test_raid_combat_battle.py \
	tests/arena_services/test_virtual_backfill.py \
	tests/arena_services/test_virtual_reserve.py \
	tests/test_arena_virtual_lineups.py \
	tests/test_building_upgrade_primitives.py \
	tests/test_guest_equipment_lock_order_contracts.py \
	tests/test_guest_equipment_locked.py \
	tests/test_guest_roster_service.py \
	tests/test_guest_skill_service.py \
	tests/test_guests_defection.py \
	tests/test_salary_service.py \
	tests/test_technology_upgrade_locked.py \
	tests/test_training_locked.py \
	tests/test_virtual_player_bootstrap_routing.py \
	tests/test_virtual_player_evidence_recorder.py \
	tests/test_virtual_player_population_demand.py \
	tests/test_arena_schedule.py \
	tests/test_arena_tasks.py \
	tests/test_virtual_player_health.py \
	tests/test_virtual_player_gate_e_automation.py \
	tests/test_virtual_player_prestige_transitions.py
VIRTUAL_PLAYER_GATE_E_REAL_SERVICE_TESTS ?= \
	tests/test_virtual_player_external_reconciliation_concurrency_integration.py \
	tests/test_virtual_player_gate_c_concurrency_integration.py \
	tests/test_virtual_player_jail_cleanup_concurrency_integration.py \
	tests/test_virtual_player_maintenance_concurrency_integration.py \
	tests/test_virtual_player_safety_real_service_integration.py \
	tests/test_virtual_player_health_mysql_integration.py \
	tests/test_virtual_player_bootstrap_routing_concurrency_integration.py \
	tests/test_arena_virtual_population_concurrency_integration.py \
	tests/test_building_upgrade_primitives_concurrency_integration.py \
	tests/test_guest_equipment_concurrency_integration.py \
	tests/test_guest_health_salary_concurrency_integration.py \
	tests/test_manor_coordinate_concurrency_integration.py \
	tests/test_technology_upgrade_concurrency_integration.py

GATE_D1_EXPECTED_COMMIT ?= $(shell git rev-parse HEAD)
GATE_D1_EVIDENCE_OUTPUT ?= test-results/gate-d1/gate-d1-$(GATE_D1_EXPECTED_COMMIT).yaml
GATE_E_EXPECTED_COMMIT ?= $(shell git rev-parse HEAD)
VIRTUAL_PLAYER_EVIDENCE_ARTIFACT_DATE ?= 2026-07-30

ifdef DJANGO_DB_PORT
REAL_SERVICES_MYSQL_PORT ?= $(DJANGO_DB_PORT)
else
REAL_SERVICES_MYSQL_PORT ?= 13306
DJANGO_DB_PORT ?= $(REAL_SERVICES_MYSQL_PORT)
endif
REAL_SERVICES_REDIS_PORT ?= 16379
DJANGO_DB_HOST ?= 127.0.0.1
DJANGO_DB_USER ?= webgame
DJANGO_DB_PASSWORD ?= webgame
DJANGO_DB_ROOT_PASSWORD ?= root
DJANGO_DB_NAME ?= webgame
REAL_SERVICES_TEST_DB_USER ?= root
REAL_SERVICES_TEST_DB_PASSWORD ?= $(DJANGO_DB_ROOT_PASSWORD)
REDIS_URL ?= redis://127.0.0.1:$(REAL_SERVICES_REDIS_PORT)
REDIS_BROKER_URL ?= $(REDIS_URL)/0
REDIS_RESULT_URL ?= $(REDIS_BROKER_URL)
REDIS_CHANNEL_URL ?= $(REDIS_URL)/1
REDIS_CACHE_URL ?= $(REDIS_URL)/2
REDIS_PASSWORD ?=

REAL_SERVICE_TEST_ENV = DJANGO_TEST_USE_ENV_SERVICES=1 DJANGO_DB_ENGINE=django.db.backends.mysql DJANGO_DB_HOST=$(DJANGO_DB_HOST) DJANGO_DB_PORT=$(DJANGO_DB_PORT) DJANGO_DB_USER=$(REAL_SERVICES_TEST_DB_USER) DJANGO_DB_PASSWORD=$(REAL_SERVICES_TEST_DB_PASSWORD) DJANGO_DB_NAME=$(DJANGO_DB_NAME) REDIS_URL=$(REDIS_URL) REDIS_BROKER_URL=$(REDIS_BROKER_URL) REDIS_RESULT_URL=$(REDIS_RESULT_URL) REDIS_CHANNEL_URL=$(REDIS_CHANNEL_URL) REDIS_CACHE_URL=$(REDIS_CACHE_URL) REDIS_PASSWORD=$(REDIS_PASSWORD)
REAL_SERVICE_COMPOSE_ENV = REAL_SERVICES_MYSQL_PORT=$(REAL_SERVICES_MYSQL_PORT) REAL_SERVICES_REDIS_PORT=$(REAL_SERVICES_REDIS_PORT) DJANGO_DB_USER=$(DJANGO_DB_USER) DJANGO_DB_PASSWORD=$(DJANGO_DB_PASSWORD) DJANGO_DB_ROOT_PASSWORD=$(DJANGO_DB_ROOT_PASSWORD) DJANGO_DB_NAME=$(DJANGO_DB_NAME) REDIS_PASSWORD=$(REDIS_PASSWORD)

.PHONY: install install-unpinned install-lock install-dev-lock migrate bootstrap-data dev dev-ws worker beat test test-unit test-unit-cov test-critical test-integration test-all format format-check lint lint-js lint-strict static-check check clean lock lock-dev test-real-services-up test-real-services-down test-real-services test-real-services-preflight test-virtual-player-gate-a test-virtual-player-gate-d1 gate-d1-evidence verify-gate-d1-evidence gate-e-readiness-evidence verify-gate-e-readiness-evidence test-virtual-player-gate-e verify-virtual-player-gate-e test-gates cov cov-html

install:
	@if [ -f requirements-dev.lock.txt ]; then \
		pip install -r requirements-dev.lock.txt; \
	elif [ -f requirements.lock.txt ]; then \
		pip install -r requirements.lock.txt -r requirements-dev.txt; \
	else \
		pip install -r requirements-dev.txt; \
	fi

install-unpinned:
	pip install -r requirements-dev.txt

install-lock:
	pip install -r requirements.lock.txt

install-dev-lock:
	pip install -r requirements-dev.lock.txt

lock:
	$(PYTHON) scripts/generate_requirements_lock.py requirements.txt > requirements.lock.txt

lock-dev:
	$(PYTHON) scripts/generate_requirements_lock.py requirements-dev.txt > requirements-dev.lock.txt

precommit:
	pre-commit install

migrate:
	$(MANAGE) migrate

bootstrap-data:
	$(MANAGE) bootstrap_game_data --skip-images

# 传统 HTTP 开发服务器（不支持 WebSocket）
dev:
	$(MANAGE) runserver 0.0.0.0:8000

# ASGI 开发服务器（支持 WebSocket）
dev-ws:
	daphne -b 0.0.0.0 -p 8000 config.asgi:application

worker:
	celery -A config worker -l info

beat:
	mkdir -p $(LOCAL_STATE_DIR)
	celery -A config beat -l info --schedule $(LOCAL_STATE_DIR)/celerybeat-schedule

# Default to the hermetic unit-like suite, then document the real-service gate explicitly.
test:
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "  Running hermetic unit tests (SQLite / LocMem / InMemory channel layer)"
	@echo "  NOT verified: select_for_update row-locking, Redis semantics, real Channels"
	@echo "  Real external-service gate lives behind 'make test-real-services' (DJANGO_TEST_USE_ENV_SERVICES=1)"
	@echo "  Run 'make test-integration' if you only need the integration marker suite."
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@$(MAKE) test-unit

test-unit:
	$(PYTHON) -m pytest -m "not integration and not evidence"

test-unit-cov:
	$(PYTHON) -m coverage run -m pytest -m "not integration and not evidence"
	$(PYTHON) -m coverage report -m

test-critical:
	@if [ "$$DJANGO_TEST_USE_ENV_SERVICES" != "1" ]; then \
		echo "Refusing to skip critical concurrency integration tests; set DJANGO_TEST_USE_ENV_SERVICES=1 (or run 'make test-real-services')."; \
		exit 2; \
	fi
	@$(REAL_SERVICE_TEST_ENV) $(PYTHON) scripts/check_env_services_ready.py
	@$(REAL_SERVICE_TEST_ENV) $(PYTHON) -m pytest $(CRITICAL_INTEGRATION_TESTS) -q

test-real-services-preflight:
	@$(REAL_SERVICE_TEST_ENV) $(PYTHON) scripts/check_env_services_ready.py

test-real-services-up:
	@$(REAL_SERVICE_COMPOSE_ENV) docker compose -f docker-compose.yml up -d db redis

test-real-services-down:
	docker compose -f docker-compose.yml stop db redis

test-real-services:
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "  Running real external-service gate (DJANGO_TEST_USE_ENV_SERVICES=1)"
	@echo "  This includes the critical concurrency regression plus the integration marker suite."
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@$(REAL_SERVICE_TEST_ENV) $(MAKE) test-critical
	@$(REAL_SERVICE_TEST_ENV) $(MAKE) test-integration

test-virtual-player-gate-a:
	@if [ "$$DJANGO_TEST_USE_ENV_SERVICES" != "1" ]; then \
		echo "Refusing to run Gate A without the isolated MySQL/Redis test services."; \
		echo "Re-run with DJANGO_TEST_USE_ENV_SERVICES=1 make test-virtual-player-gate-a"; \
		exit 2; \
	fi
	@$(REAL_SERVICE_TEST_ENV) $(PYTHON) -m pytest $(VIRTUAL_PLAYER_GATE_A_CONTRACT_TESTS) -q
	@$(REAL_SERVICE_TEST_ENV) $(PYTHON) scripts/check_env_services_ready.py
	@$(REAL_SERVICE_TEST_ENV) $(PYTHON) -m pytest $(VIRTUAL_PLAYER_GATE_A_REAL_SERVICE_TESTS) -q

test-virtual-player-gate-d1:
	@if [ "$$DJANGO_TEST_USE_ENV_SERVICES" != "1" ]; then \
		echo "Refusing to run Gate D1 without the isolated MySQL/Redis test services."; \
		echo "Re-run with DJANGO_TEST_USE_ENV_SERVICES=1 make test-virtual-player-gate-d1"; \
		exit 2; \
	fi
	DJANGO_TEST_USE_ENV_SERVICES=0 PYTEST_ADDOPTS= $(PYTHON) -m pytest $(VIRTUAL_PLAYER_GATE_D1_CONTRACT_TESTS) -q
	@$(REAL_SERVICE_TEST_ENV) $(PYTHON) scripts/check_env_services_ready.py
	@$(REAL_SERVICE_TEST_ENV) PYTEST_ADDOPTS= $(PYTHON) -m pytest $(VIRTUAL_PLAYER_GATE_D1_CORE_REAL_SERVICE_TESTS) --reuse-db -q -s
	@$(REAL_SERVICE_TEST_ENV) PYTEST_ADDOPTS= $(PYTHON) -m pytest $(VIRTUAL_PLAYER_GATE_D1_ADJACENT_REAL_SERVICE_TESTS) --reuse-db -q

gate-d1-evidence:
	$(PYTHON) scripts/record_virtual_player_evidence.py --gate d1 --output "$(GATE_D1_EVIDENCE_OUTPUT)" --expected-git-commit "$(GATE_D1_EXPECTED_COMMIT)"

verify-gate-d1-evidence:
	$(PYTHON) scripts/record_virtual_player_evidence.py --gate d1 --verify --output "$(GATE_D1_EVIDENCE_OUTPUT)" --expected-git-commit "$(GATE_D1_EXPECTED_COMMIT)"

gate-e-readiness-evidence:
	VIRTUAL_PLAYER_EVIDENCE_ARTIFACT_DATE="$(VIRTUAL_PLAYER_EVIDENCE_ARTIFACT_DATE)" $(PYTHON) scripts/record_virtual_player_evidence.py --gate all --artifact-date "$(VIRTUAL_PLAYER_EVIDENCE_ARTIFACT_DATE)" --expected-git-commit "$(GATE_E_EXPECTED_COMMIT)" --replace

verify-gate-e-readiness-evidence:
	VIRTUAL_PLAYER_EVIDENCE_ARTIFACT_DATE="$(VIRTUAL_PLAYER_EVIDENCE_ARTIFACT_DATE)" $(PYTHON) scripts/record_virtual_player_evidence.py \
		--gate all --verify --artifact-date "$(VIRTUAL_PLAYER_EVIDENCE_ARTIFACT_DATE)" \
		--expected-git-commit "$(GATE_E_EXPECTED_COMMIT)"
	$(PYTHON) -m pytest \
		tests/test_virtual_player_gate_evidence_manifest.py \
		tests/test_virtual_player_gate_d1_evidence.py \
		tests/test_virtual_player_gate_e_readiness_evidence.py \
		tests/test_virtual_player_gate_activation_evidence.py \
		tests/test_virtual_player_evidence_recorder.py \
		tests/test_virtual_player_gate_d1_automation.py \
		tests/test_virtual_player_gate_e_automation.py \
		tests/test_pytest_configuration.py -q

test-virtual-player-gate-e:
	@if [ "$$DJANGO_TEST_USE_ENV_SERVICES" != "1" ]; then \
		echo "Refusing to run Gate E without the isolated MySQL/Redis test services."; \
		echo "Re-run with DJANGO_TEST_USE_ENV_SERVICES=1 make test-virtual-player-gate-e"; \
		exit 2; \
	fi
	DJANGO_TEST_USE_ENV_SERVICES=0 PYTEST_ADDOPTS= $(PYTHON) -m pytest $(VIRTUAL_PLAYER_GATE_E_CONTRACT_TESTS) -q
	@$(REAL_SERVICE_TEST_ENV) $(PYTHON) scripts/check_env_services_ready.py
	@$(REAL_SERVICE_TEST_ENV) PYTEST_ADDOPTS= $(PYTHON) -m pytest $(VIRTUAL_PLAYER_GATE_E_REAL_SERVICE_TESTS) --reuse-db -q -s

verify-virtual-player-gate-e:
	@$(MAKE) static-check
	@$(REAL_SERVICE_TEST_ENV) $(MAKE) test-virtual-player-gate-a DJANGO_TEST_USE_ENV_SERVICES=1
	@$(REAL_SERVICE_TEST_ENV) $(MAKE) test-virtual-player-gate-d1 DJANGO_TEST_USE_ENV_SERVICES=1
	@$(REAL_SERVICE_TEST_ENV) $(MAKE) test-virtual-player-gate-e DJANGO_TEST_USE_ENV_SERVICES=1

test-gates:
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "  Running the fixed verification workflow:"
	@echo "  1. Hermetic rapid gate"
	@echo "  2. Real external-service gate"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@$(MAKE) test-unit
	@if [ "$$DJANGO_TEST_USE_ENV_SERVICES" != "1" ]; then \
		echo "Refusing to skip the real external-service gate."; \
		echo "Re-run with DJANGO_TEST_USE_ENV_SERVICES=1 make test-gates"; \
		exit 2; \
	fi
	@$(MAKE) test-real-services

test-integration:
	@$(REAL_SERVICE_TEST_ENV) $(MAKE) test-real-services-preflight
	@$(REAL_SERVICE_TEST_ENV) $(PYTHON) -m pytest -m integration -q

test-all:
	$(PYTHON) -m pytest

cov:
	$(PYTHON) -m coverage run -m pytest -m "not integration and not evidence"
	$(PYTHON) -m coverage report -m

cov-html:
	$(PYTHON) -m coverage run -m pytest -m "not integration and not evidence"
	$(PYTHON) -m coverage html
	@echo "Open htmlcov/index.html"

# Code formatting and linting
format:
	$(BLACK) .
	$(ISORT) .

format-check:
	$(BLACK) --check .
	$(ISORT) --check-only .

lint-js:
	npm run check:js
	npm run test:js

lint: lint-js
	$(PYTHON) -m flake8 --jobs=1 $(FLAKE8_TARGETS)
	@$(PYTHON) -m mypy --version >/dev/null 2>&1 || { echo "mypy is required for lint. Run: make install"; exit 1; }
	$(PYTHON) -m mypy $(MYPY_TARGETS)

lint-strict:
	$(PYTHON) -m flake8 --jobs=1 $(FLAKE8_TARGETS)
	$(PYTHON) -m mypy $(MYPY_TARGETS)

static-check: format-check lint
	$(MANAGE) check
	$(MANAGE) makemigrations --check --dry-run
	$(PYTHON) -m compileall -q $(PYTHON_SOURCE_TARGETS)
	git diff --check

check: static-check
	@echo "Static checks completed!"

clean:
	rm -rf $(LOCAL_STATE_DIR) .pytest_cache .ruff_cache .mypy_cache htmlcov
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type f -name "*.py[cod]" -delete
