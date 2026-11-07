"""Minimal, bounds-checked JPEG segment parser and splicer.

Only the header (everything between SOI and the first SOS) is parsed into
segments. Everything from the first SOS marker to the end of the file
(scan data, any later markers, EOI and trailing bytes) is treated as an
opaque tail and is never modified.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

SOI = 0xD8
EOI = 0xD9
SOS = 0xDA
COM = 0xFE
APP0 = 0xE0
APP1 = 0xE1
APP2 = 0xE2

SOF_MARKERS = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)
# Markers that stand alone, without a length field.
_STANDALONE = frozenset({0x01, *range(0xD0, 0xD8)})

EXIF_HEADER = b"Exif\x00\x00"
XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"
XMP_EXT_HEADER = b"http://ns.adobe.com/xmp/extension/\x00"
ICC_HEADER = b"ICC_PROFILE\x00"
JFIF_HEADER = b"JFIF\x00"

MAX_SEGMENT_PAYLOAD = 0xFFFF - 2


class JpegError(Exception):
    """The file is not a JPEG we can safely rewrite."""


@dataclass(frozen=True)
class Segment:
    marker: int
    start: int  # offset of the first byte of this segment, including any 0xFF fill bytes
    end: int  # offset just past the payload
    payload: bytes  # bytes after the 2-byte length field (empty for standalone markers)

    @property
    def is_app(self) -> bool:
        return 0xE0 <= self.marker <= 0xEF

    @property
    def is_metadata(self) -> bool:
        """APPn and COM segments carry metadata; everything else is image data."""
        return self.is_app or self.marker == COM

    @property
    def is_exif(self) -> bool:
        # Some writers use "Exif\0\xff"; accept any "Exif\0" prefix.
        return self.marker == APP1 and self.payload.startswith(b"Exif\x00")

    @property
    def is_xmp(self) -> bool:
        return self.marker == APP1 and (
            self.payload.startswith(XMP_HEADER) or self.payload.startswith(XMP_EXT_HEADER)
        )

    @property
    def is_icc(self) -> bool:
        return self.marker == APP2 and self.payload.startswith(ICC_HEADER)

    @property
    def is_jfif(self) -> bool:
        return self.marker == APP0 and self.payload.startswith(JFIF_HEADER)


@dataclass(frozen=True)
class JpegFile:
    data: bytes
    segments: tuple[Segment, ...]  # header segments between SOI and the first SOS
    sos_offset: int  # offset of the first SOS marker (start of the opaque tail)
    sof_marker: int
    width: int
    height: int
    has_eoi: bool
    warnings: tuple[str, ...]

    @property
    def comments(self) -> tuple[bytes, ...]:
        return tuple(s.payload for s in self.segments if s.marker == COM)

    @property
    def exif_segments(self) -> tuple[Segment, ...]:
        return tuple(s for s in self.segments if s.is_exif)

    def exif_tiff(self) -> bytes | None:
        """TIFF bytes of the first Exif APP1 segment, without the 6-byte Exif header."""
        segs = self.exif_segments
        return segs[0].payload[6:] if segs else None

    @property
    def has_icc(self) -> bool:
        return any(s.is_icc for s in self.segments)

    def preserved_segments(self) -> tuple[bytes, ...]:
        """Raw bytes of ICC and XMP segments, which must survive unchanged."""
        return tuple(self.data[s.start : s.end] for s in self.segments if s.is_icc or s.is_xmp)


def _u16(data: bytes, pos: int) -> int:
    return (data[pos] << 8) | data[pos + 1]


def parse(data: bytes) -> JpegFile:
    n = len(data)
    if n < 4 or data[0] != 0xFF or data[1] != SOI:
        raise JpegError("missing SOI marker")

    segments: list[Segment] = []
    warnings: list[str] = []
    sof: tuple[int, int, int] | None = None
    pos = 2
    while True:
        start = pos
        if pos >= n:
            raise JpegError("file ends before SOS marker")
        if data[pos] != 0xFF:
            raise JpegError(f"expected marker at offset {pos}, found 0x{data[pos]:02X}")
        while pos < n and data[pos] == 0xFF:  # fill bytes
            pos += 1
        if pos >= n:
            raise JpegError("file ends inside marker")
        marker = data[pos]
        pos += 1
        if marker == 0x00:
            raise JpegError(f"invalid marker 0xFF00 at offset {start}")
        if marker == SOS:
            if sof is None:
                raise JpegError("SOS before any SOF marker")
            sos_offset = start
            break
        if marker == SOI:
            raise JpegError(f"unexpected SOI at offset {start}")
        if marker == EOI:
            raise JpegError("EOI before SOS: no image data")
        if marker in _STANDALONE:
            segments.append(Segment(marker, start, pos, b""))
            continue
        if pos + 2 > n:
            raise JpegError(f"truncated length of marker 0x{marker:02X}")
        length = _u16(data, pos)
        if length < 2:
            raise JpegError(f"invalid length {length} of marker 0x{marker:02X}")
        end = pos + length
        if end > n:
            raise JpegError(f"truncated segment 0x{marker:02X} at offset {start}")
        payload = data[pos + 2 : end]
        segments.append(Segment(marker, start, end, payload))
        if marker in SOF_MARKERS:
            if len(payload) < 6:
                raise JpegError("SOF segment too short")
            if sof is None:
                sof = (marker, _u16(payload, 3), _u16(payload, 1))
            else:
                warnings.append("multiple SOF markers; using the first")
        pos = end

    if sos_offset + 4 > n:
        raise JpegError("truncated SOS header")
    has_eoi = data.rfind(b"\xff\xd9") > sos_offset
    if not has_eoi:
        warnings.append("no EOI marker after scan data (file may be truncated)")
    assert sof is not None  # checked when SOS was reached
    sof_marker, width, height = sof
    if width == 0:
        raise JpegError("SOF width is 0")
    return JpegFile(
        data=data,
        segments=tuple(segments),
        sos_offset=sos_offset,
        sof_marker=sof_marker,
        width=width,
        height=height,
        has_eoi=has_eoi,
        warnings=tuple(warnings),
    )


def image_data(jf: JpegFile) -> bytes:
    """Every byte that is not APPn/COM, in original order. This is what must never change."""
    parts = [b"\xff\xd8"]
    parts.extend(jf.data[s.start : s.end] for s in jf.segments if not s.is_metadata)
    parts.append(jf.data[jf.sos_offset :])
    return b"".join(parts)


def image_digest(jf: JpegFile) -> str:
    return hashlib.sha256(image_data(jf)).hexdigest()


def splice(jf: JpegFile, exif_payload: bytes) -> bytes:
    """Return a new file with all Exif APP1 segments replaced by one new segment.

    Layout: SOI, leading JFIF APP0 (if it is the first segment), new Exif APP1,
    then every other original header segment byte-for-byte in its original
    order, then the untouched tail from the first SOS.
    """
    if not exif_payload.startswith(EXIF_HEADER):
        raise ValueError("Exif payload must start with the Exif header")
    if len(exif_payload) > MAX_SEGMENT_PAYLOAD:
        raise ValueError("Exif payload too large for one APP1 segment")

    rest = list(jf.segments)
    out = [b"\xff\xd8"]
    if rest and rest[0].is_jfif:
        first = rest.pop(0)
        out.append(jf.data[first.start : first.end])
    out.append(b"\xff\xe1" + (len(exif_payload) + 2).to_bytes(2, "big") + exif_payload)
    out.extend(jf.data[s.start : s.end] for s in rest if not s.is_exif)
    out.append(jf.data[jf.sos_offset :])
    return b"".join(out)
