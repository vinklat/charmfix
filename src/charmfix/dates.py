"""EXIF date parsing and formatting. Dates are naive local wall-clock times."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

# Accepts the standard "YYYY:MM:DD HH:MM:SS" and the broken variants seen in the
# wild, e.g. the Charmera's six-part "YYYY:MM:DD:HH:MM:SS", or "T"/"-" separators.
_DATE_RE = re.compile(r"^\s*(\d{4})[:\-](\d{2})[:\-](\d{2})[ :T\-](\d{2}):(\d{2}):(\d{2})\s*$")
_NORMAL_RE = re.compile(r"^\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}$")


def _text(raw: bytes | str) -> str:
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", errors="replace")
    return raw.rstrip("\x00")


def normalize(raw: bytes | str | None) -> datetime | None:
    """Parse an EXIF-ish date string; None for blank, zero or impossible dates."""
    if raw is None:
        return None
    m = _DATE_RE.match(_text(raw))
    if not m:
        return None
    year, month, day, hour, minute, second = (int(g) for g in m.groups())
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def is_normal_form(raw: bytes | str | None) -> bool:
    """True when the value is already a valid, standard EXIF date string."""
    if raw is None:
        return False
    text = _text(raw)
    return bool(_NORMAL_RE.match(text)) and normalize(text) is not None


def format_exif(dt: datetime) -> str:
    """``YYYY:MM:DD HH:MM:SS``, the year always four digits."""
    # The year by hand: glibc's %Y does not pad years below 1000 before Python 3.14.
    return f"{dt.year:04d}:{dt:%m:%d %H:%M:%S}"


def sanity_warning(dt: datetime, now: datetime) -> str | None:
    """A warning for dates that parse but look implausible; the date is still used."""
    if dt.year < 2000:
        return f"capture date {format_exif(dt)} is before 2000 (camera clock not set?)"
    if dt > now + timedelta(days=1):
        return f"capture date {format_exif(dt)} is in the future"
    return None
