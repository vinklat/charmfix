"""Per-file results, batch summary and the JSON report."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from . import __version__

FileStatus = Literal["repaired", "partial", "already_valid", "skipped", "failed"]


@dataclass
class FileResult:
    rel_path: str
    status: FileStatus
    output: str | None = None  # relative to the output root
    reason: str | None = None  # why it was skipped
    error: str | None = None
    sof_size: tuple[int, int] | None = None
    old_sizes: dict[str, int | None] = field(default_factory=dict)
    raw_date: str | None = None
    date_written: str | None = None
    timestamp_source: str | None = None
    changes: list[str] = field(default_factory=list)
    exif_rebuilt: bool = False
    makernote: str | None = None
    identity: str | None = None
    private_text: dict[str, str] = field(default_factory=dict)
    partial_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    image_sha256: str | None = None

    @property
    def timestamp_fixed(self) -> bool:
        return self.exif_rebuilt and any("DateTimeOriginal" in c for c in self.changes)


@dataclass
class Summary:
    discovered: int = 0
    statuses: Counter[str] = field(default_factory=Counter)
    skipped_reasons: Counter[str] = field(default_factory=Counter)
    timestamps_fixed: int = 0
    exif_rebuilt: int = 0
    makernotes_discarded: int = 0
    stopped: str | None = None  # why the batch stopped early, if it did

    def add(self, r: FileResult) -> None:
        self.statuses[r.status] += 1
        if r.status == "skipped" and r.reason:
            self.skipped_reasons[r.reason] += 1
        if r.status in ("repaired", "partial"):
            self.timestamps_fixed += r.timestamp_fixed
            self.exif_rebuilt += r.exif_rebuilt
            self.makernotes_discarded += bool(r.makernote and r.makernote.startswith("discarded"))

    @property
    def failed(self) -> int:
        return self.statuses["failed"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "discovered": self.discovered,
            "processed": sum(self.statuses.values()),
            "repaired": self.statuses["repaired"],
            "partial": self.statuses["partial"],
            "already_valid": self.statuses["already_valid"],
            "skipped": self.statuses["skipped"],
            "skipped_reasons": dict(sorted(self.skipped_reasons.items())),
            "failed": self.failed,
            "timestamps_fixed": self.timestamps_fixed,
            "exif_rebuilt": self.exif_rebuilt,
            "makernotes_discarded": self.makernotes_discarded,
            "stopped": self.stopped,
        }

    def render(self, dry_run: bool) -> str:
        d = self.to_dict()
        rows = [
            ("discovered", d["discovered"]),
            ("repaired", d["repaired"]),
            ("partial", d["partial"]),
            ("already valid", d["already_valid"]),
            ("skipped", d["skipped"]),
            *((f"  {reason}", n) for reason, n in d["skipped_reasons"].items()),
            ("failed", d["failed"]),
            ("timestamps fixed", d["timestamps_fixed"]),
            ("EXIF rebuilt", d["exif_rebuilt"]),
            ("MakerNotes dropped", d["makernotes_discarded"]),
        ]
        lines = [f"Summary{' (dry run, nothing written)' if dry_run else ''}:"]
        lines += [f"  {label:<22}{value}" for label, value in rows]
        if self.stopped:
            lines.append(f"  STOPPED EARLY: {self.stopped}")
        return "\n".join(lines)


def build_report(
    results: list[FileResult], summary: Summary, options: dict[str, Any]
) -> dict[str, Any]:
    """The JSON report. Paths are relative; no free-text tag content is included."""
    return {
        "tool": "charmfix",
        "version": __version__,
        "options": options,
        "summary": summary.to_dict(),
        "files": [asdict(r) for r in results],
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    """Write the report atomically: a temp file is renamed over ``path``.

    An existing file at ``path`` is replaced, never written through, so a hard link
    there (possibly to a source file) keeps its content, and a crash leaves no half report.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=".charmfix-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            umask = os.umask(0)
            os.umask(umask)
            os.fchmod(f.fileno(), 0o666 & ~umask)  # mkstemp uses 0600
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
