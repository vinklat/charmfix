"""Find JPEG files under an input folder."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

JPEG_EXTENSIONS = frozenset({".jpg", ".jpeg"})
SKIP_DIRS = frozenset({".Trashes", ".Spotlight-V100", ".fseventsd"})
MAX_FILE_SIZE = 64 * 1024 * 1024
JPEG_MAGIC = b"\xff\xd8\xff"


@dataclass(frozen=True)
class Candidate:
    path: Path
    rel: Path  # relative to the input root
    skip_reason: str | None = None
    error: str | None = None


def _check(path: Path, rel: Path) -> Candidate:
    try:
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode):
            return Candidate(path, rel, skip_reason="symlink")
        if not stat.S_ISREG(st.st_mode):
            return Candidate(path, rel, skip_reason="not a regular file")
        if st.st_size > MAX_FILE_SIZE:
            return Candidate(path, rel, skip_reason="too large")
        with open(path, "rb") as f:
            magic = f.read(3)
    except OSError as exc:
        return Candidate(path, rel, error=f"cannot read: {exc.strerror or exc.__class__.__name__}")
    if magic != JPEG_MAGIC:
        return Candidate(path, rel, skip_reason="not JPEG content")
    return Candidate(path, rel)


def _is_jpeg_name(name: str) -> bool:
    return not name.startswith("._") and os.path.splitext(name)[1].lower() in JPEG_EXTENSIONS


def discover(root: Path, recursive: bool = True) -> Iterator[Candidate]:
    """Yield every *.jpg/*.jpeg under root in a stable order.

    AppleDouble ``._*`` files and macOS volume system folders are ignored.
    Symlinked folders are not followed.
    """
    if not root.is_dir():  # a single file, or something that is neither (checked below)
        yield _check(root, Path(root.name))
        return
    # os.walk silently skips folders it cannot list unless told otherwise; report them
    # as failures instead, so an unreadable card never looks like an empty one.
    errors: list[OSError] = []

    def unreadable() -> Iterator[Candidate]:
        while errors:
            exc = errors.pop(0)
            folder = Path(exc.filename) if exc.filename else root
            rel = folder.relative_to(root) if folder.is_relative_to(root) else Path(folder.name)
            reason = exc.strerror or exc.__class__.__name__
            yield Candidate(folder, rel, error=f"cannot list folder: {reason}")

    for dirpath, dirnames, filenames in os.walk(root, onerror=errors.append, followlinks=False):
        yield from unreadable()
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS) if recursive else []
        base = Path(dirpath)
        for name in sorted(filenames):
            if _is_jpeg_name(name):
                path = base / name
                yield _check(path, path.relative_to(root))
    yield from unreadable()


def unreadable_reason(folder: Path) -> str | None:
    """Why ``folder`` cannot be listed, or None if it can."""
    try:
        with os.scandir(folder):
            return None
    except OSError as exc:
        return exc.strerror or exc.__class__.__name__
