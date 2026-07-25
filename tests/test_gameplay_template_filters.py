from pathlib import Path

from gameplay.templatetags import gameplay_extras


def test_building_image_returns_expected_path_for_known_key():
    assert gameplay_extras.building_image("farm") == "images/buildings/农田.webp"
    assert gameplay_extras.building_image("oath_grove") == "images/buildings/结义林.webp"
    assert gameplay_extras.building_image("wall") == "images/buildings/城墙.webp"
    assert gameplay_extras.building_image("arrow_tower") == "images/buildings/箭塔.webp"


def test_city_defense_building_images_exist_in_static_assets():
    static_buildings = Path(__file__).resolve().parents[1] / "static" / "images" / "buildings"

    assert (static_buildings / "城墙.webp").is_file()
    assert (static_buildings / "箭塔.webp").is_file()


def test_building_image_returns_default_for_unknown_key():
    assert gameplay_extras.building_image("unknown") == "images/buildings/xianxuan.webp"
    assert gameplay_extras.building_image(None) == "images/buildings/xianxuan.webp"


def test_work_image_returns_expected_path_for_known_key():
    assert gameplay_extras.work_image("jiulou") == "images/works/酒楼.webp"
    assert gameplay_extras.work_image("chaguan") == "images/works/茶馆.webp"
    assert gameplay_extras.work_image("guozijian") == "images/works/国子监.webp"


def test_work_image_returns_default_for_unknown_key():
    assert gameplay_extras.work_image("unknown") == "images/works/酒楼.webp"
    assert gameplay_extras.work_image(None) == "images/works/酒楼.webp"
