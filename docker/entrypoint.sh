#!/usr/bin/env bash
set -euo pipefail

export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings}"

python - <<'PY'
import os
import time

import django
from django.db import connections
from django.db.utils import OperationalError

os.environ.setdefault("DJANGO_SETTINGS_MODULE", os.environ.get("DJANGO_SETTINGS_MODULE", "config.settings"))
django.setup()

deadline = time.time() + int(os.environ.get("DJANGO_WAIT_FOR_DB_SECONDS", "60"))
while True:
    try:
        connections["default"].ensure_connection()
        break
    except OperationalError as exc:
        if time.time() > deadline:
            raise
        print(f"[entrypoint] waiting for db... ({exc})")
        time.sleep(1)
PY

python - <<'PY'
import os
import time

import django
from django.conf import settings
from django.core.cache import cache

os.environ.setdefault("DJANGO_SETTINGS_MODULE", os.environ.get("DJANGO_SETTINGS_MODULE", "config.settings"))
django.setup()

deadline = time.time() + int(os.environ.get("DJANGO_WAIT_FOR_REDIS_SECONDS", "60"))
allow_degraded = os.environ.get("DJANGO_ALLOW_CACHE_STARTUP_DEGRADED")
if allow_degraded is None:
    allow_degraded = "1" if settings.DEBUG or getattr(settings, "RUNNING_TESTS", False) else "0"
key = "startup:cache:ping"
while True:
    try:
        cache.set(key, "1", timeout=5)
        if cache.get(key) == "1":
            break
    except Exception as exc:
        if time.time() > deadline:
            message = f"[entrypoint] cache/redis not ready after timeout ({exc})"
            if allow_degraded == "1":
                print(message + ", continuing...")
                break
            raise RuntimeError(message)
        print(f"[entrypoint] waiting for cache/redis... ({exc})")
        time.sleep(1)
PY

if [[ "${DJANGO_RUN_MIGRATIONS:-0}" == "1" ]]; then
  python manage.py migrate --noinput
  python manage.py repair_item_images
fi

if [[ "${DJANGO_SYNC_GUEST_TEMPLATES:-0}" == "1" && "${1:-}" != "celery" ]]; then
  guest_templates_file="${DJANGO_GUEST_TEMPLATES_FILE:-data/guest_templates.yaml}"
  load_args=(python manage.py load_guest_templates --file "$guest_templates_file")

  if [[ -n "${DJANGO_GUEST_SKILLS_FILE:-}" ]]; then
    load_args+=(--skills-file "$DJANGO_GUEST_SKILLS_FILE")
  fi

  if [[ -n "${DJANGO_GUEST_HEROES_DIR:-}" ]]; then
    load_args+=(--heroes-dir "$DJANGO_GUEST_HEROES_DIR")
  fi

  if [[ "${DJANGO_GUEST_TEMPLATES_SKIP_IMAGES:-0}" == "1" ]]; then
    load_args+=(--skip-images)
  fi

  "${load_args[@]}"
fi

if [[ "${DJANGO_COLLECTSTATIC:-0}" == "1" ]]; then
  python manage.py collectstatic --noinput
  python scripts/prepare_webp_images.py --root staticfiles --root media --apply
fi

exec "$@"
