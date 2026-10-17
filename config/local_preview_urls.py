"""Local preview routes, enabled exclusively by settings.local_preview."""

import yaml
from django.conf import settings
from django.http import FileResponse, Http404, JsonResponse
from django.urls import path

from config.urls import urlpatterns as game_urlpatterns


def gallery(request):
    return FileResponse(
        (settings.BASE_DIR / "scripts" / "guest_avatar_preview.html").open("rb"),
        content_type="text/html; charset=utf-8",
    )


def avatar_catalog(request):
    entries = []
    for source in sorted((settings.BASE_DIR / "data" / "guests").glob("*.yaml")):
        payload = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
        for rarity, heroes in (payload.get("heroes") or payload.get("hero_roster") or {}).items():
            for hero in heroes:
                avatar = hero.get("avatar") or ""
                entries.append(
                    {
                        "key": hero["key"],
                        "name": hero["name"],
                        "rarity": rarity,
                        "avatar": avatar,
                        "flavor": hero.get("flavor", ""),
                        "archetype": hero.get("archetype", ""),
                        "source": source.name,
                        "available": bool(
                            avatar and (settings.BASE_DIR / "data" / "images" / "guests" / avatar).is_file()
                        ),
                    }
                )
    return JsonResponse({"guests": entries}, json_dumps_params={"ensure_ascii": False})


def source_image(request, image_path):
    root = (settings.BASE_DIR / "data" / "images" / "guests").resolve()
    target = (root / image_path).resolve()
    if (
        not target.is_relative_to(root)
        or not target.is_file()
        or target.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}
    ):
        raise Http404
    return FileResponse(target.open("rb"))


urlpatterns = [
    path("local-preview/guests/", gallery),
    path("local-preview/guests/catalog.json", avatar_catalog),
    path("local-preview/guests/images/<path:image_path>", source_image),
] + game_urlpatterns
