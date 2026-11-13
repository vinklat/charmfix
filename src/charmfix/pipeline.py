"""Process one file end to end: read, analyse, rebuild, verify, write."""

from __future__ import annotations

import errno
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from . import jpeg
from .discovery import Candidate, discover
from .exif_read import read_exif
from .exif_write import ExifTooLargeError, build_exif
from .output import (
    OutputExistsError,
    OutsideOutputError,
    assign_outputs,
    check_inside,
    write_atomic,
)
from .repair import DEFAULT_MAKE, DEFAULT_MODEL, RepairOptions, RepairPlan, plan_repair
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
    assume_charmera: bool = True
    make: str = DEFAULT_MAKE
    model: str = DEFAULT_MODEL

    def report_options(self) -> dict[str, bool | str]:
        """The options for the JSON report: everything but the output folder (no paths)."""
        return {k: v for k, v in asdict(self).items() if k != "output_root"}


@dataclass(frozen=True)
class Job:
    candidate: Candidate
    dest: Path


def prepare(input_path: Path, opts: RunOptions) -> list[Job]:
    candidates = list(discover(input_path, recursive=opts.recursive))
    dests = assign_outputs((c.rel for c in candidates), opts.output_root, opts.preserve_structure)
    return [Job(c, d) for c, d in zip(candidates, dests, strict=True)]


def _error_text(exc: BaseException) -> str:
    # OSError's str() includes the absolute path; keep reports free of user paths.
    if isinstance(exc, OSError):
        return f"{exc.__class__.__name__}: {exc.strerror or 'I/O error'}"
    return f"{exc.__class__.__name__}: {exc}"


def rebuild(
    data: bytes, src_mtime: float, options: RepairOptions
) -> tuple[jpeg.JpegFile, RepairPlan, bytes]:
    """Analyse one file in memory and return the verified output bytes.

    Raises JpegError, ExifTooLargeError or VerificationError for files that
    cannot be repaired safely.
    """
    jf = jpeg.parse(data)
    tiff = jf.exif_tiff()
    exif = read_exif(tiff) if tiff is not None else None
    plan = plan_repair(jf, exif, src_mtime, options)
    if not plan.needs_rebuild:
        return jf, plan, data  # already valid: byte-for-byte copy
    out = jpeg.splice(jf, build_exif(plan))
    verify_output(jf, out, plan)
    return jf, plan, out


def process_file(job: Job, opts: RunOptions, now: datetime | None = None) -> FileResult:
    cand = job.candidate
    result = FileResult(rel_path=cand.rel.as_posix(), status="failed")
    try:
        result.output = job.dest.relative_to(opts.output_root).as_posix()
    except ValueError:
        result.output = job.dest.name
    if cand.skip_reason:
        result.status, result.reason, result.output = "skipped", cand.skip_reason, None
        return result
    if cand.error:
        result.error = cand.error
        return result
    try:  # before the `exists` check, which would follow the symlink
        check_inside(job.dest, opts.output_root)
    except OutsideOutputError as exc:
        result.error = _error_text(exc)
        return result
    if not opts.force and job.dest.exists():
        result.status, result.reason = "skipped", "exists"
        return result

    try:
        _process(job, opts, result, now or datetime.now())
    except OutputExistsError:
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


def _process(job: Job, opts: RunOptions, result: FileResult, now: datetime) -> None:
    with open(job.candidate.path, "rb") as f:
        st = os.fstat(f.fileno())
        data = f.read()

    repair_opts = RepairOptions(
        assume_charmera=opts.assume_charmera, now=now, make=opts.make, model=opts.model
    )
    jf, plan, out = rebuild(data, st.st_mtime, repair_opts)
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
            job.dest,
            out,
            root=opts.output_root,
            force=opts.force,
            times_ns=(st.st_atime_ns, st.st_mtime_ns),
        )
    result.status = plan.status_hint
