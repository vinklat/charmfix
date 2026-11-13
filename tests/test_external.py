"""Independent checks of repaired generated fixtures: Pillow, exiftool and Apple ImageIO."""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest
from helpers import (
    SIX_PART_DATE,
    E,
    broken_charmera_tiff,
    build_tiff,
    charmera_layout,
    rational,
    short,
    standard_exif_entries,
    standard_ifd0_entries,
    with_exif,
)
from PIL import Image, ImageChops

from charmfix import jpeg
from charmfix.pipeline import rebuild
from charmfix.repair import RepairOptions

MTIME = datetime(2026, 5, 1, 10, 20, 30).timestamp()

FIXTURES: dict[str, Callable[[bytes, bytes], bytes]] = {
    "no-exif-charmera": lambda base, thumb: charmera_layout(base),
    "known-defects": lambda base, thumb: with_exif(base, broken_charmera_tiff()),
    "unreadable-exif": lambda base, thumb: with_exif(base, b"junk junk junk"),
    "thumbnail-gps": lambda base, thumb: with_exif(
        base,
        build_tiff(
            standard_ifd0_entries(SIX_PART_DATE),
            [*standard_exif_entries(SIX_PART_DATE), rational(0x829A, (1, 125))],
            [E(0, 1, 4, bytes([2, 3, 0, 0]))],
            ifd1=[short(0x0103, 6)],
            thumbnail=thumb,
        ),
    ),
}


@pytest.fixture(params=sorted(FIXTURES), ids=str)
def repaired(
    request: pytest.FixtureRequest, base_jpeg: bytes, tmp_path: Path
) -> tuple[bytes, Path]:
    thumb = io.BytesIO()
    Image.new("RGB", (16, 12), "blue").save(thumb, "JPEG")
    src = FIXTURES[request.param](base_jpeg, thumb.getvalue())
    _, _, out = rebuild(src, MTIME, datetime(2026, 11, 6), RepairOptions())
    dest = tmp_path / f"{request.param}.jpg"
    dest.write_bytes(out)
    return src, dest


def test_pixels_identical(repaired: tuple[bytes, Path]) -> None:
    src, dest = repaired
    with Image.open(io.BytesIO(src)) as a, Image.open(dest) as b:
        assert ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox() is None
    assert jpeg.image_digest(jpeg.parse(dest.read_bytes())) == jpeg.image_digest(jpeg.parse(src))


@pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool not installed")
def test_exiftool_validate(repaired: tuple[bytes, Path]) -> None:
    _, dest = repaired
    proc = subprocess.run(
        ["exiftool", "-validate", "-warning", "-error", "-a", str(dest)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Validate                        : OK" in proc.stdout, proc.stdout


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("sips"), reason="macOS only")
def test_apple_imageio(repaired: tuple[bytes, Path]) -> None:
    _, dest = repaired
    proc = subprocess.run(
        ["sips", "-g", "pixelWidth", "-g", "pixelHeight", "-g", "creation", str(dest)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "pixelWidth: 64" in proc.stdout and "pixelHeight: 48" in proc.stdout
    assert "creation: 20" in proc.stdout
