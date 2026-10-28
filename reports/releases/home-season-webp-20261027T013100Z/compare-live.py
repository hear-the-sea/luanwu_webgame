import hashlib
import json
from pathlib import Path

files = [
    ".dockerignore",
    "config/static_finders.py",
    "gameplay/selectors/home.py",
    "gameplay/selectors/home_season.py",
    "gameplay/selectors/season.py",
    "gameplay/templates/gameplay/season.html",
    "gameplay/templates/gameplay/season/training_summary.html",
    "gameplay/views/season.py",
    "static/css/landing-page.css",
    "templates/landing.html",
    "data/images/pvp/layers-v1/mountain/background.png.webp",
    "data/images/pvp/layers-v1/mountain/inn.png.webp",
    "data/images/pvp/layers-v1/mountain/gate.png.webp",
    "data/images/pvp/layers-v1/mountain/ruins.png.webp",
    "data/images/pvp/layers-v1/river/background.png.webp",
    "data/images/pvp/layers-v1/river/town.png.webp",
    "data/images/pvp/layers-v1/river/market.png.webp",
    "data/images/pvp/layers-v1/river/warehouse.png.webp",
    "data/images/pvp/layers-v1/river/docks.png.webp",
    "data/images/pvp/layers-v1/forest/background.png.webp",
    "data/images/pvp/layers-v1/forest/camp.png.webp",
    "data/images/pvp/layers-v1/forest/granary.png.webp",
    "data/images/pvp/layers-v1/forest/lodge.png.webp",
    "data/images/troops/daosheng.png.webp",
    "data/images/troops/qiangwang.png.webp",
    "data/images/troops/jiansheng.png.webp",
    "data/images/troops/quansheng.png.webp",
    "data/images/troops/jianshen.png.webp",
    "data/images/troops/shixuetieqi.png.webp",
]
print(
    json.dumps(
        {
            f: hashlib.sha256(Path("/app", f).read_bytes()).hexdigest() if Path("/app", f).is_file() else None
            for f in files
        }
    )
)
