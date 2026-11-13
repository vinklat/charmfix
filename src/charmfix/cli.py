"""Command-line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from . import __version__
from .discovery import unreadable_reason
from .pipeline import DiskFullError, Job, RunOptions, prepare, process_file
from .repair import DEFAULT_MAKE, DEFAULT_MODEL, RepairOptions, check_camera_text
from .report import FileResult, Summary, build_report, write_report

DEFAULT_OUTPUT = Path.home() / "Pictures" / "Kodak Charmera Repaired"
MACOS_PERMISSION_HINT = (
    "macOS is blocking access. Allow the app you run charmfix from (Terminal, iTerm, "
    "VS Code, ...) under System Settings > Privacy & Security > Files and Folders > "
    "Removable Volumes (or Full Disk Access), then restart that app and try again."
)
EXIT_OK, EXIT_FAILURES, EXIT_FATAL, EXIT_INTERRUPTED = 0, 1, 2, 130

log = logging.getLogger("charmfix")


class _Progress:
    """A single status line rewritten on stderr, only when stderr is a terminal."""

    def __init__(self, stream: TextIO, enabled: bool) -> None:
        self.stream, self.enabled, self.active = stream, enabled, False

    def update(self, text: str) -> None:
        if self.enabled:
            self.stream.write("\r\x1b[K" + text[:120])
            self.stream.flush()
            self.active = True

    def clear(self) -> None:
        if self.active:
            self.stream.write("\r\x1b[K")
            self.stream.flush()
            self.active = False


class _ProgressAwareHandler(logging.StreamHandler):  # type: ignore[type-arg]
    def __init__(self, progress: _Progress) -> None:
        super().__init__(sys.stderr)
        self.progress = progress

    def emit(self, record: logging.LogRecord) -> None:
        self.progress.clear()
        super().emit(record)


def _camera_text(value: str) -> str:
    """argparse type for --make/--model: the repair rule, reported as a usage error."""
    try:
        return check_camera_text(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="charmfix",
        description="Write copies of Kodak Charmera JPEGs with repaired EXIF metadata. "
        "Image data is never changed and source files are never modified.",
    )
    p.add_argument("input", type=Path, help="folder (e.g. the mounted SD card) or a single JPEG")
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"output folder (default: {DEFAULT_OUTPUT})",
    )
    p.add_argument(
        "--recursive",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="descend into subfolders (default: yes)",
    )
    p.add_argument("-n", "--dry-run", action="store_true", help="analyse only; write no images")
    p.add_argument("--force", action="store_true", help="overwrite existing output files")
    p.add_argument("-v", "--verbose", action="store_true", help="log details for every file")
    p.add_argument("--report", type=Path, help="write a JSON report to this path")
    p.add_argument(
        "--no-preserve-structure",
        dest="preserve_structure",
        action="store_false",
        help="put all outputs directly in the output folder",
    )
    p.add_argument(
        "--no-assume-charmera",
        dest="assume_charmera",
        action="store_false",
        help="do not set the camera Make/Model on files with only a GPEncoder comment",
    )
    p.add_argument(
        "--make",
        type=_camera_text,
        default=DEFAULT_MAKE,
        metavar="TEXT",
        help=f"camera Make written to Charmera files (default: {DEFAULT_MAKE})",
    )
    p.add_argument(
        "--model",
        type=_camera_text,
        default=DEFAULT_MODEL,
        metavar="TEXT",
        help=f"camera Model written to Charmera files (default: {DEFAULT_MODEL})",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _same(a: Path, b: Path) -> bool:
    """Same file or folder on disk, whatever the spelling (letter case, Unicode form).

    A path that cannot be statted counts as different: nothing can be written there either.
    """
    if a == b:
        return True
    try:
        return a.samefile(b)
    except OSError:
        return False


def _within(path: Path, root: Path) -> bool:
    """``path`` is ``root`` or lies under it, judged by identity on disk."""
    return any(_same(p, root) for p in (path, *path.parents))


def _refusal(
    input_path: Path, is_folder: bool, output_root: Path, report_path: Path | None
) -> str | None:
    """Why these paths would put a source file at risk (CLI.md source safety), or None."""
    if is_folder:
        if _within(output_root, input_path) or _within(input_path, output_root):
            return "input and output folders must not overlap"
        if report_path and _within(report_path, input_path):
            return "the report must not be written inside the input folder"
    else:
        if _same(output_root, input_path.parent):
            return "the output folder must not be the input file's folder"
        if report_path and _same(report_path, input_path):
            return "the report must not replace the input file"
    return None


def _log_result(r: FileResult) -> None:
    if r.status == "failed":
        log.error("FAILED  %s: %s", r.rel_path, r.error)
    elif r.status == "partial":
        log.warning("partial %s: %s", r.rel_path, "; ".join(r.partial_reasons))
    elif r.warnings:
        log.warning("%-7s %s: %s", r.status, r.rel_path, "; ".join(r.warnings))
    else:
        log.debug("%-7s %s", r.status, r.rel_path)
    if r.status not in ("skipped", "failed"):
        log.debug(
            "  SOF %s, old sizes %s; date %r -> %s (source: %s); identity %s; MakerNote %s",
            r.sof_size,
            r.old_sizes,
            r.raw_date,
            r.date_written,
            r.timestamp_source,
            r.identity,
            r.makernote,
        )
        for change in r.changes:
            log.debug("  change: %s", change)
    elif r.reason:
        log.debug("  reason: %s", r.reason)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    progress = _Progress(sys.stderr, enabled=sys.stderr.isatty() and not args.verbose)
    handler = _ProgressAwareHandler(progress)
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.handlers[:] = [handler]
    log.setLevel(logging.DEBUG if args.verbose else logging.INFO)
    log.propagate = False

    input_path: Path = args.input.expanduser().resolve()
    output_root: Path = args.output.expanduser().resolve()
    if not input_path.exists():
        log.error("input not found: %s", args.input)
        return EXIT_FATAL
    report_path = args.report.expanduser().resolve() if args.report else None
    is_folder = input_path.is_dir()  # anything else is one file, as in discovery
    refusal = _refusal(input_path, is_folder, output_root, report_path)
    if refusal:
        log.error(refusal)
        return EXIT_FATAL

    opts = RunOptions(
        output_root=output_root,
        recursive=args.recursive,
        preserve_structure=args.preserve_structure,
        force=args.force,
        dry_run=args.dry_run,
        repair=RepairOptions(
            assume_charmera=args.assume_charmera, make=args.make, model=args.model
        ),
    )
    input_root = input_path if is_folder else input_path.parent
    reason = unreadable_reason(input_root)
    if reason is not None:
        log.error("cannot read %s: %s", input_root, reason)
        if sys.platform == "darwin" and reason == "Operation not permitted":
            log.error(MACOS_PERMISSION_HINT)
        return EXIT_FATAL

    summary = Summary()
    results: list[FileResult] = []
    exit_code = EXIT_OK
    try:
        jobs: list[Job] = prepare(input_path, opts)  # walks the card: Ctrl-C may land here too
        if not jobs:
            log.warning("no .jpg/.jpeg files found in %s", input_path)
        summary.discovered = len(jobs)
        for i, job in enumerate(jobs, 1):
            progress.update(f"[{i}/{len(jobs)}] {job.candidate.rel.as_posix()}")
            r = process_file(job, opts)
            results.append(r)
            summary.add(r)
            _log_result(r)
    except DiskFullError as exc:
        summary.stopped = str(exc)
        log.error("stopping: %s", exc)
        exit_code = EXIT_FATAL
    except KeyboardInterrupt:
        summary.stopped = "interrupted"
        exit_code = EXIT_INTERRUPTED
    finally:
        progress.clear()

    print(summary.render(args.dry_run))
    if report_path:
        try:
            write_report(report_path, build_report(results, summary, opts.report_options()))
        except OSError as exc:
            log.error("cannot write the report: %s", exc.strerror or exc.__class__.__name__)
            if exit_code != EXIT_INTERRUPTED:
                exit_code = EXIT_FATAL
    if exit_code == EXIT_OK and summary.failed:
        exit_code = EXIT_FAILURES
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
