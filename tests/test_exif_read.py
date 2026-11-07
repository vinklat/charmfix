from __future__ import annotations

import struct

import pytest
from helpers import (
    E,
    ascii_,
    broken_charmera_tiff,
    build_tiff,
    long_,
    short,
    standard_ifd0_entries,
    valid_tiff,
)

from charmfix.exif_read import read_exif


def test_reads_valid_block() -> None:
    ex = read_exif(valid_tiff())
    assert ex.readable and not ex.warnings
    assert ex.get("0th", 0x010F).value == b"Kodak\x00"  # type: ignore[union-attr]
    assert ex.get("Exif", 0xA002).value == (64,)  # type: ignore[union-attr]
    assert ex.makernote_status == "absent"


def test_little_endian() -> None:
    tiff = b"II*\x00" + struct.pack("<L", 8) + struct.pack("<H", 1)
    tiff += struct.pack("<HHL", 0x0112, 3, 1) + struct.pack("<HH", 6, 0) + struct.pack("<L", 0)
    ex = read_exif(tiff)
    assert ex.byte_order == "II"
    assert ex.get("0th", 0x0112).value == (6,)  # type: ignore[union-attr]


def test_out_of_bounds_makernote_is_invalid_not_fatal() -> None:
    ex = read_exif(broken_charmera_tiff())
    assert ex.readable
    assert ex.makernote_status == "invalid"
    assert any("0x927C" in w for w in ex.warnings)
    assert ex.get("Exif", 0x9003).value == b"2026:03:03:12:16:29\x00"  # type: ignore[union-attr]


def test_valid_makernote_is_present() -> None:
    tiff = build_tiff(standard_ifd0_entries(), [E(0x927C, 7, 16, b"M" * 16)])
    assert read_exif(tiff).makernote_status == "present"


@pytest.mark.parametrize(
    "tiff, needle",
    [
        (b"XX*\x00\x00\x00\x00\x08", "invalid TIFF header"),
        (b"MM\x00", "too short"),
        (b"MM\x00*" + struct.pack(">L", 9999), "outside"),
        (build_tiff(standard_ifd0_entries(), ifd0_next=8), "loop"),
        (build_tiff(standard_ifd0_entries(), declared_counts={"0th": 60000}), "implausible"),
        (build_tiff(standard_ifd0_entries(), declared_counts={"0th": 40}), "truncated"),
        (build_tiff([E(0x0112, 99, 1, b"\x00\x01")]), "unknown type"),
        (build_tiff([E(0x010F, 2, 0, b"")]), "count 0"),
        (build_tiff([E(0x010F, 2, 0x7FFFFFFF, b"", offset=8)]), "more than"),
        (build_tiff([ascii_(0x010F, "a"), ascii_(0x010F, "b")]), "duplicate"),
        (build_tiff([E(0x8769, 3, 1, b"\x00\x08")]), "invalid IFD pointer"),
    ],
)
def test_damage_gives_warnings_never_exceptions(tiff: bytes, needle: str) -> None:
    ex = read_exif(tiff)
    assert any(needle in w for w in ex.warnings), ex.warnings


def test_duplicate_tag_first_wins() -> None:
    ex = read_exif(build_tiff([ascii_(0x010F, "first"), ascii_(0x010F, "second")]))
    assert ex.get("0th", 0x010F).value == b"first\x00"  # type: ignore[union-attr]
    assert ex.duplicate_tags == [("0th", 0x010F)]


def test_truncated_block_keeps_what_is_readable() -> None:
    full = valid_tiff()
    ex = read_exif(full[:60])
    assert ex.readable
    assert ex.warnings


def test_exif_pointer_loop_back_to_ifd0() -> None:
    tiff = build_tiff([short(0x0112, 1), long_(0x8769, 8)])
    ex = read_exif(tiff)
    assert any("loop" in w for w in ex.warnings)


def test_thumbnail_extracted_and_bounds_checked(base_jpeg: bytes) -> None:
    tiff = build_tiff(standard_ifd0_entries(), ifd1=[short(0x0103, 6)], thumbnail=base_jpeg)
    assert read_exif(tiff).thumbnail == base_jpeg
    cut = read_exif(tiff[: len(tiff) - 10])
    assert cut.thumbnail is None
    assert any("thumbnail" in w for w in cut.warnings)
