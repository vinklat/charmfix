"""Random damage must only ever produce a clean failure, never a crash or a hang."""

from __future__ import annotations

from datetime import datetime

from helpers import (
    REAL_SAMPLES,
    broken_charmera_tiff,
    charmera_layout,
    make_jpeg,
    valid_tiff,
    with_exif,
)
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from charmfix import jpeg
from charmfix.exif_read import read_exif
from charmfix.exif_write import ExifTooLargeError
from charmfix.pipeline import analyse, build
from charmfix.repair import RepairOptions
from charmfix.verify import VerificationError

BASE = make_jpeg(16, 16)
SEEDS = [
    with_exif(BASE, broken_charmera_tiff()),
    with_exif(BASE, valid_tiff(16, 16)),
    charmera_layout(BASE),
]
if REAL_SAMPLES:
    SEEDS.append(REAL_SAMPLES[0].read_bytes()[:4096])  # header + some scan data
TIFFS = [broken_charmera_tiff(), valid_tiff()]
NOW = datetime(2026, 11, 6)
EXPECTED = (jpeg.JpegError, ExifTooLargeError, VerificationError)


@st.composite
def damaged(draw: st.DrawFn, seeds: list[bytes]) -> bytes:
    data = bytearray(draw(st.sampled_from(seeds)))
    for _ in range(draw(st.integers(0, 8))):
        pos = draw(st.integers(0, len(data) - 1))
        data[pos] = draw(st.integers(0, 255))
    if draw(st.booleans()):
        data = data[: draw(st.integers(0, len(data)))]
    return bytes(data)


@settings(max_examples=400, deadline=1000, suppress_health_check=[HealthCheck.too_slow])
@given(damaged(SEEDS))
def test_analyse_and_build_never_crash(data: bytes) -> None:
    try:
        jf, plan = analyse(data, 1_700_000_000, NOW, RepairOptions())
        out = build(jf, plan, data)
    except EXPECTED:
        return
    # Whatever succeeded must have kept the image data.
    assert jpeg.image_data(jpeg.parse(out)) == jpeg.image_data(jpeg.parse(data))


@settings(max_examples=600, deadline=1000)
@given(damaged(TIFFS))
def test_exif_reader_never_needs_its_safety_net(data: bytes) -> None:
    ex = read_exif(data)
    assert not any("reader error" in w for w in ex.warnings), ex.warnings


@settings(max_examples=200, deadline=1000)
@given(st.binary(max_size=512))
def test_exif_reader_on_random_bytes(data: bytes) -> None:
    ex = read_exif(b"MM\x00*\x00\x00\x00\x08" + data)
    assert not any("reader error" in w for w in ex.warnings), ex.warnings
