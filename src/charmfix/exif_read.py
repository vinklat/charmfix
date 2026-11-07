"""Tolerant TIFF/EXIF reader.

Never raises on malformed input: every problem is skipped and recorded as a
warning. Values are decoded into plain Python types; the original bytes are
kept as well.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Literal

TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}
MAX_ENTRIES = 1024

TAG_EXIF_IFD = 0x8769
TAG_GPS_IFD = 0x8825
TAG_INTEROP_IFD = 0xA005
TAG_MAKERNOTE = 0x927C
TAG_THUMB_OFFSET = 0x0201
TAG_THUMB_LENGTH = 0x0202

IfdName = Literal["0th", "Exif", "GPS", "Interop", "1st"]
MakerNoteStatus = Literal["absent", "present", "invalid"]

Value = bytes | tuple[int, ...] | tuple[tuple[int, int], ...] | tuple[float, ...]


@dataclass(frozen=True)
class RawValue:
    type: int
    count: int
    raw: bytes  # value bytes exactly as stored
    value: Value  # bytes for ASCII/UNDEFINED, otherwise a tuple of numbers/rationals


@dataclass
class ExifData:
    byte_order: str = ""
    ifds: dict[str, dict[int, RawValue]] = field(default_factory=dict)
    thumbnail: bytes | None = None
    warnings: list[str] = field(default_factory=list)
    makernote_status: MakerNoteStatus = "absent"
    duplicate_tags: list[tuple[str, int]] = field(default_factory=list)
    readable: bool = False

    def get(self, ifd: str, tag: int) -> RawValue | None:
        return self.ifds.get(ifd, {}).get(tag)


def _decode(type_: int, count: int, raw: bytes, bo: str) -> Value:
    if type_ in (2, 7):
        return raw
    if type_ == 1:
        return tuple(raw)
    if type_ == 6:
        return struct.unpack(f"{count}b", raw)
    fmt = {3: "H", 8: "h", 4: "L", 9: "l", 13: "L", 11: "f", 12: "d"}.get(type_)
    if fmt is not None:
        return struct.unpack(f"{bo}{count}{fmt}", raw)
    # rationals
    nums = struct.unpack(f"{bo}{count * 2}{'L' if type_ == 5 else 'l'}", raw)
    return tuple((nums[i], nums[i + 1]) for i in range(0, len(nums), 2))


class _Reader:
    def __init__(self, tiff: bytes) -> None:
        self.data = tiff
        self.n = len(tiff)
        self.result = ExifData()
        self.visited: set[int] = set()
        self.bo = ">"

    def warn(self, msg: str) -> None:
        self.result.warnings.append(msg)

    def u16(self, pos: int) -> int:
        return int(struct.unpack_from(f"{self.bo}H", self.data, pos)[0])

    def u32(self, pos: int) -> int:
        return int(struct.unpack_from(f"{self.bo}L", self.data, pos)[0])

    def run(self) -> ExifData:
        d = self.data
        if self.n < 8:
            self.warn("EXIF block too short for a TIFF header")
            return self.result
        if d[:4] == b"II*\x00":
            self.bo, self.result.byte_order = "<", "II"
        elif d[:4] == b"MM\x00*":
            self.bo, self.result.byte_order = ">", "MM"
        else:
            self.warn("invalid TIFF header in EXIF block")
            return self.result

        ifd0 = self.read_ifd(self.u32(4), "0th")
        if ifd0 is None:
            return self.result
        self.result.readable = True
        entries0, next_offset = ifd0

        exif_ptr = self.pointer(entries0, TAG_EXIF_IFD, "0th")
        if exif_ptr is not None:
            exif = self.read_ifd(exif_ptr, "Exif")
            if exif is not None:
                interop_ptr = self.pointer(exif[0], TAG_INTEROP_IFD, "Exif")
                if interop_ptr is not None:
                    self.read_ifd(interop_ptr, "Interop")
        gps_ptr = self.pointer(entries0, TAG_GPS_IFD, "0th")
        if gps_ptr is not None:
            self.read_ifd(gps_ptr, "GPS")
        if next_offset:
            ifd1 = self.read_ifd(next_offset, "1st")
            if ifd1 is not None:
                self.read_thumbnail(ifd1[0])
        return self.result

    def pointer(self, entries: dict[int, RawValue], tag: int, ifd: str) -> int | None:
        rv = entries.get(tag)
        if rv is None:
            return None
        if rv.type not in (4, 13) or rv.count != 1 or not isinstance(rv.value[0], int):
            self.warn(f"{ifd}: invalid IFD pointer tag 0x{tag:04X}")
            return None
        return rv.value[0]

    def read_ifd(self, offset: int, name: str) -> tuple[dict[int, RawValue], int] | None:
        if offset in self.visited:
            self.warn(f"{name}: IFD offset {offset} already visited (loop)")
            return None
        if offset < 8 or offset + 2 > self.n:
            self.warn(f"{name}: IFD offset {offset} outside EXIF block")
            return None
        self.visited.add(offset)
        count = self.u16(offset)
        if count > MAX_ENTRIES:
            self.warn(f"{name}: implausible entry count {count}")
            return None
        avail = (self.n - offset - 2) // 12
        if count > avail:
            self.warn(f"{name}: IFD truncated ({count} entries declared, {avail} present)")
            count = avail
        entries: dict[int, RawValue] = {}
        for i in range(count):
            self.read_entry(offset + 2 + 12 * i, name, entries)
        self.result.ifds[name] = entries
        if name != "0th":  # only IFD0 links to another IFD (IFD1); sub-IFD links are ignored
            return entries, 0
        next_pos = offset + 2 + 12 * count
        next_offset = self.u32(next_pos) if next_pos + 4 <= self.n else 0
        if next_offset and next_offset + 2 > self.n:
            self.warn(f"{name}: next IFD offset {next_offset} outside EXIF block")
            next_offset = 0
        return entries, next_offset

    def read_entry(self, pos: int, ifd: str, entries: dict[int, RawValue]) -> None:
        tag = self.u16(pos)
        type_ = self.u16(pos + 2)
        count = self.u32(pos + 4)
        is_makernote = ifd == "Exif" and tag == TAG_MAKERNOTE

        def bad(msg: str) -> None:
            self.warn(f"{ifd}: tag 0x{tag:04X} {msg}; skipped")
            if is_makernote:
                self.result.makernote_status = "invalid"

        size_per = TYPE_SIZES.get(type_)
        if size_per is None:
            bad(f"has unknown type {type_}")
            return
        if count == 0:
            bad("has count 0")
            return
        size = size_per * count
        if size > self.n:
            bad(f"declares {size} bytes, more than the EXIF block")
            return
        if size <= 4:
            raw = self.data[pos + 8 : pos + 8 + size]
        else:
            value_offset = self.u32(pos + 8)
            if value_offset + size > self.n:
                bad(f"value at offset {value_offset} (+{size}) is outside the EXIF block")
                return
            raw = self.data[value_offset : value_offset + size]
        if tag in entries:
            self.warn(f"{ifd}: duplicate tag 0x{tag:04X}; first value kept")
            self.result.duplicate_tags.append((ifd, tag))
            return
        entries[tag] = RawValue(type_, count, raw, _decode(type_, count, raw, self.bo))
        if is_makernote:
            self.result.makernote_status = "present"

    def read_thumbnail(self, entries: dict[int, RawValue]) -> None:
        off_rv = entries.get(TAG_THUMB_OFFSET)
        len_rv = entries.get(TAG_THUMB_LENGTH)
        if off_rv is None or len_rv is None:
            return
        off, length = off_rv.value[0], len_rv.value[0]
        if not isinstance(off, int) or not isinstance(length, int):
            self.warn("1st: invalid thumbnail pointer")
            return
        if length == 0 or off + length > self.n:
            self.warn(f"1st: thumbnail at offset {off} (+{length}) is outside the EXIF block")
            return
        self.result.thumbnail = self.data[off : off + length]


def read_exif(tiff: bytes) -> ExifData:
    """Parse TIFF bytes (the APP1 payload after the 6-byte Exif header)."""
    try:
        return _Reader(tiff).run()
    except Exception as exc:  # defensive: the reader must never crash the pipeline
        result = ExifData()
        result.warnings.append(f"EXIF reader error: {exc!r}")
        return result
