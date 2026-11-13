"""Decide what the repaired EXIF should contain. Pure: no file access."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import piexif

from . import dates
from .exif_read import TAG_MAKERNOTE, ExifData, RawValue
from .jpeg import JpegError, JpegFile
from .jpeg import parse as parse_jpeg

Status = Literal["repaired", "partial", "already_valid"]
Identity = Literal["charmera(exif)", "charmera(assumed)", "generalplus-unconfirmed", "unknown"]

# Tags piexif recomputes itself, or that make no sense in a JPEG's EXIF.
_STRUCTURAL: dict[str, frozenset[int]] = {
    "0th": frozenset({0x8769, 0x8825, 0x0201, 0x0202, 0x0111, 0x0117}),
    "Exif": frozenset({0xA005, TAG_MAKERNOTE}),
    "GPS": frozenset(),
    "1st": frozenset({0x8769, 0x8825, 0x0201, 0x0202, 0x0111, 0x0117}),
}

T_DATETIME = ("0th", 0x0132)
T_DATETIME_ORIGINAL = ("Exif", 0x9003)
T_DATETIME_DIGITIZED = ("Exif", 0x9004)
DATE_TAGS = (T_DATETIME_ORIGINAL, T_DATETIME_DIGITIZED, T_DATETIME)
OFFSET_TIME_TAGS = (("Exif", 0x9010), ("Exif", 0x9011), ("Exif", 0x9012))

DEFAULT_MAKE = "Kodak"
DEFAULT_MODEL = "Charmera"

T_MAKE = ("0th", 0x010F)
T_MODEL = ("0th", 0x0110)
T_ORIENTATION = ("0th", 0x0112)
# The size of a JPEG lives in PixelX/YDimension. IFD0 ImageWidth/ImageLength are
# "not recorded" for JPEG-compressed data per the EXIF spec, so they are dropped.
SIZE_TAGS = {
    "Exif.PixelXDimension": ("Exif", 0xA002),
    "Exif.PixelYDimension": ("Exif", 0xA003),
}
IFD0_SIZE_TAGS = {
    "0th.ImageWidth": ("0th", 0x0100),
    "0th.ImageLength": ("0th", 0x0101),
}
# Free-text tags whose content never goes into logs or reports.
PRIVATE_TEXT = {
    "UserComment": ("Exif", 0x9286),
    "Artist": ("0th", 0x013B),
    "Copyright": ("0th", 0x8298),
}

_FIXED_COUNTS: dict[tuple[str, int], int] = {
    ("0th", 0x0100): 1,
    ("0th", 0x0101): 1,
    ("0th", 0x0112): 1,
    ("0th", 0x011A): 1,
    ("0th", 0x011B): 1,
    ("0th", 0x0128): 1,
    ("0th", 0x0213): 1,
    ("Exif", 0x9000): 4,
    ("Exif", 0xA000): 4,
    ("Exif", 0x9101): 4,
    ("Exif", 0xA001): 1,
    ("Exif", 0xA002): 1,
    ("Exif", 0xA003): 1,
    ("Exif", 0x829A): 1,
    ("Exif", 0x829D): 1,
    ("Exif", 0x9209): 1,
    ("Exif", 0x9207): 1,
    ("Exif", 0xA403): 1,
    ("Exif", 0x8822): 1,
    ("GPS", 0x0000): 4,
}

_FLASH_VALUES = frozenset(
    {
        0x00,
        0x01,
        0x05,
        0x07,
        0x08,
        0x09,
        0x0D,
        0x0F,
        0x10,
        0x14,
        0x18,
        0x19,
        0x1D,
        0x1F,
        0x20,
        0x30,
        0x41,
        0x45,
        0x47,
        0x49,
        0x4D,
        0x4F,
        0x50,
        0x58,
        0x59,
        0x5D,
        0x5F,
    }
)
_USER_COMMENT_PREFIXES = (
    b"ASCII\x00\x00\x00",
    b"JIS\x00\x00\x00\x00\x00",
    b"UNICODE\x00",
    b"\x00" * 8,
)


def _positive_rational(v: Any) -> bool:
    return isinstance(v, tuple) and len(v) == 2 and v[0] > 0 and v[1] > 0


def _one_of(values: frozenset[int] | range) -> Callable[[Any], bool]:
    return lambda v: isinstance(v, int) and v in values


_SEMANTIC: dict[tuple[str, int], Callable[[Any], bool]] = {
    ("Exif", 0x829A): _positive_rational,  # ExposureTime
    ("Exif", 0x829D): _positive_rational,  # FNumber
    ("Exif", 0x9209): _one_of(_FLASH_VALUES),  # Flash
    ("Exif", 0x9207): _one_of(frozenset({0, 1, 2, 3, 4, 5, 6, 255})),  # MeteringMode
    ("Exif", 0xA403): _one_of(frozenset({0, 1})),  # WhiteBalance
    ("Exif", 0x8822): _one_of(range(0, 9)),  # ExposureProgram
    ("Exif", 0xA001): _one_of(frozenset({1, 0xFFFF})),  # ColorSpace
    ("0th", 0x0112): _one_of(range(1, 9)),  # Orientation
    ("0th", 0x0128): _one_of(frozenset({1, 2, 3})),  # ResolutionUnit
    ("0th", 0x0213): _one_of(frozenset({1, 2})),  # YCbCrPositioning
    ("0th", 0x011A): _positive_rational,  # XResolution
    ("0th", 0x011B): _positive_rational,  # YResolution
    ("Exif", 0x9286): lambda v: isinstance(v, bytes) and v[:8] in _USER_COMMENT_PREFIXES,
    ("Exif", 0x9290): lambda v: isinstance(v, bytes) and v.strip().isdigit(),  # SubSecTime
    ("Exif", 0x9291): lambda v: isinstance(v, bytes) and v.strip().isdigit(),
    ("Exif", 0x9292): lambda v: isinstance(v, bytes) and v.strip().isdigit(),
}


def _mandatory(has_icc: bool) -> list[tuple[tuple[str, int], Any]]:
    return [
        (("0th", 0x011A), (72, 1)),  # XResolution
        (("0th", 0x011B), (72, 1)),  # YResolution
        (("0th", 0x0128), 2),  # ResolutionUnit = inches
        (("0th", 0x0213), 1),  # YCbCrPositioning = centered
        (("Exif", 0x9000), b"0232"),  # ExifVersion
        (("Exif", 0xA000), b"0100"),  # FlashpixVersion
        (("Exif", 0x9101), b"\x01\x02\x03\x00"),  # ComponentsConfiguration
        (("Exif", 0xA001), 0xFFFF if has_icc else 1),  # ColorSpace
    ]


@dataclass(frozen=True, kw_only=True)
class RepairOptions:
    assume_charmera: bool = True
    now: datetime = field(default_factory=datetime.now)
    make: str = DEFAULT_MAKE
    model: str = DEFAULT_MODEL


@dataclass(frozen=True)
class Change:
    field: str
    old: str | None = None
    new: str | None = None
    note: str | None = None

    def __str__(self) -> str:
        s = f"{self.field}: {self.old!s} -> {self.new!s}" if self.old or self.new else self.field
        return f"{s} ({self.note})" if self.note else s


@dataclass
class RepairPlan:
    ifds: dict[str, dict[int, Any]]
    thumbnail: bytes | None
    capture_time: datetime | None
    raw_date: str | None
    timestamp_source: str | None
    identity: Identity
    makernote_status: str
    exif_present: bool
    old_sizes: dict[str, int | None]
    private_text: dict[str, str]
    raw_size_tags: frozenset[str] = frozenset()
    changes: list[Change] = field(default_factory=list)
    defects: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    partial_reasons: list[str] = field(default_factory=list)

    @property
    def needs_rebuild(self) -> bool:
        return bool(self.defects)

    @property
    def date_written(self) -> str | None:
        return dates.format_exif(self.capture_time) if self.capture_time else None

    @property
    def status_hint(self) -> Status:
        if not self.needs_rebuild:
            return "already_valid"
        return "partial" if self.partial_reasons else "repaired"


def tag_name(ifd: str, tag: int) -> str:
    info = piexif.TAGS.get(ifd, {}).get(tag)
    return f"{ifd}.{info['name'] if info else f'0x{tag:04X}'}"


def _ascii(value: Any) -> str:
    if isinstance(value, bytes):
        return value.rstrip(b"\x00").decode("utf-8", errors="replace").strip()
    return ""


def _convert(ifd: str, tag: int, rv: RawValue, expected: int) -> tuple[Any, str | None]:
    """Turn a stored value into piexif's representation, or explain why not."""
    t, v = rv.type, rv.value
    fixed = _FIXED_COUNTS.get((ifd, tag))
    if fixed is not None and rv.count != fixed:
        return None, f"count {rv.count}, expected {fixed}"

    def single(x: tuple[Any, ...]) -> Any:
        return x[0] if len(x) == 1 else x

    if expected == 2:
        if t != 2 or not isinstance(v, bytes):
            return None, f"type {t}, expected ASCII"
        text = v.rstrip(b"\x00")
        try:
            ok = text.decode("utf-8").isprintable()
        except UnicodeDecodeError:
            ok = False
        return (text, None) if ok else (None, "non-printable text")
    if expected == 7:
        if t in (7, 1):
            return bytes(rv.raw), None
        return None, f"type {t}, expected UNDEFINED"
    if expected == 1:
        if t in (1, 7):
            return tuple(rv.raw), None
        return None, f"type {t}, expected BYTE"
    if expected in (3, 4):
        if t not in (1, 3, 4) or not isinstance(v, tuple):
            return None, f"type {t}, expected {'SHORT' if expected == 3 else 'LONG'}"
        if expected == 3 and any(not isinstance(x, int) or x > 0xFFFF for x in v):
            return None, "value out of SHORT range"
        return single(v), None
    if expected in (6, 8, 9):
        if t not in (6, 8, 9) or not isinstance(v, tuple):
            return None, f"type {t}, expected signed integer"
        return single(v), None
    if expected in (5, 10, 11, 12):
        if t != expected or not isinstance(v, tuple):
            return None, f"type {t}, expected type {expected}"
        return single(v), None
    return None, f"unsupported type {expected}"


def _valid_thumbnail(thumb: bytes | None) -> bool:
    if (
        not thumb
        or not thumb.startswith(b"\xff\xd8")
        or not thumb.rstrip(b"\x00").endswith(b"\xff\xd9")
    ):
        return False
    try:
        return parse_jpeg(thumb).has_eoi
    except JpegError:
        return False


def plan_repair(
    jf: JpegFile,
    exif: ExifData | None,
    src_mtime: float,
    opts: RepairOptions | None = None,
    *,
    exif_segment_count: int | None = None,
) -> RepairPlan:
    opts = opts or RepairOptions()
    src: dict[str, dict[int, RawValue]] = exif.ifds if exif else {}
    new: dict[str, dict[int, Any]] = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}}
    plan = RepairPlan(
        ifds=new,
        thumbnail=None,
        capture_time=None,
        raw_date=None,
        timestamp_source=None,
        identity="unknown",
        makernote_status="absent",
        exif_present=exif is not None,
        old_sizes={
            name: _int_value(src, key) for name, key in {**IFD0_SIZE_TAGS, **SIZE_TAGS}.items()
        },
        private_text={},
        raw_size_tags=frozenset(
            name
            for name, (ifd, tag) in {**IFD0_SIZE_TAGS, **SIZE_TAGS}.items()
            if tag in src.get(ifd, {})
        ),
    )
    plan.warnings.extend(jf.warnings)
    defect, change = plan.defects.append, plan.changes.append

    # Structure of the existing block.
    if exif is None:
        defect("no EXIF block")
    else:
        if not exif.readable:
            defect("EXIF block unreadable")
            plan.partial_reasons.append("unreadable EXIF block rebuilt from nothing")
        for w in exif.warnings:
            defect(w)
    n_exif = exif_segment_count if exif_segment_count is not None else len(jf.exif_segments)
    if n_exif > 1:
        defect(f"{n_exif} Exif APP1 segments")

    # Rule 5: keep known tags with sane types, counts and values.
    special = {*DATE_TAGS, *OFFSET_TIME_TAGS, *SIZE_TAGS.values(), *IFD0_SIZE_TAGS.values()}
    for ifd in ("0th", "Exif", "GPS"):
        for tag, rv in sorted(src.get(ifd, {}).items()):
            if tag in _STRUCTURAL[ifd] or (ifd, tag) in special:
                continue
            expected = piexif.TAGS[ifd].get(tag, {}).get("type")
            name = tag_name(ifd, tag)
            if expected is None:
                change(Change(name, note="dropped: unknown tag"))
                continue
            value, problem = _convert(ifd, tag, rv, expected)
            check = _SEMANTIC.get((ifd, tag))
            if problem is None and check is not None and not check(value):
                problem = "invalid value"
            if problem is not None:
                defect(f"{name}: {problem}")
                change(Change(name, note=f"dropped: {problem}"))
                continue
            new[ifd][tag] = value

    _apply_dates(plan, src, src_mtime, opts)
    _apply_sizes(plan, jf)

    if T_ORIENTATION[1] not in new["0th"]:
        defect("Orientation missing or invalid")
        change(Change("0th.Orientation", None, "1"))
        new["0th"][T_ORIENTATION[1]] = 1

    for (ifd, tag), default in _mandatory(jf.has_icc):
        if tag not in new[ifd]:
            name = tag_name(ifd, tag)
            defect(f"{name} missing")
            change(Change(name, None, _show(default)))
            new[ifd][tag] = default

    _apply_identity(plan, jf, opts)
    _apply_thumbnail(plan, exif, src)

    if exif is not None:
        if exif.makernote_status == "invalid":
            plan.makernote_status = "discarded(invalid)"
        elif exif.makernote_status == "present":
            plan.makernote_status = "discarded(not relocatable)" if plan.needs_rebuild else "kept"
        if plan.makernote_status.startswith("discarded"):
            change(Change("Exif.MakerNote", note=plan.makernote_status))

    for label, (ifd, tag) in PRIVATE_TEXT.items():
        if tag in src.get(ifd, {}):
            kept = tag in new[ifd] or not plan.needs_rebuild
            plan.private_text[label] = "kept" if kept else "dropped"

    if not plan.needs_rebuild:
        plan.changes.clear()
    return plan


def _int_value(src: dict[str, dict[int, RawValue]], key: tuple[str, int]) -> int | None:
    rv = src.get(key[0], {}).get(key[1])
    if rv is None or rv.type not in (1, 3, 4) or not isinstance(rv.value, tuple) or rv.count != 1:
        return None
    v = rv.value[0]
    return v if isinstance(v, int) else None


def _show(value: Any) -> str:
    if isinstance(value, bytes):
        text = value.decode("latin-1")
        return text if text.isprintable() else value.hex(" ")
    if isinstance(value, tuple) and len(value) == 2 and all(isinstance(x, int) for x in value):
        return f"{value[0]}/{value[1]}"
    return str(value)


def _apply_dates(
    plan: RepairPlan, src: dict[str, dict[int, RawValue]], src_mtime: float, opts: RepairOptions
) -> None:
    raws: dict[tuple[str, int], bytes | None] = {}
    for key in DATE_TAGS:
        rv = src.get(key[0], {}).get(key[1])
        raws[key] = (
            rv.value if rv is not None and rv.type == 2 and isinstance(rv.value, bytes) else None
        )

    present = [r for r in raws.values() if r is not None]
    plan.raw_date = _ascii(present[0]) if present else None

    if all(dates.is_normal_form(r) for r in raws.values()):
        for key, raw in raws.items():
            assert raw is not None
            plan.ifds[key[0]][key[1]] = raw.rstrip(b"\x00")
        plan.capture_time = dates.normalize(raws[T_DATETIME_ORIGINAL])
        plan.timestamp_source = "DateTimeOriginal"
        for key in OFFSET_TIME_TAGS:
            rv = src.get(key[0], {}).get(key[1])
            if rv is not None and rv.type == 2 and isinstance(rv.value, bytes):
                plan.ifds[key[0]][key[1]] = rv.value.rstrip(b"\x00")
        return

    capture: datetime | None = None
    for key in DATE_TAGS:
        capture = dates.normalize(raws[key])
        if capture is not None:
            plan.timestamp_source = piexif.TAGS[key[0]][key[1]]["name"]
            break
    if capture is None:
        capture = datetime.fromtimestamp(src_mtime).replace(microsecond=0)
        plan.timestamp_source = "file_mtime"
        if plan.exif_present:
            plan.partial_reasons.append("EXIF had no valid date; file modification time used")
    plan.capture_time = capture
    plan.defects.append("dates missing or not in standard form")
    text = dates.format_exif(capture).encode()
    for key in DATE_TAGS:
        old = raws[key]
        plan.ifds[key[0]][key[1]] = text
        plan.changes.append(
            Change(tag_name(*key), _ascii(old) if old is not None else None, text.decode())
        )
    # Rewritten dates make any existing timezone offsets meaningless.
    for key in OFFSET_TIME_TAGS:
        if key[1] in src.get(key[0], {}):
            plan.changes.append(Change(tag_name(*key), note="dropped: dates rewritten"))
    warning = dates.sanity_warning(capture, opts.now)
    if warning:
        plan.warnings.append(warning)


def _apply_sizes(plan: RepairPlan, jf: JpegFile) -> None:
    for name in IFD0_SIZE_TAGS:
        if name in plan.raw_size_tags:
            plan.defects.append(f"{name} is not allowed in JPEG EXIF")
            plan.changes.append(Change(name, note="dropped: not allowed in JPEG EXIF"))
    if jf.height == 0:
        plan.partial_reasons.append("image height defined by DNL marker; size tags omitted")
        for name in SIZE_TAGS:
            if name in plan.raw_size_tags:
                plan.defects.append(f"{name} cannot be verified (DNL height)")
                plan.changes.append(Change(name, note="dropped: height unknown"))
        return
    targets = {"Exif.PixelXDimension": jf.width, "Exif.PixelYDimension": jf.height}
    for name, (ifd, tag) in SIZE_TAGS.items():
        old, target = plan.old_sizes[name], targets[name]
        plan.ifds[ifd][tag] = target
        if old != target:
            plan.defects.append(f"{name} is {old}, SOF says {target}")
            plan.changes.append(Change(name, None if old is None else str(old), str(target)))


def _apply_identity(plan: RepairPlan, jf: JpegFile, opts: RepairOptions) -> None:
    zeroth = plan.ifds["0th"]
    make, model = _ascii(zeroth.get(T_MAKE[1])), _ascii(zeroth.get(T_MODEL[1]))
    gpencoder = any(c.rstrip(b"\x00").strip().startswith(b"GPEncoder") for c in jf.comments)

    def set_identity() -> None:
        for (ifd, tag), target, old in ((T_MAKE, opts.make, make), (T_MODEL, opts.model, model)):
            if old != target:
                plan.defects.append(f"{tag_name(ifd, tag)} is {old!r}")
                plan.changes.append(Change(tag_name(ifd, tag), old or None, target))
            zeroth[tag] = target.encode()

    if "charmera" in f"{make} {model}".lower():
        plan.identity = "charmera(exif)"
        set_identity()
    elif gpencoder and opts.assume_charmera:
        plan.identity = "charmera(assumed)"
        blank = not make and not model
        already_target = (make, model) == (opts.make, opts.model)
        if blank or already_target:
            set_identity()
        else:
            plan.warnings.append(f"GPEncoder file has Make/Model {make!r}/{model!r}; kept")
    elif gpencoder:
        plan.identity = "generalplus-unconfirmed"


def _apply_thumbnail(
    plan: RepairPlan, exif: ExifData | None, src: dict[str, dict[int, RawValue]]
) -> None:
    if exif is None or exif.thumbnail is None:
        if "1st" in src:
            plan.changes.append(Change("IFD1", note="dropped: no thumbnail"))
        return
    if not _valid_thumbnail(exif.thumbnail):
        plan.defects.append("thumbnail is not a complete JPEG")
        plan.changes.append(Change("thumbnail", note="dropped: not a complete JPEG"))
        return
    first: dict[int, Any] = {}
    for tag, rv in src.get("1st", {}).items():
        expected = piexif.TAGS["1st"].get(tag, {}).get("type")
        if tag in _STRUCTURAL["1st"] or expected is None:
            continue
        value, problem = _convert("0th", tag, rv, expected)
        if problem is None:
            first[tag] = value
    first[0x0103] = 6  # Compression: JPEG
    # Required in IFD1 of a JPEG thumbnail (EXIF 2.32, table 17).
    first.setdefault(0x011A, (72, 1))
    first.setdefault(0x011B, (72, 1))
    first.setdefault(0x0128, 2)
    plan.ifds["1st"] = first
    plan.thumbnail = exif.thumbnail
