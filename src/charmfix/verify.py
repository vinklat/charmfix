"""Independent re-check of a rebuilt file before it is written."""

from __future__ import annotations

from . import jpeg
from .exif_read import read_exif
from .repair import DATE_TAGS, SIZE_TAGS, T_MAKE, T_MODEL, T_ORIENTATION, RepairPlan, tag_name


class VerificationError(Exception):
    pass


def _raw_segments(jf: jpeg.JpegFile) -> list[bytes]:
    return [jf.data[s.start : s.end] for s in jf.segments if not s.is_exif]


def verify_output(src: jpeg.JpegFile, out_bytes: bytes, plan: RepairPlan) -> None:
    """Raise VerificationError unless the output is exactly what was planned."""
    try:
        out = jpeg.parse(out_bytes)
    except jpeg.JpegError as exc:
        raise VerificationError(f"output is not a parseable JPEG: {exc}") from exc

    if jpeg.image_data(out) != jpeg.image_data(src):
        raise VerificationError(
            f"image data changed: {jpeg.image_digest(src)} -> {jpeg.image_digest(out)}"
        )
    if _raw_segments(out) != _raw_segments(src):
        raise VerificationError("a non-Exif APP/COM segment changed")
    if out.preserved_segments() != src.preserved_segments():
        raise VerificationError("ICC or XMP segment changed")
    if len(out.exif_segments) != 1:
        raise VerificationError(f"expected one Exif segment, found {len(out.exif_segments)}")

    tiff = out.exif_tiff()
    assert tiff is not None
    exif = read_exif(tiff)
    if not exif.readable or exif.warnings:
        raise VerificationError(f"rebuilt EXIF has problems: {exif.warnings or 'unreadable'}")

    def written(ifd: str, tag: int) -> object:
        rv = exif.get(ifd, tag)
        if rv is None:
            return None
        if isinstance(rv.value, bytes):
            return rv.value.rstrip(b"\x00") if rv.type == 2 else rv.value
        return rv.value[0] if len(rv.value) == 1 else rv.value

    checks = [*DATE_TAGS, T_ORIENTATION, T_MAKE, T_MODEL, *SIZE_TAGS.values()]
    for ifd, tag in checks:
        planned = plan.ifds[ifd].get(tag)
        actual = written(ifd, tag)
        if planned != actual:
            raise VerificationError(f"{tag_name(ifd, tag)} is {actual!r}, planned {planned!r}")
    if src.height:
        for name, (ifd, tag) in SIZE_TAGS.items():
            expected = src.width if name.endswith(("Width", "XDimension")) else src.height
            if written(ifd, tag) != expected:
                raise VerificationError(f"{name} does not match the SOF size {expected}")
