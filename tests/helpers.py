"""Builders for test JPEGs and deliberately broken EXIF blocks."""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

SAMPLES_DIR = Path(__file__).parent / "samples"
REAL_SAMPLES = sorted(SAMPLES_DIR.glob("*/*.jpg"))

# ---------------------------------------------------------------- JPEG level


def make_jpeg(width: int = 64, height: int = 48, *, icc: bytes | None = None) -> bytes:
    """A small baseline JPEG from Pillow (with JFIF APP0, no EXIF)."""
    img = Image.new("RGB", (width, height))
    for x in range(width):
        for y in range(height):
            img.putpixel((x, y), ((x * 4) % 256, (y * 5) % 256, ((x + y) * 3) % 256))
    buf = io.BytesIO()
    kwargs = {"quality": 90}
    if icc is not None:
        kwargs["icc_profile"] = icc  # type: ignore[assignment]
    img.save(buf, "JPEG", **kwargs)
    return buf.getvalue()


def segment(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def split_segments(data: bytes) -> tuple[list[bytes], bytes]:
    """Split a JPEG into header segment bytes (excluding SOI) and the tail from SOS."""
    pos, segs = 2, list[bytes]()
    while True:
        marker = data[pos + 1]
        if marker == 0xDA:
            return segs, data[pos:]
        length = struct.unpack(">H", data[pos + 2 : pos + 4])[0]
        segs.append(data[pos : pos + 2 + length])
        pos += 2 + length


def assemble(segs: list[bytes], tail: bytes) -> bytes:
    return b"\xff\xd8" + b"".join(segs) + tail


def insert_after_soi(data: bytes, *segs: bytes) -> bytes:
    return data[:2] + b"".join(segs) + data[2:]


def charmera_layout(data: bytes, comment: bytes = b"GPEncoder") -> bytes:
    """Rearrange a Pillow JPEG into the Charmera layout: SOF first, then COM, no APP0."""
    segs, tail = split_segments(data)
    segs = [s for s in segs if s[1] != 0xE0]
    sof = next(s for s in segs if s[1] == 0xC0)
    rest = [s for s in segs if s is not sof]
    return assemble([sof, segment(0xFE, comment), *rest], tail)


def exif_segment(tiff: bytes) -> bytes:
    return segment(0xE1, b"Exif\x00\x00" + tiff)


def with_exif(data: bytes, tiff: bytes) -> bytes:
    return insert_after_soi(data, exif_segment(tiff))


def set_sof_height(data: bytes, height: int) -> bytes:
    i = data.index(b"\xff\xc0")
    return data[: i + 5] + struct.pack(">H", height) + data[i + 7 :]


# ---------------------------------------------------------------- TIFF level

TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}


@dataclass
class E:
    """One IFD entry. ``offset`` forces the value offset (e.g. out of bounds)."""

    tag: int
    type: int
    count: int
    data: bytes
    offset: int | None = None


def ascii_(tag: int, text: str | bytes) -> E:
    raw = (text.encode() if isinstance(text, str) else text) + b"\x00"
    return E(tag, 2, len(raw), raw)


def short(tag: int, *values: int) -> E:
    return E(tag, 3, len(values), struct.pack(f">{len(values)}H", *values))


def long_(tag: int, *values: int) -> E:
    return E(tag, 4, len(values), struct.pack(f">{len(values)}L", *values))


def rational(tag: int, *pairs: tuple[int, int]) -> E:
    return E(tag, 5, len(pairs), b"".join(struct.pack(">LL", n, d) for n, d in pairs))


def undefined(tag: int, data: bytes) -> E:
    return E(tag, 7, len(data), data)


def build_tiff(
    ifd0: list[E],
    exif: list[E] | None = None,
    gps: list[E] | None = None,
    ifd1: list[E] | None = None,
    thumbnail: bytes | None = None,
    *,
    ifd0_next: int | None = None,
    declared_counts: dict[str, int] | None = None,
    truncate: int | None = None,
) -> bytes:
    """Big-endian TIFF with IFD0 -> Exif -> GPS -> IFD1 -> thumbnail, laid out in order.

    ``declared_counts`` overrides the entry count written for an IFD (to fake huge
    or truncated tables), ``ifd0_next`` overrides IFD0's next pointer (loops),
    ``truncate`` cuts the result.
    """
    declared_counts = declared_counts or {}
    ifds: list[tuple[str, list[E]]] = [("0th", list(ifd0))]
    if exif is not None:
        ifds[0][1].append(long_(0x8769, 0))
        ifds.append(("Exif", list(exif)))
    if gps is not None:
        ifds[0][1].append(long_(0x8825, 0))
        ifds.append(("GPS", list(gps)))
    if ifd1 is not None:
        ifd1 = [*ifd1, long_(0x0201, 0), long_(0x0202, len(thumbnail or b""))]
        ifds.append(("1st", ifd1))

    def size(entries: list[E]) -> int:
        vals = sum(len(e.data) + (len(e.data) & 1) for e in entries if len(e.data) > 4)
        return 2 + 12 * len(entries) + 4 + vals

    offsets, pos = {}, 8
    for name, entries in ifds:
        offsets[name] = pos
        pos += size(entries)
    thumb_offset = pos

    def patch(entries: list[E], tag: int, value: int) -> None:
        for e in entries:
            if e.tag == tag:
                e.data = struct.pack(">L", value)

    if "Exif" in offsets:
        patch(ifds[0][1], 0x8769, offsets["Exif"])
    if "GPS" in offsets:
        patch(ifds[0][1], 0x8825, offsets["GPS"])
    if ifd1 is not None:
        patch(ifd1, 0x0201, thumb_offset)

    out = bytearray(b"MM\x00\x2a" + struct.pack(">L", 8))
    for name, entries in ifds:
        base = offsets[name]
        entries = sorted(entries, key=lambda e: e.tag)
        value_pos = base + 2 + 12 * len(entries) + 4
        head = bytearray(struct.pack(">H", declared_counts.get(name, len(entries))))
        values = bytearray()
        for e in entries:
            if len(e.data) <= 4 and e.offset is None:
                field = e.data.ljust(4, b"\x00")
            else:
                field = struct.pack(">L", e.offset if e.offset is not None else value_pos)
                if e.offset is None:
                    padded = e.data + b"\x00" * (len(e.data) & 1)
                    values += padded
                    value_pos += len(padded)
            head += struct.pack(">HHL", e.tag, e.type, e.count) + field
        if name == "0th":
            nxt = ifd0_next if ifd0_next is not None else offsets.get("1st", 0)
        else:
            nxt = 0
        head += struct.pack(">L", nxt)
        out += head + values
    if thumbnail and ifd1 is not None:
        out += thumbnail
    data = bytes(out)
    return data[:truncate] if truncate is not None else data


# ---------------------------------------------------------------- canonical defects

SIX_PART_DATE = "2026:03:03:12:16:29"
NORMAL_DATE = "2026:03:03 12:16:29"


def standard_exif_entries(date: str = NORMAL_DATE) -> list[E]:
    return [
        ascii_(0x9003, date),
        ascii_(0x9004, date),
        undefined(0x9000, b"0232"),
        undefined(0xA000, b"0100"),
        undefined(0x9101, b"\x01\x02\x03\x00"),
        short(0xA001, 1),
    ]


def standard_ifd0_entries(
    date: str = NORMAL_DATE, make: str = "Kodak", model: str = "Charmera"
) -> list[E]:
    return [
        ascii_(0x010F, make),
        ascii_(0x0110, model),
        short(0x0112, 1),
        rational(0x011A, (72, 1)),
        rational(0x011B, (72, 1)),
        short(0x0128, 2),
        ascii_(0x0132, date),
        short(0x0213, 1),
    ]


def broken_charmera_tiff(width: int = 640, height: int = 480) -> bytes:
    """The known Charmera defects: six-part dates, wrong sizes, out-of-bounds MakerNote."""
    return build_tiff(
        [ascii_(0x010F, "Kodak"), ascii_(0x0110, "Charmera"), ascii_(0x0132, SIX_PART_DATE)],
        [
            ascii_(0x9003, SIX_PART_DATE),
            ascii_(0x9004, SIX_PART_DATE),
            long_(0xA002, width),
            long_(0xA003, height),
            E(0x927C, 7, 64, b"", offset=0x00FF_0000),
            rational(0x829A, (1, 60)),
        ],
    )


def valid_tiff(width: int = 64, height: int = 48, **kw: str) -> bytes:
    exif = [*standard_exif_entries(), long_(0xA002, width), long_(0xA003, height)]
    return build_tiff(standard_ifd0_entries(**kw), exif)
