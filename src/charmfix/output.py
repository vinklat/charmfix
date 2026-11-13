"""Output path mapping and safe, atomic writes."""

from __future__ import annotations

import contextlib
import errno
import os
import tempfile
from datetime import datetime
from pathlib import Path

# Errors meaning "this filesystem cannot hard-link" (FAT/exFAT, SMB, ...).
_NO_LINK_ERRNOS = frozenset(
    e
    for e in (
        errno.EXDEV,
        errno.EPERM,
        errno.ENOTSUP,
        getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
        errno.EMLINK,
        errno.ENOSYS,
    )
)


class OutputExistsError(Exception):
    """The destination already exists and --force was not given."""


class OutsideOutputError(Exception):
    """A symlinked folder would send the write outside the output folder."""


DATE_PREFIX_FORMAT = "%Y%m%d-%H%M%S_"


class OutputNames:
    """Output paths for one run, assigned in processing order and unique within the run.

    Collisions are detected case-insensitively (macOS and FAT are case-insensitive).
    Existing files on disk are *not* considered here: those are skipped later.
    """

    def __init__(self, root: Path, *, preserve_structure: bool, date_prefix: bool) -> None:
        self.root, self.preserve_structure, self.date_prefix = root, preserve_structure, date_prefix
        self._claimed: set[str] = set()

    def assign(self, rel: Path, taken: datetime) -> Path:
        """Claim the output path for the input-relative ``rel``, captured at ``taken``."""
        target = self.root / (rel if self.preserve_structure else Path(rel.name))
        if self.date_prefix:
            prefix = taken.strftime(DATE_PREFIX_FORMAT)
            if not target.name.startswith(prefix):  # a charmfix output keeps its name
                target = target.with_name(prefix + target.name)
        candidate, n = target, 0
        while str(candidate).casefold() in self._claimed:
            n += 1
            candidate = target.with_name(f"{target.stem}_{n}{target.suffix}")
        self._claimed.add(str(candidate).casefold())
        return candidate


def _current_umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask


def _unlink_quietly(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


def check_inside(dest: Path, root: Path) -> None:
    """Raise OutsideOutputError if ``dest`` leaves ``root`` once symlinks are followed."""
    if not dest.parent.resolve().is_relative_to(root.resolve()):
        raise OutsideOutputError("a symlinked folder leads outside the output folder")


def write_atomic(
    dest: Path, data: bytes, *, root: Path, force: bool, times_ns: tuple[int, int]
) -> list[str]:
    """Write ``data`` to ``dest`` without ever exposing a partial file.

    ``dest`` must stay under the output folder ``root`` once symlinks are followed.
    Without ``force`` an existing ``dest`` is never replaced, even if another
    process creates it concurrently. Returns warnings (e.g. timestamps not set).
    """
    check_inside(dest, root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not force and dest.exists():
        raise OutputExistsError(str(dest))
    fd, tmp_name = tempfile.mkstemp(prefix=".charmfix-", suffix=".tmp", dir=dest.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), 0o666 & ~_current_umask())  # mkstemp uses 0600
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if tmp.read_bytes() != data:
            raise OSError(errno.EIO, "temporary file does not match written data")
        if force:
            os.replace(tmp, dest)
        else:
            _publish_exclusive(tmp, dest)
    finally:
        _unlink_quietly(tmp)

    warnings = []
    try:
        os.utime(dest, ns=times_ns)
    except OSError as exc:
        warnings.append(f"could not set file times: {exc.strerror}")
    return warnings


def _publish_exclusive(tmp: Path, dest: Path) -> None:
    try:
        os.link(tmp, dest)
        return
    except FileExistsError as exc:
        raise OutputExistsError(str(dest)) from exc
    except OSError as exc:
        if exc.errno not in _NO_LINK_ERRNOS:
            raise
    # No hard links: reserve the name with O_EXCL, then move the data over it.
    try:
        placeholder = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError as exc:
        raise OutputExistsError(str(dest)) from exc
    os.close(placeholder)
    try:
        os.replace(tmp, dest)
    except BaseException:
        _unlink_quietly(dest)
        raise
