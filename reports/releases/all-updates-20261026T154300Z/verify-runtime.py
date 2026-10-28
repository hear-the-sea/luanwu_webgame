import json
import os
import re

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()
from django.conf import settings
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.staticfiles.storage import staticfiles_storage
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import RequestFactory
from django.urls import reverse

from config.celery import app
from config.static_finders import CatalogImageFinder
from gameplay.models import Manor, Season
from gameplay.views.season import season_view

connection.ensure_connection()


def readonly(execute, sql, params, many, context):
    assert sql.lstrip().split(None, 1)[0].upper() in {
        "SELECT",
        "SHOW",
        "EXPLAIN",
        "DESCRIBE",
    }, "Read-only verification refused a write"
    return execute(sql, params, many, context)


with connection.execute_wrapper(readonly):
    executor = MigrationExecutor(connection)
    assert executor.migration_plan(executor.loader.graph.leaf_nodes()) == []
    manor = Manor.objects.select_related("user").filter(user__is_active=True).order_by("id").first()
    req = RequestFactory().get(reverse("gameplay:season"), secure=True, HTTP_HOST="luanwu.top")
    req.user = manor.user
    req.session = {}
    req._messages = FallbackStorage(req)
    response = season_view(req)
    assert response.status_code == 200
    body = response.content.decode()
    assert "data-season-page" in body and "/static/js/season." in body and "山海争鸣·第一季" in body
    season = Season.objects.get(closed=False)
    assets = [
        "css/style.css",
        "css/season.css",
        "css/season-visual.css",
        "css/map-wooden-sign.css",
        "js/season.js",
        "js/jail-page.js",
        "images/season-icons.svg",
        "images/season-item-parcel.svg",
    ]
    assets.extend(k for k in CatalogImageFinder.ASSETS if k.startswith(("season-assets/", "season-troops/")))
    app.loader.import_default_modules()
    tasks = ["gameplay.settle_seasons", "gameplay.advance_season_expedition"]
    assert all(t in app.tasks for t in tasks)
    routes = {t: app.amqp.router.route({}, t).get("queue").name for t in tasks}
    print(
        json.dumps(
            {
                "pending_migrations": 0,
                "authenticated_season_render": response.status_code,
                "season_page_bytes": len(response.content),
                "readonly_database_writes": 0,
                "season": {
                    "id": season.pk,
                    "name": season.name,
                    "starts_at": season.starts_at.isoformat(),
                    "ends_at": season.ends_at.isoformat(),
                    "sites": season.sites.count(),
                    "factions": season.faction_states.count(),
                },
                "season_tasks": tasks,
                "task_routes": routes,
                "beat_schedule": str(settings.CELERY_BEAT_SCHEDULE["settle-seasons"]["schedule"]),
                "assets": {f: staticfiles_storage.url(f) for f in assets},
                "html_static_assets": sorted(set(re.findall(r'(?:src|href)="(/static/[^"]+)"', body))),
            },
            ensure_ascii=False,
        )
    )
