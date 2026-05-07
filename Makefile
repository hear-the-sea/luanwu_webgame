PYTHON ?= python3
MANAGE ?= $(PYTHON) manage.py
LOCAL_STATE_DIR ?= .local
FLAKE8_TARGETS ?= accounts battle gameplay guests guilds trade core websocket config tests
MYPY_TARGETS ?= accounts battle common config core gameplay guests guilds tasks trade websocket
CRITICAL_INTEGRATION_TESTS ?= \
	tests/test_raid_concurrency_integration.py \
	tests/test_raid_scout_concurrency_integration.py \
	tests/test_mission_concurrency_integration.py \
	tests/test_guest_recruitment_concurrency_integration.py \
	tests/test_guest_equipment_concurrency_integration.py \
	tests/test_arena_coop_concurrency_integration.py \
	tests/test_trade_auction_concurrency_integration.py \
	tests/test_work_service_concurrency.py

.PHONY: install install-unpinned install-lock install-dev-lock migrate bootstrap-data dev dev-ws worker beat test test-unit test-unit-cov test-critical test-integration test-all format lint lint-js lint-strict check clean lock lock-dev test-real-services-up test-real-services-down test-real-services test-real-services-preflight test-gates cov cov-html

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
	$(PYTHON) -m pytest -m "not integration"

test-unit-cov:
	$(PYTHON) -m coverage run -m pytest -m "not integration"
	$(PYTHON) -m coverage report -m

test-critical:
	@if [ "$$DJANGO_TEST_USE_ENV_SERVICES" = "1" ]; then \
		$(MAKE) test-real-services-preflight && \
		$(PYTHON) -m pytest $(CRITICAL_INTEGRATION_TESTS) -q; \
	else \
		echo "Skipping critical concurrency integration tests; set DJANGO_TEST_USE_ENV_SERVICES=1 (or run 'make test-real-services') to enable non-SQLite verification."; \
	fi

test-real-services-preflight:
	@$(PYTHON) scripts/check_env_services_ready.py

test-real-services-up:
	docker compose -f docker-compose.yml up -d db redis

test-real-services-down:
	docker compose -f docker-compose.yml stop db redis

test-real-services:
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "  Running real external-service gate (DJANGO_TEST_USE_ENV_SERVICES=1)"
	@echo "  This includes the critical concurrency regression plus the integration marker suite."
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@DJANGO_TEST_USE_ENV_SERVICES=1 $(MAKE) test-critical
	@DJANGO_TEST_USE_ENV_SERVICES=1 $(MAKE) test-integration

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
	@$(MAKE) test-real-services-preflight
	DJANGO_TEST_USE_ENV_SERVICES=1 $(PYTHON) -m pytest -m integration -q

test-all:
	$(PYTHON) -m pytest

cov:
	$(PYTHON) -m coverage run -m pytest -m "not integration"
	$(PYTHON) -m coverage report -m

cov-html:
	$(PYTHON) -m coverage run -m pytest -m "not integration"
	$(PYTHON) -m coverage html
	@echo "Open htmlcov/index.html"

# Code formatting and linting
format:
	black .
	isort .

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

check: format lint
	@echo "Code formatting and linting completed!"

clean:
	rm -rf $(LOCAL_STATE_DIR) .pytest_cache .ruff_cache .mypy_cache htmlcov
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type f -name "*.py[cod]" -delete
