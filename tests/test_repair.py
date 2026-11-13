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
    MAX_TARGET_TEXT,
    RepairOptions,
    RepairPlan,
    check_target_text,
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
    opts = RepairOptions(assume_charmera=assume_charmera, make=make, model=model)
    jf = jpeg.parse(data)
    tiff = jf.exif_tiff()
    exif = read_exif(tiff) if tiff is not None else None
    plan = plan_repair(jf, exif, MTIME, NOW, opts)
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
    rerun = plan_repair(again, read_exif(out_tiff), 0, NOW, opts)
    assert rerun.status_hint == "already_valid", rerun.defects
    assert set(rerun.warnings) <= set(plan.warnings), "a re-run adds no warning"
    return result


def as_is(data: bytes) -> bytes:
    """A layout that leaves the JPEG as built (no GPEncoder comment)."""
    return data


def kodak_exif(data: bytes) -> bytes:
    """A layout with valid Kodak/Charmera EXIF and no GPEncoder comment."""
    return with_exif(data, valid_tiff())


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


KODAK = (DEFAULT_MAKE, DEFAULT_MODEL)
ACME = ("Acme", "Snap 1")


@pytest.mark.parametrize(
    ("layout", "target", "stored", "written", "changes", "identity"),
    [
        pytest.param(
            as_is,
            KODAK,
            ("Kodak ", " Charmera"),
            ("Kodak ", " Charmera"),
            [],
            "charmera(exif)",
            id="charmera-exif",
        ),
        pytest.param(
            charmera_layout,
            ACME,
            ("Acme ", " Snap 1"),
            ("Acme ", " Snap 1"),
            [],
            "charmera(assumed)",
            id="charmera-assumed",
        ),
        pytest.param(
            as_is,
            KODAK,
            ("Kodak ", "charmera v1"),
            ("Kodak ", "Charmera"),
            ["0th.Model: charmera v1 -> Charmera"],
            "charmera(exif)",
            id="other-part-differs",
        ),
    ],
)
def test_value_equal_to_target_is_kept_as_stored(
    base_jpeg: bytes,
    layout: Callable[[bytes], bytes],
    target: tuple[str, str],
    stored: tuple[str, str],
    written: tuple[str, str],
    changes: list[str],
    identity: str,
) -> None:
    """Padding is not a difference, per field, even when another defect forces a rebuild."""
    src = layout(with_exif(base_jpeg, valid_tiff(640, make=stored[0], model=stored[1])))
    r = repair(src, make=target[0], model=target[1])
    assert (r.plan.identity, r.plan.status_hint) == (identity, "repaired")
    assert (r.tag("0th", 0x010F), r.tag("0th", 0x0110)) == written
    assert [str(c) for c in r.plan.changes if c.field in ("0th.Make", "0th.Model")] == changes
    assert not r.plan.warnings


@pytest.mark.parametrize(
    ("layout", "target"),
    [pytest.param(as_is, KODAK, id="exif"), pytest.param(charmera_layout, ACME, id="assumed")],
)
def test_trailing_nuls_are_not_a_difference(
    base_jpeg: bytes, layout: Callable[[bytes], bytes], target: tuple[str, str]
) -> None:
    """NUL padding is normalised on output, so only the absence of a change shows it was kept."""
    stored = (f"{target[0]}\x00\x00", f"{target[1]}\x00")  # on top of the terminating NUL
    src = layout(with_exif(base_jpeg, valid_tiff(640, make=stored[0], model=stored[1])))
    r = repair(src, make=target[0], model=target[1])
    assert (r.tag("0th", 0x010F), r.tag("0th", 0x0110)) == target
    assert not [c for c in r.plan.changes if c.field in ("0th.Make", "0th.Model")]
    assert not [d for d in r.plan.defects if "Make" in d or "Model" in d]
    assert not r.plan.warnings


# 11
def test_gpencoder_with_other_make_is_kept_with_warning(base_jpeg: bytes) -> None:
    src = charmera_layout(with_exif(base_jpeg, valid_tiff(make="Acme", model="Cam")))
    r = repair(src)
    assert r.plan.identity == "charmera(assumed)"
    assert r.tag("0th", 0x010F) == "Acme"
    assert any("Acme" in w for w in r.plan.warnings)


NOT_WRITTEN = (None, None)


@pytest.mark.parametrize(
    ("layout", "assume", "target", "written", "identity"),
    [
        pytest.param(charmera_layout, True, ACME, ACME, "charmera(assumed)", id="assumed"),
        pytest.param(
            charmera_layout,
            True,
            ("Acme", DEFAULT_MODEL),
            ("Acme", DEFAULT_MODEL),
            "charmera(assumed)",
            id="assumed-make-only",
        ),
        pytest.param(
            charmera_layout,
            True,
            (DEFAULT_MAKE, "Snap 1"),
            (DEFAULT_MAKE, "Snap 1"),
            "charmera(assumed)",
            id="assumed-model-only",
        ),
        pytest.param(kodak_exif, True, ACME, ACME, "charmera(exif)", id="exif"),
        pytest.param(
            charmera_layout, False, KODAK, NOT_WRITTEN, "generalplus-unconfirmed", id="unconfirmed"
        ),
        pytest.param(
            charmera_layout,
            False,
            ACME,
            NOT_WRITTEN,
            "generalplus-unconfirmed",
            id="unconfirmed-custom",
        ),
        pytest.param(as_is, True, KODAK, NOT_WRITTEN, "unknown", id="unknown"),
        pytest.param(as_is, True, ACME, NOT_WRITTEN, "unknown", id="unknown-custom"),
    ],
)
def test_target_is_written_only_for_charmera_identities(
    base_jpeg: bytes,
    layout: Callable[[bytes], bytes],
    assume: bool,
    target: tuple[str, str],
    written: tuple[str | None, str | None],
    identity: str,
) -> None:
    """A charmera identity gets the target Make/Model; the others are left without one."""
    r = repair(layout(base_jpeg), assume_charmera=assume, make=target[0], model=target[1])
    assert r.plan.identity == identity
    assert (r.tag("0th", 0x010F), r.tag("0th", 0x0110)) == written
    assert not r.plan.warnings


@pytest.mark.parametrize(
    ("make", "status", "makernote", "unknown_kept"),
    [
        (DEFAULT_MAKE, "already_valid", "kept", True),
        ("Acme", "repaired", "discarded(not relocatable)", False),
    ],
    ids=["default-target", "other-target"],
)
def test_valid_file_is_rebuilt_only_for_another_target(
    base_jpeg: bytes, make: str, status: str, makernote: str, unknown_kept: bool
) -> None:
    exif = [*standard_exif_entries(), long_(0xA002, 64), long_(0xA003, 48)]
    unknown = ascii_(0xC000, "x")  # not in piexif's table: rule 5
    tiff = build_tiff(standard_ifd0_entries(), [*exif, E(0x927C, 7, 16, b"M" * 16), unknown])
    r = repair(with_exif(base_jpeg, tiff), make=make)
    assert (r.plan.status_hint, r.plan.makernote_status) == (status, makernote)
    assert (r.tag("Exif", 0xC000) is not None) == unknown_kept


@pytest.mark.parametrize(
    ("make", "model"),
    [("Acme", "Other"), ("Other", "Snap 1"), ("Other", "Cam"), ("Acme", ""), ("", "Snap 1")],
    ids=["make-matches", "model-matches", "neither-matches", "model-empty", "make-empty"],
)
def test_assumed_file_not_at_target_is_kept_with_warning(
    base_jpeg: bytes, make: str, model: str
) -> None:
    src = charmera_layout(with_exif(base_jpeg, valid_tiff(make=make, model=model)))
    r = repair(src, make="Acme", model="Snap 1")
    assert r.plan.identity == "charmera(assumed)"
    assert (r.tag("0th", 0x010F), r.tag("0th", 0x0110)) == (make, model)
    assert any(f"{make!r}/{model!r}; kept" in w for w in r.plan.warnings)


@pytest.mark.parametrize(
    ("layout", "model", "identity", "status", "changes", "warned"),
    [
        pytest.param(as_is, "Snap 1", "unknown", "already_valid", [], False, id="other-target"),
        pytest.param(
            charmera_layout,
            "Snap 1",
            "charmera(assumed)",
            "already_valid",
            [],
            True,
            id="gpencoder",
        ),
        pytest.param(
            as_is,
            DEFAULT_MODEL,
            "charmera(exif)",
            "repaired",
            ["0th.Make: Acme -> Kodak"],
            False,
            id="charmera",
        ),
        pytest.param(
            charmera_layout,
            DEFAULT_MODEL,
            "charmera(exif)",
            "repaired",
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
    status: str,
    changes: list[str],
    warned: bool,
) -> None:
    """REPAIRS.md Later run: a custom-target output, repaired again with the default target."""
    r = repair(layout(with_exif(base_jpeg, valid_tiff())), make="Acme", model=model)
    later = repair(r.out).plan
    assert (later.identity, later.status_hint) == (identity, status)
    assert [str(c) for c in later.changes] == changes  # kept, or set back to the default
    assert any("kept" in w for w in later.warnings) == warned


def test_assumed_file_already_at_target_is_valid_without_warning(base_jpeg: bytes) -> None:
    src = charmera_layout(with_exif(base_jpeg, valid_tiff(make="Acme", model="Snap 1")))
    r = repair(src, make="Acme", model="Snap 1")
    assert r.plan.identity == "charmera(assumed)"
    assert r.plan.status_hint == "already_valid"
    assert not r.plan.warnings


@pytest.mark.parametrize("value", ["A", "Snap 1", "~", "A" * MAX_TARGET_TEXT])
def test_target_text_accepted(value: str) -> None:
    assert check_target_text(value) == value
    opts = RepairOptions(make=value, model=value)
    assert (opts.make, opts.model) == (value, value)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        pytest.param("", "1-64 characters", id="empty"),
        pytest.param("A" * (MAX_TARGET_TEXT + 1), "1-64 characters", id="too-long"),
        pytest.param(" Acme", "printable ASCII", id="leading-space"),
        pytest.param("Acme ", "printable ASCII", id="trailing-space"),
        pytest.param("Ačme", "printable ASCII", id="non-ascii"),
        pytest.param("Ac\tme", "printable ASCII", id="control"),
        pytest.param("Ac\x7fme", "printable ASCII", id="del"),
    ],
)
def test_target_text_rejected(value: str, message: str) -> None:
    with pytest.raises(ValueError, match=f"^must be .*{message}"):
        check_target_text(value)
    with pytest.raises(ValueError, match=f"^make must be .*{message}"):
        RepairOptions(make=value)
    with pytest.raises(ValueError, match=f"^model must be .*{message}"):
        RepairOptions(model=value)


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
    plan = plan_repair(jf, read_exif(jf.exif_tiff() or b""), MTIME, NOW)
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
