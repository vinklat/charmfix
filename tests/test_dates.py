from __future__ import annotations

from datetime import datetime

import pytest

from charmfix import dates


@pytest.mark.parametrize(
    "raw",
    [
        b"2026:03:03 12:16:29",
        b"2026:03:03:12:16:29",  # the Charmera six-part form
        b"2026:03:03T12:16:29",
        b"2026-03-03T12:16:29",
        b"2026:03:03-12:16:29",
        b"2026:03:03 12:16:29\x00",
        " 2026:03:03 12:16:29 ",
    ],
)
def test_normalize_accepts_variants(raw: bytes | str) -> None:
    assert dates.normalize(raw) == datetime(2026, 3, 3, 12, 16, 29)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        b"",
        b"    :  :     :  :  ",
        b"0000:00:00 00:00:00",
        b"2026:02:30 12:00:00",
        b"2026:13:01 12:00:00",
        b"2026:01:01 25:00:00",
        b"2026:03:03",
        b"garbage",
        b"\xff\xfe\xfd",
    ],
)
def test_normalize_rejects_invalid(raw: bytes | None) -> None:
    assert dates.normalize(raw) is None


def test_is_normal_form() -> None:
    assert dates.is_normal_form(b"2026:03:03 12:16:29\x00")
    assert not dates.is_normal_form(b"2026:03:03:12:16:29")
    assert not dates.is_normal_form(b"2026:02:30 12:16:29")
    assert not dates.is_normal_form(None)


def test_format_exif() -> None:
    assert dates.format_exif(datetime(2026, 3, 3, 9, 5, 1)) == "2026:03:03 09:05:01"
    assert dates.format_exif(datetime(999, 6, 15, 12)) == "0999:06:15 12:00:00"  # glibc %Y


def test_sanity_warning() -> None:
    now = datetime(2026, 11, 6, 12, 0, 0)
    assert dates.sanity_warning(datetime(2026, 3, 3), now) is None
    assert "before 2000" in (dates.sanity_warning(datetime(1999, 12, 31), now) or "")
    assert "future" in (dates.sanity_warning(datetime(2027, 1, 1), now) or "")
