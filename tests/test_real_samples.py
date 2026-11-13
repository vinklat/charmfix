"""Every real camera sample in tests/samples/*/*.jpg goes through the full pipeline."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest
from helpers import REAL_SAMPLES
from PIL import Image, ImageChops

from charmfix import jpeg
from charmfix.discovery import Candidate
from charmfix.exif_read import read_exif
from charmfix.pipeline import Job, RunOptions, process_file
from charmfix.repair import RepairOptions

PLANTED = datetime(2026, 7, 14, 18, 45, 12)

pytestmark = pytest.mark.skipif(not REAL_SAMPLES, reason="no real samples in tests/samples")


def run_one(src: Path, out_dir: Path, *, assume_charmera: bool = True) -> tuple[object, Path]:
    dest = out_dir / src.name
    opts = RunOptions(out_dir, repair=RepairOptions(assume_charmera=assume_charmera))
    result = process_file(Job(Candidate(src, Path(src.name)), dest), opts)
    return result, dest


@pytest.fixture(params=REAL_SAMPLES, ids=lambda p: p.parent.name)
def sample(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    # The repo copy's mtime is not the capture time; plant a known one.
    original: Path = request.param
    src = tmp_path / "card" / original.name
    src.parent.mkdir()
    shutil.copyfile(original, src)
    os.utime(src, (PLANTED.timestamp(), PLANTED.timestamp()))
    return src


def test_real_sample_repaired(sample: Path, tmp_path: Path) -> None:
    result, dest = run_one(sample, tmp_path / "out")
    assert result.status == "repaired", result.error  # type: ignore[attr-defined]
    assert result.timestamp_source == "file_mtime"  # type: ignore[attr-defined]
    assert result.identity == "charmera(assumed)"  # type: ignore[attr-defined]

    src_bytes, out_bytes = sample.read_bytes(), dest.read_bytes()
    src_jf, out_jf = jpeg.parse(src_bytes), jpeg.parse(out_bytes)
    exif = read_exif(out_jf.exif_tiff() or b"")
    assert not exif.warnings

    def text(ifd: str, tag: int) -> str:
        rv = exif.get(ifd, tag)
        assert rv is not None and isinstance(rv.value, bytes)
        return rv.value.rstrip(b"\x00").decode()

    expected = PLANTED.strftime("%Y:%m:%d %H:%M:%S")
    assert text("Exif", 0x9003) == text("Exif", 0x9004) == text("0th", 0x0132) == expected
    assert (text("0th", 0x010F), text("0th", 0x0110)) == ("Kodak", "Charmera")
    assert exif.get("Exif", 0xA002).value == (1440,)  # type: ignore[union-attr]
    assert exif.get("Exif", 0xA003).value == (1080,)  # type: ignore[union-attr]
    assert exif.get("0th", 0x0112).value == (1,)  # type: ignore[union-attr]

    # image bytes, pixels and the GPEncoder comment are untouched
    assert jpeg.image_digest(out_jf) == jpeg.image_digest(src_jf)
    assert out_bytes.endswith(src_bytes[2:])  # only an APP1 was inserted after SOI
    assert out_jf.comments == src_jf.comments
    with Image.open(sample) as a, Image.open(dest) as b:
        assert ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox() is None

    # output keeps the source times
    assert dest.stat().st_mtime == PLANTED.timestamp()

    # processing the output again changes nothing
    again, again_dest = run_one(dest, tmp_path / "out2")
    assert again.status == "already_valid"  # type: ignore[attr-defined]
    assert again_dest.read_bytes() == out_bytes


def test_real_sample_without_assumption(sample: Path, tmp_path: Path) -> None:
    result, dest = run_one(sample, tmp_path / "out", assume_charmera=False)
    assert result.identity == "generalplus-unconfirmed"  # type: ignore[attr-defined]
    exif = read_exif(jpeg.parse(dest.read_bytes()).exif_tiff() or b"")
    assert exif.get("0th", 0x010F) is None and exif.get("0th", 0x0110) is None


@pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool not installed")
def test_real_sample_exiftool_validate(sample: Path, tmp_path: Path) -> None:
    _, dest = run_one(sample, tmp_path / "out")
    proc = subprocess.run(
        ["exiftool", "-validate", "-warning", "-error", "-a", "-s3", str(dest)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout.strip() == "OK", proc.stdout


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("sips"), reason="macOS only")
def test_real_sample_apple_imageio(sample: Path, tmp_path: Path) -> None:
    _, dest = run_one(sample, tmp_path / "out")
    proc = subprocess.run(
        ["sips", "-g", "pixelWidth", "-g", "pixelHeight", "-g", "creation", str(dest)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "pixelWidth: 1440" in proc.stdout
    assert "pixelHeight: 1080" in proc.stdout
    assert f"creation: {PLANTED.strftime('%Y:%m:%d %H:%M:%S')}" in proc.stdout
