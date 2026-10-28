import concurrent.futures
import json
import urllib.parse
import urllib.request
from pathlib import Path

p = Path(__file__).resolve().parent
runtime = json.loads((p / "runtime-verification.json").read_text())
paths = {
    urllib.parse.urldefrag(path)[0]
    for path in [
        *runtime["assets"].values(),
        *runtime["html_static_assets"],
        "/",
        "/health/live",
        "/accounts/login/",
        "/manor/season/",
    ]
}


def check(path):
    with urllib.request.urlopen("https://luanwu.top" + path, timeout=20) as response:
        body = response.read()
        assert response.status == 200 and body, path
        kind = response.headers.get("Content-Type", "")
        cache = response.headers.get("Cache-Control", "")
        if path.startswith(("/static/season-assets/", "/static/season-troops/")) and path.endswith(".webp"):
            assert kind == "image/webp" and body[:4] == b"RIFF" and body[8:12] == b"WEBP", path
            assert "immutable" in cache, path
        return {
            "path": path,
            "status": response.status,
            "bytes": len(body),
            "content_type": kind,
            "cache_control": cache,
            "redirected": response.url != "https://luanwu.top" + path,
        }


with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    rows = list(pool.map(check, sorted(paths)))
(p / "public-verification.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))
print(
    json.dumps(
        {
            "public_paths": len(rows),
            "all_status_200": True,
            "verified_webp": sum(
                row["path"].endswith(".webp")
                and row["path"].startswith(("/static/season-assets/", "/static/season-troops/"))
                for row in rows
            ),
        }
    )
)
