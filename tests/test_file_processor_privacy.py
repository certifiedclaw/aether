import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image, PngImagePlugin

from actions.file_processor import _image_metadata_report, _strip_image_metadata


@pytest.mark.parametrize("suffix, image_format", [
    (".jpg", "JPEG"), (".png", "PNG"), (".webp", "WEBP"),
])
def test_strip_metadata_creates_verified_copy_without_changing_original(
    tmp_path, suffix, image_format
):
    source = tmp_path / f"photo{suffix}"
    image = Image.new("RGB", (20, 12), color=(90, 120, 150))
    exif = Image.Exif()
    exif[0x010F] = "Private Camera Model"
    save_options = {"exif": exif}
    if image_format == "PNG":
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("Author", "Private Author")
        save_options = {"exif": exif, "pnginfo": metadata}
    elif image_format == "WEBP":
        save_options["lossless"] = True
    image.save(source, image_format, **save_options)
    original_bytes = source.read_bytes()

    report = _image_metadata_report(source)
    assert "Make" in report or "image info: exif" in report.lower()
    assert "Private Camera Model" not in report

    result = _strip_image_metadata(source)
    output = tmp_path / f"photo_metadata_removed{suffix}"
    assert output.exists()
    assert "original was not changed" in result
    assert source.read_bytes() == original_bytes
    assert "Make" in _image_metadata_report(source)
    with Image.open(output) as clean:
        assert clean.size == (20, 12)
        assert not clean.getexif()
        assert not _privacy_info(clean.info)


def test_strip_metadata_refuses_to_overwrite_existing_copy(tmp_path):
    source = tmp_path / "image.png"
    Image.new("RGB", (4, 4)).save(source)
    output = tmp_path / "image_metadata_removed.png"
    output.write_bytes(b"keep this")

    result = _strip_image_metadata(source)

    assert result.startswith("Output already exists")
    assert output.read_bytes() == b"keep this"


def test_strip_metadata_rejects_unsupported_and_animated_images(tmp_path):
    tiff = tmp_path / "image.tiff"
    Image.new("RGB", (4, 4)).save(tiff)
    assert _strip_image_metadata(tiff).startswith("Metadata removal supports")

    animated = tmp_path / "animated.webp"
    first = Image.new("RGB", (4, 4), "red")
    second = Image.new("RGB", (4, 4), "blue")
    first.save(animated, "WEBP", save_all=True, append_images=[second], duration=100)
    result = _strip_image_metadata(animated)
    assert "animated images are not supported" in result
    assert not (tmp_path / "animated_metadata_removed.webp").exists()


def _privacy_info(info):
    return {
        key.lower() for key, value in info.items()
        if value and key.lower() in {
            "exif", "xmp", "xml:com.adobe.xmp", "icc_profile", "comment", "artist",
            "author", "description", "title", "software", "timestamp", "creation_time",
            "make", "model", "copyright", "date", "gps", "location", "serial",
            "camera", "usercomment",
        }
    }
