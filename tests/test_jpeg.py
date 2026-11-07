from __future__ import annotations

import pytest
from helpers import (
    REAL_SAMPLES,
    charmera_layout,
    exif_segment,
    insert_after_soi,
    segment,
    valid_tiff,
    with_exif,
)

from charmfix import jpeg


def test_parse_pillow_jpeg(base_jpeg: bytes) -> None:
    jf = jpeg.parse(base_jpeg)
    assert (jf.width, jf.height) == (64, 48)
    assert jf.sof_marker == 0xC0
    assert jf.has_eoi
    assert jf.segments[0].is_jfif
    assert jf.exif_tiff() is None


@pytest.mark.parametrize("path", REAL_SAMPLES, ids=lambda p: p.parent.name)
def test_parse_real_sample_layout(path) -> None:  # type: ignore[no-untyped-def]
    jf = jpeg.parse(path.read_bytes())
    assert (jf.width, jf.height) == (1440, 1080)
    assert [s.marker for s in jf.segments][:2] == [0xC0, 0xFE]
    assert jf.comments[0].startswith(b"GPEncoder")
    assert jf.exif_tiff() is None


def test_image_data_excludes_only_app_and_com(base_jpeg: bytes) -> None:
    jf = jpeg.parse(base_jpeg)
    noisy = insert_after_soi(base_jpeg, segment(0xED, b"Photoshop 3.0\x00x"), segment(0xFE, b"hi"))
    assert jpeg.image_data(jpeg.parse(noisy)) == jpeg.image_data(jf)
    assert jpeg.image_data(jf).endswith(base_jpeg[-100:])


def test_splice_keeps_every_other_segment(base_jpeg: bytes) -> None:
    xmp = segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta/>")
    app13 = segment(0xED, b"Photoshop 3.0\x00data")
    com = segment(0xFE, b"GPEncoder")
    src = insert_after_soi(base_jpeg, exif_segment(b"garbage"), xmp, app13, com)
    # put a JFIF APP0 first again: Pillow's APP0 is now after our inserted segments
    jf = jpeg.parse(src)
    out = jpeg.splice(jf, b"Exif\x00\x00" + valid_tiff())
    out_jf = jpeg.parse(out)
    assert len(out_jf.exif_segments) == 1
    assert out_jf.segments[0].is_exif
    raw = [src[s.start : s.end] for s in jf.segments if not s.is_exif]
    assert [out[s.start : s.end] for s in out_jf.segments if not s.is_exif] == raw
    assert jpeg.image_data(out_jf) == jpeg.image_data(jf)


def test_splice_puts_exif_after_leading_jfif(base_jpeg: bytes) -> None:
    out = jpeg.splice(jpeg.parse(base_jpeg), b"Exif\x00\x00" + valid_tiff())
    markers = [s.marker for s in jpeg.parse(out).segments]
    assert markers[:2] == [0xE0, 0xE1]


def test_splice_charmera_layout_keeps_com_in_place(base_jpeg: bytes) -> None:
    src = charmera_layout(base_jpeg)
    jf = jpeg.parse(src)
    out = jpeg.splice(jf, b"Exif\x00\x00" + valid_tiff())
    assert out[2:4] == b"\xff\xe1"
    markers = [s.marker for s in jpeg.parse(out).segments]
    assert markers[:3] == [0xE1, 0xC0, 0xFE]
    assert out.endswith(src[2:])  # everything after SOI unchanged, only APP1 inserted


def test_splice_replaces_multiple_exif_segments(base_jpeg: bytes) -> None:
    src = with_exif(with_exif(base_jpeg, valid_tiff()), valid_tiff())
    out = jpeg.splice(jpeg.parse(src), b"Exif\x00\x00" + valid_tiff())
    assert len(jpeg.parse(out).exif_segments) == 1


def test_fill_bytes_are_tolerated_and_preserved(base_jpeg: bytes) -> None:
    src = base_jpeg[:2] + b"\xff\xff" + base_jpeg[2:]
    jf = jpeg.parse(src)
    out = jpeg.splice(jf, b"Exif\x00\x00" + valid_tiff())
    assert jpeg.image_data(jpeg.parse(out)) == jpeg.image_data(jf)


def test_splice_rejects_oversized_payload(base_jpeg: bytes) -> None:
    with pytest.raises(ValueError):
        jpeg.splice(jpeg.parse(base_jpeg), b"Exif\x00\x00" + b"\x00" * 70000)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: b"\x00" + d[1:], "SOI"),
        (lambda d: d[:2] + d[2:].split(b"\xff\xda")[0][:-5], "truncated|ends"),
        (lambda d: d.replace(b"\xff\xc0", b"\xff\xfe", 1), "SOF"),
        (lambda d: d[:2] + b"\xff\xd9", "EOI before SOS"),
        (lambda d: d[:2] + b"\xab" + d[2:], "expected marker"),
        (lambda d: d[:4] + b"\x00\x01" + d[6:], "invalid length"),
    ],
)
def test_unrecoverable_files_raise_jpeg_error(base_jpeg: bytes, mutate, message) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(jpeg.JpegError, match=message):
        jpeg.parse(mutate(base_jpeg))


def test_missing_eoi_is_a_warning(base_jpeg: bytes) -> None:
    jf = jpeg.parse(base_jpeg[:-2])
    assert not jf.has_eoi
    assert any("EOI" in w for w in jf.warnings)


def test_trailing_data_is_part_of_image_data(base_jpeg: bytes) -> None:
    jf = jpeg.parse(base_jpeg + b"TRAILER")
    assert jpeg.image_data(jf).endswith(b"\xff\xd9TRAILER")
