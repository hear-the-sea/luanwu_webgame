import hashlib
import json
from pathlib import Path

files = [
    "battle/defender_setup.py",
    "config/asgi.py",
    "core/utils/image_utils.py",
    "data/mission_templates.yaml",
    "gameplay/services/arena/coop_lifecycle.py",
    "gameplay/services/arena/core.py",
    "gameplay/services/arena/lifecycle_helpers.py",
    "gameplay/services/arena/state_helpers.py",
    "gameplay/services/arena/wheel_core.py",
    "gameplay/services/missions_impl/launch_post_actions.py",
    "gameplay/services/missions_impl/launch_resilience.py",
    "scripts/prepare_webp_images.py",
    "static/js/jail-page.js",
    "data/images/guests/33.png",
    "data/images/guests/monkey/2b4a302e-832b-4fd3-b7b5-a6fbb836555d.png",
    "data/images/guests/monkey/orange/1.png",
    "data/images/guests/monkey/orange/111.png",
    "data/images/guests/monkey/orange/1233.png",
    "data/images/guests/monkey/orange/sima.png",
    "data/images/guests/monkey/temp.png",
]
print(
    json.dumps(
        {
            f: hashlib.sha256(Path("/app", f).read_bytes()).hexdigest() if Path("/app", f).is_file() else None
            for f in files
        }
    )
)
