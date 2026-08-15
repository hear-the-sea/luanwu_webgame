from pathlib import Path

from django.contrib.staticfiles import finders

from config.static_finders import CatalogImageFinder


def test_catalog_image_finder_exposes_only_explicit_template_assets():
    finder = CatalogImageFinder()

    assert finder.check() == []
    assert finder.find("items/chunqiu_coin.png") == str(Path(finder.root) / "items" / "chunqiu_coin.png")
    assert finder.find("items/large.png") == str(Path(finder.root) / "items" / "large.png")
    assert finder.find("buildings/guild-residence.webp") == str(
        Path(finder.root) / "buildings" / "guild-residence.webp"
    )

    listed_paths = {path for path, _storage in finder.list([])}
    assert listed_paths == {
        "buildings/guild-residence.webp",
        "items/chunqiu_coin.png",
        "items/large.png",
    }


def test_catalog_image_finder_does_not_expose_raw_catalog_files():
    finder = CatalogImageFinder()

    assert finder.find("buildings/竞技场.png") == []
    assert finder.find("guests/zhao_yun.png") == []
    assert finders.find("buildings/竞技场.png") is None
    assert finders.find("guests/zhao_yun.png") is None


def test_catalog_image_sources_are_reincluded_in_docker_context():
    dockerignore = Path(__file__).resolve().parents[1] / ".dockerignore"
    rules = {
        line.strip()
        for line in dockerignore.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert {
        "!data/images/items/",
        "!data/images/items/**",
        "!data/images/buildings/",
        "!data/images/buildings/guild-residence.webp",
    } <= rules
