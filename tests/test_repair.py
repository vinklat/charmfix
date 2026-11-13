"""Repair rules, each checked through build -> splice -> verify -> re-read."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import pytest
from helpers import (
    NORMAL_DATE,
    SIX_PART_DATE,
    E,
    ascii_,
    broken_charmera_tiff,
    build_tiff,
    charmera_layout,
    long_,
    rational,
    set_sof_height,
    short,
    standard_exif_entries,
    standard_ifd0_entries,
    undefined,
    valid_tiff,
    with_exif,
)

from charmfix import jpeg
from charmfix.exif_read import ExifData, read_exif
from charmfix.exif_write import build_exif
from charmfix.repair import (
    DEFAULT_MAKE,
    DEFAULT_MODEL,
    MAX_CAMERA_TEXT,
    RepairOptions,
    RepairPlan,
    check_camera_text,
    plan_repair,
)
from charmfix.verify import verify_output

MTIME = datetime(2026, 5, 1, 10, 20, 30).timestamp()
NOW = datetime(2026, 11, 6, 12, 0, 0)


@dataclass
class Outcome:
    plan: RepairPlan
    out: bytes
    exif: ExifData

    def tag(self, ifd: str, tag: int) -> object:
        rv = self.exif.get(ifd, tag)
        if rv is None:
            return None
        if isinstance(rv.value, bytes):
            return rv.value.rstrip(b"\x00").decode("latin-1") if rv.type == 2 else rv.value
        return rv.value[0] if len(rv.value) == 1 else rv.value


def repair(
    data: bytes,
    *,
    assume_charmera: bool = True,
    make: str = DEFAULT_MAKE,
    model: str = DEFAULT_MODEL,
) -> Outcome:
    opts = RepairOptions(assume_charmera=assume_charmera, now=NOW, make=make, model=model)
    jf = jpeg.parse(data)
    tiff = jf.exif_tiff()
    exif = read_exif(tiff) if tiff is not None else None
    plan = plan_repair(jf, exif, MTIME, opts)
    if not plan.needs_rebuild:
        out = data
    else:
        out = jpeg.splice(jf, build_exif(plan))
        verify_output(jf, out, plan)
    out_tiff = jpeg.parse(out).exif_tiff()
    assert out_tiff is not None
    result = Outcome(plan, out, read_exif(out_tiff))
    # Every output is stable: running again finds nothing to do.
    again = jpeg.parse(out)
    rerun = plan_repair(again, read_exif(out_tiff), 0, opts)
    assert rerun.status_hint == "already_valid", rerun.defects
    assert set(rerun.warnings) <= set(plan.warnings), "a re-run adds no warning"
    return result


# 1
def test_no_exif_charmera_file(base_jpeg: bytes) -> None:
    r = repair(charmera_layout(base_jpeg))
    assert r.plan.status_hint == "repaired"
    assert r.plan.timestamp_source == "file_mtime"
    assert r.plan.identity == "charmera(assumed)"
    for ifd, tag in (("Exif", 0x9003), ("Exif", 0x9004), ("0th", 0x0132)):
        assert r.tag(ifd, tag) == "2026:05:01 10:20:30"
    assert (r.tag("Exif", 0xA002), r.tag("Exif", 0xA003)) == (64, 48)
    assert (r.tag("0th", 0x010F), r.tag("0th", 0x0110)) == ("Kodak", "Charmera")
    assert r.tag("0th", 0x0112) == 1
    assert r.tag("Exif", 0x9000) == b"0232"
    assert r.tag("0th", 0x0100) is None  # IFD0 ImageWidth is not written for JPEG


# 2, 3, 4
def test_known_charmera_defects(base_jpeg: bytes) -> None:
    r = repair(with_exif(base_jpeg, broken_charmera_tiff(640, 480)))
    assert r.plan.status_hint == "repaired"
    assert r.plan.timestamp_source == "DateTimeOriginal"
    assert r.plan.raw_date == SIX_PART_DATE
    assert r.tag("Exif", 0x9003) == NORMAL_DATE
    assert r.tag("0th", 0x0132) == NORMAL_DATE
    assert (r.tag("Exif", 0xA002), r.tag("Exif", 0xA003)) == (64, 48)
    assert r.plan.makernote_status == "discarded(invalid)"
    assert r.tag("Exif", 0x927C) is None
    assert r.tag("Exif", 0x829A) == (1, 60)  # valid ExposureTime survives
    assert r.plan.identity == "charmera(exif)"


# 5
def test_valid_makernote_dropped_when_rebuilding(base_jpeg: bytes) -> None:
    tiff = build_tiff(standard_ifd0_entries(SIX_PART_DATE), [E(0x927C, 7, 16, b"M" * 16)])
    r = repair(with_exif(base_jpeg, tiff))
    assert r.plan.makernote_status == "discarded(not relocatable)"
    assert r.tag("Exif", 0x927C) is None


# 6
def test_exif_without_valid_date_is_partial(base_jpeg: bytes) -> None:
    tiff = build_tiff(
        [*standard_ifd0_entries("0000:00:00 00:00:00")], [ascii_(0x9003, "    :  :  ")]
    )
    r = repair(with_exif(base_jpeg, tiff))
    assert r.plan.status_hint == "partial"
    assert r.plan.timestamp_source == "file_mtime"
    assert r.tag("Exif", 0x9003) == "2026:05:01 10:20:30"


# 7
def test_unreadable_exif_rebuilt_as_partial(base_jpeg: bytes) -> None:
    r = repair(with_exif(base_jpeg, b"this is not a TIFF header at all"))
    assert r.plan.status_hint == "partial"
    assert any("unreadable" in p for p in r.plan.partial_reasons)
    assert r.tag("Exif", 0xA002) == 64


# 8
def test_already_valid_is_copied_unchanged(base_jpeg: bytes) -> None:
    src = with_exif(base_jpeg, valid_tiff())
    r = repair(src)
    assert r.plan.status_hint == "already_valid"
    assert r.out == src
    assert r.plan.changes == []


# 9
@pytest.mark.parametrize("orientation", [None, 0, 9])
def test_orientation_missing_or_invalid_becomes_1(
    base_jpeg: bytes, orientation: int | None
) -> None:
    ifd0 = [e for e in standard_ifd0_entries() if e.tag != 0x0112]
    if orientation is not None:
        ifd0.append(short(0x0112, orientation))
    exif = [*standard_exif_entries(), long_(0xA002, 64), long_(0xA003, 48)]
    r = repair(with_exif(base_jpeg, build_tiff(ifd0, exif)))
    assert r.plan.status_hint == "repaired"
    assert r.tag("0th", 0x0112) == 1


def test_valid_orientation_kept(base_jpeg: bytes) -> None:
    ifd0 = [e for e in standard_ifd0_entries(SIX_PART_DATE) if e.tag != 0x0112]
    r = repair(with_exif(base_jpeg, build_tiff([*ifd0, short(0x0112, 6)], [])))
    assert r.tag("0th", 0x0112) == 6


# 10
def test_charmera_in_exif_is_normalized(base_jpeg: bytes) -> None:
    tiff = valid_tiff(make="KODAK ", model="charmera v1")
    r = repair(with_exif(base_jpeg, tiff))
    assert r.plan.identity == "charmera(exif)"
    assert (r.tag("0th", 0x010F), r.tag("0th", 0x0110)) == ("Kodak", "Charmera")


@pytest.mark.parametrize("assumed", [False, True], ids=["charmera-exif", "charmera-assumed"])
def test_make_equal_to_target_is_kept_as_stored(base_jpeg: bytes, assumed: bool) -> None:
    """Padding is not a difference, even when another defect (the size) forces a rebuild."""
    target = ("Acme", "Snap 1") if assumed else (DEFAULT_MAKE, DEFAULT_MODEL)
    src = with_exif(base_jpeg, valid_tiff(640, make=f"{target[0]} ", model=target[1]))
    r = repair(charmera_layout(src) if assumed else src, make=target[0], model=target[1])
    assert r.plan.status_hint == "repaired"
    assert r.tag("0th", 0x010F) == f"{target[0]} "
    assert not [c for c in r.plan.changes if c.field == "0th.Make"]
    assert not r.plan.warnings


# 11
def test_gpencoder_with_other_make_is_kept_with_warning(base_jpeg: bytes) -> None:
    src = charmera_layout(with_exif(base_jpeg, valid_tiff(make="Acme", model="Cam")))
    r = repair(src)
    assert r.plan.identity == "charmera(assumed)"
    assert r.tag("0th", 0x010F) == "Acme"
    assert any("Acme" in w for w in r.plan.warnings)


def test_no_assume_charmera(base_jpeg: bytes) -> None:
    r = repair(charmera_layout(base_jpeg), assume_charmera=False)
    assert r.plan.identity == "generalplus-unconfirmed"
    assert r.tag("0th", 0x010F) is None


def test_unknown_camera(base_jpeg: bytes) -> None:
    r = repair(base_jpeg)
    assert r.plan.identity == "unknown"
    assert r.tag("0th", 0x010F) is None


def test_custom_target_on_assumed_file(base_jpeg: bytes) -> None:
    r = repair(charmera_layout(base_jpeg), make="Acme", model="Snap 1")
    assert r.plan.identity == "charmera(assumed)"
    assert (r.tag("0th", 0x010F), r.tag("0th", 0x0110)) == ("Acme", "Snap 1")
    assert not r.plan.warnings


def test_custom_target_rewrites_charmera_exif(base_jpeg: bytes) -> None:
    r = repair(with_exif(base_jpeg, valid_tiff()), make="Acme", model="Snap 1")
    assert r.plan.identity == "charmera(exif)"
    assert r.plan.status_hint == "repaired"
    assert (r.tag("0th", 0x010F), r.tag("0th", 0x0110)) == ("Acme", "Snap 1")


@pytest.mark.parametrize(
    ("make", "status", "makernote"),
    [
        (DEFAULT_MAKE, "already_valid", "kept"),
        ("Acme", "repaired", "discarded(not relocatable)"),
    ],
    ids=["default-target", "other-target"],
)
def test_other_target_rebuilds_a_valid_file(
    base_jpeg: bytes, make: str, status: str, makernote: str
) -> None:
    exif = [*standard_exif_entries(), long_(0xA002, 64), long_(0xA003, 48)]
    tiff = build_tiff(standard_ifd0_entries(), [*exif, E(0x927C, 7, 16, b"M" * 16)])
    r = repair(with_exif(base_jpeg, tiff), make=make)
    assert (r.plan.status_hint, r.plan.makernote_status) == (status, makernote)


@pytest.mark.parametrize(
    ("make", "model"),
    [("Acme", DEFAULT_MODEL), (DEFAULT_MAKE, "Snap 1")],
    ids=["make-only", "model-only"],
)
def test_custom_target_parts_are_independent(base_jpeg: bytes, make: str, model: str) -> None:
    r = repair(charmera_layout(base_jpeg), make=make, model=model)
    assert (r.tag("0th", 0x010F), r.tag("0th", 0x0110)) == (make, model)


@pytest.mark.parametrize(
    ("make", "model"),
    [("Acme", "Other"), ("Other", "Snap 1"), ("Other", "Cam")],
    ids=["make-matches", "model-matches", "neither-matches"],
)
def test_assumed_file_not_at_target_is_kept_with_warning(
    base_jpeg: bytes, make: str, model: str
) -> None:
    src = charmera_layout(with_exif(base_jpeg, valid_tiff(make=make, model=model)))
    r = repair(src, make="Acme", model="Snap 1")
    assert r.plan.identity == "charmera(assumed)"
    assert (r.tag("0th", 0x010F), r.tag("0th", 0x0110)) == (make, model)
    assert any(f"{make!r}/{model!r}; kept" in w for w in r.plan.warnings)


def test_custom_target_with_no_assume_charmera(base_jpeg: bytes) -> None:
    r = repair(charmera_layout(base_jpeg), assume_charmera=False, make="Acme")
    assert r.plan.identity == "generalplus-unconfirmed"
    assert r.tag("0th", 0x010F) is None


def as_is(data: bytes) -> bytes:
    return data


@pytest.mark.parametrize(
    ("layout", "model", "identity", "changes", "warned"),
    [
        pytest.param(as_is, "Snap 1", "unknown", [], False, id="other-target"),
        pytest.param(charmera_layout, "Snap 1", "charmera(assumed)", [], True, id="gpencoder"),
        pytest.param(
            as_is,
            DEFAULT_MODEL,
            "charmera(exif)",
            ["0th.Make: Acme -> Kodak"],
            False,
            id="charmera",
        ),
        pytest.param(
            charmera_layout,
            DEFAULT_MODEL,
            "charmera(exif)",
            ["0th.Make: Acme -> Kodak"],
            False,
            id="charmera-gpencoder",
        ),
    ],
)
def test_custom_target_output_on_a_later_default_run(
    base_jpeg: bytes,
    layout: Callable[[bytes], bytes],
    model: str,
    identity: str,
    changes: list[str],
    warned: bool,
) -> None:
    r = repair(layout(with_exif(base_jpeg, valid_tiff())), make="Acme", model=model)
    jf = jpeg.parse(r.out)
    later = plan_repair(jf, read_exif(jf.exif_tiff() or b""), MTIME, RepairOptions(now=NOW))
    assert later.identity == identity
    assert [str(c) for c in later.changes] == changes  # kept, or set back to the default
    assert any("kept" in w for w in later.warnings) == warned


def test_assumed_file_already_at_target_is_valid_without_warning(base_jpeg: bytes) -> None:
    src = charmera_layout(with_exif(base_jpeg, valid_tiff(make="Acme", model="Snap 1")))
    r = repair(src, make="Acme", model="Snap 1")
    assert r.plan.identity == "charmera(assumed)"
    assert r.plan.status_hint == "already_valid"
    assert not r.plan.warnings


@pytest.mark.parametrize("value", ["A", "Snap 1", "~", "A" * MAX_CAMERA_TEXT])
def test_camera_text_accepted(value: str) -> None:
    assert check_camera_text(value) == value
    assert RepairOptions(make=value, model=value).make == value


@pytest.mark.parametrize(
    ("value", "message"),
    [
        pytest.param("", "1-64 characters", id="empty"),
        pytest.param("A" * (MAX_CAMERA_TEXT + 1), "1-64 characters", id="too-long"),
        pytest.param(" Acme", "printable ASCII", id="leading-space"),
        pytest.param("Acme ", "printable ASCII", id="trailing-space"),
        pytest.param("Ačme", "printable ASCII", id="non-ascii"),
        pytest.param("Ac\tme", "printable ASCII", id="control"),
        pytest.param("Ac\x7fme", "printable ASCII", id="del"),
    ],
)
def test_camera_text_rejected(value: str, message: str) -> None:
    with pytest.raises(ValueError, match=f"^must be .*{message}"):
        check_camera_text(value)
    with pytest.raises(ValueError, match=f"^make must be .*{message}"):
        RepairOptions(make=value)
    with pytest.raises(ValueError, match=f"^model must be .*{message}"):
        RepairOptions(model=value)


def test_custom_target_leaves_unknown_camera(base_jpeg: bytes) -> None:
    r = repair(base_jpeg, make="Acme", model="Snap 1")
    assert r.plan.identity == "unknown"
    assert r.tag("0th", 0x010F) is None


# 12
def test_icc_profile_sets_colorspace_uncalibrated_and_is_preserved(icc_jpeg: bytes) -> None:
    jf = jpeg.parse(icc_jpeg)
    assert jf.has_icc
    r = repair(icc_jpeg)
    assert r.tag("Exif", 0xA001) == 0xFFFF
    assert jpeg.parse(r.out).preserved_segments() == jf.preserved_segments()


def test_thumbnail_kept_when_complete(base_jpeg: bytes) -> None:
    tiff = build_tiff(
        standard_ifd0_entries(SIX_PART_DATE), [], ifd1=[short(0x0103, 6)], thumbnail=base_jpeg
    )
    r = repair(with_exif(base_jpeg, tiff))
    assert r.exif.thumbnail == base_jpeg


def test_broken_thumbnail_dropped(base_jpeg: bytes) -> None:
    thumb = base_jpeg[:-50]
    tiff = build_tiff(
        standard_ifd0_entries(SIX_PART_DATE), [], ifd1=[short(0x0103, 6)], thumbnail=thumb
    )
    r = repair(with_exif(base_jpeg, tiff))
    assert r.exif.thumbnail is None
    assert any("thumbnail" in str(c) for c in r.plan.changes)


def test_dnl_height_is_partial_without_size_tags(base_jpeg: bytes) -> None:
    src = with_exif(set_sof_height(base_jpeg, 0), broken_charmera_tiff())
    jf = jpeg.parse(src)
    plan = plan_repair(jf, read_exif(jf.exif_tiff() or b""), MTIME, RepairOptions(now=NOW))
    assert plan.status_hint == "partial"
    out = jpeg.splice(jf, build_exif(plan))
    verify_output(jf, out, plan)
    out_exif = read_exif(jpeg.parse(out).exif_tiff() or b"")
    assert out_exif.get("Exif", 0xA002) is None


def test_semantic_checks_drop_bad_values(base_jpeg: bytes) -> None:
    exif = [
        *standard_exif_entries(SIX_PART_DATE),
        rational(0x829A, (0, 1)),  # ExposureTime 0
        rational(0x829D, (28, 10)),  # FNumber fine
        short(0x9209, 0x1234),  # Flash invalid
        short(0x9207, 5),  # MeteringMode fine
        undefined(0x9286, b"BOGUS!!!text"),  # UserComment bad prefix
        ascii_(0x9290, "12a"),  # SubSecTime not digits
        ascii_(0x0131 + 0, "x"),  # Software is an IFD0 tag: unknown in the Exif IFD
    ]
    r = repair(with_exif(base_jpeg, build_tiff(standard_ifd0_entries(SIX_PART_DATE), exif)))
    assert r.tag("Exif", 0x829A) is None
    assert r.tag("Exif", 0x829D) == (28, 10)
    assert r.tag("Exif", 0x9209) is None
    assert r.tag("Exif", 0x9207) == 5
    assert r.tag("Exif", 0x9286) is None
    assert r.tag("Exif", 0x9290) is None
    assert r.plan.private_text == {"UserComment": "dropped"}
    assert all("BOGUS" not in str(c) for c in r.plan.changes)


def test_wrong_type_dropped_and_mandatory_restored(base_jpeg: bytes) -> None:
    exif = [ascii_(0x9003, SIX_PART_DATE), ascii_(0x9000, "0232"), short(0xA001, 7)]
    r = repair(with_exif(base_jpeg, build_tiff([], exif)))
    assert r.tag("Exif", 0x9000) == b"0232"
    assert r.tag("Exif", 0xA001) == 1


def test_offset_time_dropped_when_dates_rewritten(base_jpeg: bytes) -> None:
    exif = [*standard_exif_entries(SIX_PART_DATE), ascii_(0x9011, "+01:00")]
    r = repair(with_exif(base_jpeg, build_tiff(standard_ifd0_entries(SIX_PART_DATE), exif)))
    assert r.tag("Exif", 0x9011) is None


def test_offset_time_kept_when_dates_valid(base_jpeg: bytes) -> None:
    exif = [*standard_exif_entries(), ascii_(0x9011, "+01:00")]  # sizes missing -> rebuild
    r = repair(with_exif(base_jpeg, build_tiff(standard_ifd0_entries(), exif)))
    assert r.plan.status_hint == "repaired"
    assert r.tag("Exif", 0x9011) == "+01:00"


def test_ifd0_size_tags_are_dropped(base_jpeg: bytes) -> None:
    exif = [*standard_exif_entries(), long_(0xA002, 64), long_(0xA003, 48)]
    ifd0 = [*standard_ifd0_entries(), long_(0x0100, 640), long_(0x0101, 480)]
    r = repair(with_exif(base_jpeg, build_tiff(ifd0, exif)))
    assert r.plan.status_hint == "repaired"
    assert r.tag("0th", 0x0100) is None
    assert r.plan.old_sizes["0th.ImageWidth"] == 640


def test_duplicate_tags_first_wins_and_rebuilds(base_jpeg: bytes) -> None:
    exif = [*standard_exif_entries(), long_(0xA002, 64), long_(0xA003, 48)]
    ifd0 = [*standard_ifd0_entries(), ascii_(0x0131, "first"), ascii_(0x0131, "second")]
    r = repair(with_exif(base_jpeg, build_tiff(ifd0, exif)))
    assert r.plan.status_hint == "repaired"
    assert r.tag("0th", 0x0131) == "first"


def test_gps_kept(base_jpeg: bytes) -> None:
    gps = [
        E(0x0000, 1, 4, bytes([2, 3, 0, 0])),
        ascii_(0x0001, "N"),
        rational(0x0002, (50, 1), (5, 1), (0, 1)),
    ]
    r = repair(with_exif(base_jpeg, build_tiff(standard_ifd0_entries(SIX_PART_DATE), [], gps)))
    assert r.tag("GPS", 0x0001) == "N"
    assert r.tag("GPS", 0x0002) == ((50, 1), (5, 1), (0, 1))


def test_future_date_warns_but_is_kept(base_jpeg: bytes) -> None:
    tiff = build_tiff(
        standard_ifd0_entries("2031:01:01:00:00:00"), [ascii_(0x9003, "2031:01:01:00:00:00")]
    )
    r = repair(with_exif(base_jpeg, tiff))
    assert r.tag("Exif", 0x9003) == "2031:01:01 00:00:00"
    assert any("future" in w for w in r.plan.warnings)
