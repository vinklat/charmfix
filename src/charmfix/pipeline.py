"""Process one file end to end: read, analyse, rebuild, verify, write."""

from __future__ import annotations

import errno
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from . import jpeg
from .discovery import Candidate
from .exif_read import read_exif
from .exif_write import ExifTooLargeError, build_exif
from .output import (
    OutputExistsError,
    OutputNames,
    OutsideOutputError,
    check_inside,
    write_atomic,
)
from .repair import RepairOptions, RepairPlan, plan_repair
from .report import FileResult
from .verify import VerificationError, verify_output

log = logging.getLogger("charmfix")


class DiskFullError(Exception):
    """The output volume is full; every following file would fail too."""


@dataclass(frozen=True)
class RunOptions:
    output_root: Path
    recursive: bool = True
    preserve_structure: bool = True
    force: bool = False
    dry_run: bool = False
    date_prefix: bool = True
    # RepairOptions validates its target text when built: before any file is planned.
    repair: RepairOptions = field(default_factory=RepairOptions)

    def report_options(self) -> dict[str, bool | str]:
        """The options for the JSON report, flat and without the output folder (a path)."""
        run = {k: v for k, v in asdict(self).items() if k not in ("output_root", "repair")}
        return run | asdict(self.repair)

    def output_names(self) -> OutputNames:
        """A fresh name register for one run."""
        return OutputNames(
            self.output_root,
            preserve_structure=self.preserve_structure,
            date_prefix=self.date_prefix,
        )


def _error_text(exc: BaseException) -> str:
    # OSError's str() includes the absolute path; keep reports free of user paths.
    if isinstance(exc, OSError):
        return f"{exc.__class__.__name__}: {exc.strerror or 'I/O error'}"
    return f"{exc.__class__.__name__}: {exc}"


def analyse(
    data: bytes, src_mtime: float, now: datetime, options: RepairOptions
) -> tuple[jpeg.JpegFile, RepairPlan]:
    """Parse one file in memory and plan its repair. Raises JpegError if it cannot be parsed."""
    jf = jpeg.parse(data)
    tiff = jf.exif_tiff()
    exif = read_exif(tiff) if tiff is not None else None
    return jf, plan_repair(jf, exif, src_mtime, now, options)


def build(jf: jpeg.JpegFile, plan: RepairPlan, data: bytes) -> bytes:
    """The verified output bytes for an analysed file.

    Raises ExifTooLargeError or VerificationError for files that cannot be repaired safely.
    """
    if not plan.needs_rebuild:
        return data  # already valid: byte-for-byte copy
    out = jpeg.splice(jf, build_exif(plan))
    verify_output(jf, out, plan)
    return out


def rebuild(
    data: bytes, src_mtime: float, now: datetime, options: RepairOptions
) -> tuple[jpeg.JpegFile, RepairPlan, bytes]:
    """``analyse`` then ``build``: one file in memory, start to end."""
    jf, plan = analyse(data, src_mtime, now, options)
    return jf, plan, build(jf, plan, data)


def process_file(
    cand: Candidate, opts: RunOptions, names: OutputNames, now: datetime | None = None
) -> FileResult:
    """Process one candidate; its output name is claimed from ``names`` once it is analysed.

    ``names`` is the run's single register (``opts.output_names()``), shared by every call:
    it keeps output names unique within the run, and its root is where files are written.
    """
    result = FileResult(rel_path=cand.rel.as_posix(), status="failed")
    if cand.skip_reason:
        result.status, result.reason = "skipped", cand.skip_reason
        return result
    if cand.error:
        result.error = cand.error
        return result

    try:
        _process(cand, opts, names, result, now or datetime.now())
    except OutputExistsError:  # another writer created the output after the check
        result.status, result.reason = "skipped", "exists"
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise DiskFullError("output volume is full") from exc
        result.status, result.error = "failed", _error_text(exc)
    except (jpeg.JpegError, VerificationError, ExifTooLargeError, OutsideOutputError) as exc:
        result.status, result.error = "failed", _error_text(exc)
    except Exception as exc:  # one bad file must never stop the batch
        log.debug("unexpected error on %s", result.rel_path, exc_info=True)
        result.status, result.error = "failed", _error_text(exc)
    return result


def _process(
    cand: Candidate, opts: RunOptions, names: OutputNames, result: FileResult, now: datetime
) -> None:
    with open(cand.path, "rb") as f:
        st = os.fstat(f.fileno())
        data = f.read()

    jf, plan = analyse(data, st.st_mtime, now, opts.repair)
    assert plan.capture_time is not None  # rule 1 always sets it, falling back to the mtime
    dest = names.assign(cand.rel, plan.capture_time)
    result.output = dest.relative_to(names.root).as_posix()
    check_inside(dest, names.root)  # before the `exists` check, which follows symlinks
    if not opts.force and dest.exists():
        result.status, result.reason = "skipped", "exists"
        return

    out = build(jf, plan, data)
    result.sof_size = (jf.width, jf.height)
    result.image_sha256 = jpeg.image_digest(jf)
    result.old_sizes = plan.old_sizes
    result.raw_date = plan.raw_date
    result.date_written = plan.date_written
    result.timestamp_source = plan.timestamp_source
    result.changes = [str(c) for c in plan.changes]
    result.exif_rebuilt = plan.needs_rebuild
    result.makernote = plan.makernote_status
    result.identity = plan.identity
    result.private_text = plan.private_text
    result.partial_reasons = list(plan.partial_reasons)
    result.warnings = list(plan.warnings)

    if not opts.dry_run:
        result.warnings += write_atomic(
            dest,
            out,
            root=names.root,
            force=opts.force,
            times_ns=(st.st_atime_ns, st.st_mtime_ns),
        )
    result.status = plan.status_hint
