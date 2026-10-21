"""WebP 副本保留透明度、尺寸、动画及原图，并支持重复运行。"""

from PIL import Image

from scripts.prepare_webp_images import prepare_image


def test_dry_run_does_not_write_files(tmp_path):
    source = tmp_path / "asset.png"
    Image.new("RGBA", (12, 10), (20, 30, 40, 0)).save(source)
    assert prepare_image(source)["status"] == "planned"
    assert not source.with_name("asset.png.webp").exists()


def test_conversion_preserves_alpha_dimensions_and_source_and_is_repeatable(tmp_path):
    source = tmp_path / "asset.png"
    image = Image.new("RGBA", (12, 10), (20, 30, 40, 0))
    image.putpixel((0, 0), (200, 100, 50, 128))
    image.save(source)
    original = source.read_bytes()
    assert prepare_image(source, apply=True)["status"] == "converted"
    target = source.with_name("asset.png.webp")
    with Image.open(target) as result:
        assert result.format == "WEBP"
        assert result.size == (12, 10)
        assert result.getpixel((0, 0))[3] == 128
        assert result.getpixel((1, 1))[3] == 0
    modified = target.stat().st_mtime_ns
    assert prepare_image(source, apply=True)["status"] == "current"
    assert target.stat().st_mtime_ns == modified
    assert source.read_bytes() == original


def test_source_changes_regenerate_companion(tmp_path):
    source = tmp_path / "asset.jpg"
    Image.new("RGB", (12, 10), "red").save(source)
    prepare_image(source, apply=True)
    Image.new("RGB", (24, 20), "blue").save(source)
    assert prepare_image(source, apply=True)["status"] == "converted"
    with Image.open(source.with_name("asset.jpg.webp")) as result:
        assert result.size == (24, 20)


def test_animated_png_preserves_frames(tmp_path):
    source = tmp_path / "animation.png"
    Image.new("RGBA", (12, 10), "red").save(
        source, save_all=True, append_images=[Image.new("RGBA", (12, 10), "blue")], duration=[100, 200], loop=0
    )
    prepare_image(source, apply=True)
    with Image.open(source.with_name("animation.png.webp")) as result:
        assert result.n_frames == 2
        assert result.info["loop"] == 0
