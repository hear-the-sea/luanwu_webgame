"""本地静态服务直接发送轻量 WebP，透明建筑仍可按轮廓交互。"""

from pathlib import Path

import pytest
from django.contrib.staticfiles.views import serve
from django.test import RequestFactory
from PIL import Image

from config.static_finders import CatalogImageFinder


def test_season_webp_sources_preserve_size_and_alpha_and_reduce_transfer():
    finder = CatalogImageFinder()
    aliases = {
        public: source
        for public, source in finder.ASSETS.items()
        if public.startswith(("season-assets/", "season-troops/")) and public.endswith(".webp")
    }
    assert len(aliases) == 19
    source_bytes = optimized_bytes = 0
    for public, source in aliases.items():
        optimized = Path(finder.root) / source
        original = optimized.with_suffix("")
        assert finder.find(public) == str(optimized)
        source_bytes += original.stat().st_size
        optimized_bytes += optimized.stat().st_size
        with Image.open(original) as before, Image.open(optimized) as after:
            assert after.format == "WEBP"
            assert after.size == before.size
            if "A" in before.getbands() and before.getchannel("A").getextrema()[0] < 255:
                assert before.getchannel("A").tobytes() == after.getchannel("A").tobytes()
    assert optimized_bytes < source_bytes * 0.2


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["season-assets/mountain/inn.webp", "season-troops/shixuetieqi.webp"])
def test_django_static_server_delivers_webp_without_caddy(path):
    request = RequestFactory().get("/static/" + path)
    response = serve(request, path, insecure=True)
    try:
        assert response.status_code == 200
        assert response["Content-Type"] == "image/webp"
        header = next(iter(response.streaming_content))[:12]
        assert header[:4] == b"RIFF" and header[8:] == b"WEBP"
    finally:
        response.close()
