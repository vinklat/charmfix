"""Serialize a RepairPlan into an Exif APP1 payload.

A small TIFF writer of our own: piexif.dump omits the next-IFD pointer of
sub-IFDs (values then overlap the IFD) and does not word-align values, both of
which exiftool flags. piexif is still used for its tag/type table.
"""

from __future__ import annotations

import struct
from typing import Any

import piexif

from .jpeg import EXIF_HEADER, MAX_SEGMENT_PAYLOAD
from .repair import Change, RepairPlan

TAG_EXIF_IFD = 0x8769
TAG_GPS_IFD = 0x8825
TAG_THUMB_OFFSET = 0x0201
TAG_THUMB_LENGTH = 0x0202

_FORMATS = {1: "B", 3: "H", 4: "L", 6: "b", 8: "h", 9: "l", 11: "f", 12: "d"}

Entry = tuple[int, int, int, bytes]  # tag, type, count, value bytes


class ExifTooLargeError(Exception):
    """The rebuilt EXIF does not fit into one APP1 segment."""


def _encode(type_: int, value: Any) -> tuple[int, bytes]:
    """Return (count, bytes) for a value in piexif's representation."""
    if type_ == 2:
        data = value.encode("latin-1") if isinstance(value, str) else bytes(value)
        data = data.rstrip(b"\x00") + b"\x00"
        return len(data), data
    if type_ == 7:
        return len(value), bytes(value)
    if type_ in (5, 10):
        pairs = (value,) if isinstance(value[0], int) else value
        fmt = "L" if type_ == 5 else "l"
        return len(pairs), b"".join(struct.pack(f">{fmt}{fmt}", n, d) for n, d in pairs)
    values = (value,) if isinstance(value, int | float) else tuple(value)
    return len(values), struct.pack(f">{len(values)}{_FORMATS[type_]}", *values)


def _entries(ifd: str, tags: dict[int, Any]) -> list[Entry]:
    out = []
    for tag, value in tags.items():
        type_ = piexif.TAGS[ifd][tag]["type"]
        count, data = _encode(type_, value)
        out.append((tag, type_, count, data))
    return out


def _pad(n: int) -> int:
    return n + (n & 1)


def _ifd_size(entries: list[Entry]) -> int:
    return 2 + 12 * len(entries) + 4 + sum(_pad(len(d)) for *_, d in entries if len(d) > 4)


def _build_ifd(entries: list[Entry], offset: int, next_ifd: int) -> bytes:
    """Serialize an IFD at TIFF offset `offset`, with its out-of-line values right after it."""
    entries = sorted(entries)
    head = [struct.pack(">H", len(entries))]
    values: list[bytes] = []
    value_pos = offset + 2 + 12 * len(entries) + 4
    for tag, type_, count, data in entries:
        if len(data) <= 4:
            field = data.ljust(4, b"\x00")
        else:
            field = struct.pack(">L", value_pos)
            padded = data + b"\x00" * (_pad(len(data)) - len(data))
            values.append(padded)
            value_pos += len(padded)
        head.append(struct.pack(">HHL", tag, type_, count) + field)
    head.append(struct.pack(">L", next_ifd))
    return b"".join(head) + b"".join(values)


def serialize(ifds: dict[str, dict[int, Any]], thumbnail: bytes | None) -> bytes:
    """Return the APP1 payload: Exif header + big-endian TIFF."""
    zeroth = _entries("0th", ifds.get("0th", {}))
    exif = _entries("Exif", ifds.get("Exif", {}))
    gps = _entries("GPS", ifds.get("GPS", {}))
    first = _entries("1st", ifds.get("1st", {})) if thumbnail else []

    placeholder = b"\x00\x00\x00\x00"
    if exif:
        zeroth.append((TAG_EXIF_IFD, 4, 1, placeholder))
    if gps:
        zeroth.append((TAG_GPS_IFD, 4, 1, placeholder))
    if first:
        first += [(TAG_THUMB_OFFSET, 4, 1, placeholder), (TAG_THUMB_LENGTH, 4, 1, placeholder)]

    off0 = 8
    off_exif = off0 + _ifd_size(zeroth)
    off_gps = off_exif + (_ifd_size(exif) if exif else 0)
    off1 = off_gps + (_ifd_size(gps) if gps else 0)
    off_thumb = off1 + (_ifd_size(first) if first else 0)

    def setptr(entries: list[Entry], tag: int, value: int) -> None:
        for i, e in enumerate(entries):
            if e[0] == tag:
                entries[i] = (tag, 4, 1, struct.pack(">L", value))

    setptr(zeroth, TAG_EXIF_IFD, off_exif)
    setptr(zeroth, TAG_GPS_IFD, off_gps)
    if first and thumbnail:
        setptr(first, TAG_THUMB_OFFSET, off_thumb)
        setptr(first, TAG_THUMB_LENGTH, len(thumbnail))

    parts = [b"MM\x00\x2a" + struct.pack(">L", off0)]
    parts.append(_build_ifd(zeroth, off0, off1 if first else 0))
    if exif:
        parts.append(_build_ifd(exif, off_exif, 0))
    if gps:
        parts.append(_build_ifd(gps, off_gps, 0))
    if first and thumbnail:
        parts.append(_build_ifd(first, off1, 0))
        parts.append(thumbnail)
    return EXIF_HEADER + b"".join(parts)


def build_exif(plan: RepairPlan) -> bytes:
    """Return the APP1 payload for the plan. May drop the thumbnail to fit.

    Dropping the thumbnail is recorded in ``plan.changes`` and ``plan.warnings``.
    """
    if plan.thumbnail is not None:
        payload = serialize(plan.ifds, plan.thumbnail)
        if len(payload) <= MAX_SEGMENT_PAYLOAD:
            return payload
        reason = "EXIF too large for one APP1 segment"
        plan.thumbnail = None
        plan.ifds["1st"] = {}
        plan.changes.append(Change("thumbnail", note=f"dropped: {reason}"))
        plan.warnings.append(f"thumbnail dropped: {reason}")

    payload = serialize(plan.ifds, None)
    if len(payload) > MAX_SEGMENT_PAYLOAD:
        raise ExifTooLargeError(
            f"rebuilt EXIF is {len(payload)} bytes, limit {MAX_SEGMENT_PAYLOAD}"
        )
    return payload
