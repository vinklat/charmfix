from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pytest
from helpers import (
    REAL_SAMPLES,
    SAMPLES_DIR,
    ascii_,
    broken_charmera_tiff,
    build_tiff,
    charmera_layout,
    make_jpeg,
    standard_exif_entries,
    standard_ifd0_entries,
    undefined,
    with_exif,
)

from charmfix import cli, jpeg, pipeline
from charmfix.exif_read import read_exif

MTIME = datetime(2026, 4, 2, 8, 30, 0).timestamp()
OVERLAP = "input and output folders must not overlap"
REPORT_INSIDE = "the report must not be written inside the input folder"
FILE_OUTPUT = "the output folder must not be the input file's folder"
FILE_REPORT = "the report must not replace the input file"

needs_permissions = pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="needs POSIX permissions as a non-root user",
)


def other_case(path: Path) -> Path:
    """``path`` with its last part's case swapped: the same folder on a case-insensitive disk."""
    return path.with_name(path.name.swapcase())


def _temp_dir_ignores_case() -> bool:
    with tempfile.TemporaryDirectory(prefix="charmfix-case-") as folder:  # where tmp_path lives
        return other_case(Path(folder)).exists()


case_insensitive_only = pytest.mark.skipif(
    not _temp_dir_ignores_case(), reason="the temp folder's file system is case-sensitive"
)


def snapshot(root: Path) -> dict[str, tuple[str, int]]:
    return {
        p.relative_to(root).as_posix(): (
            hashlib.sha256(p.read_bytes()).hexdigest(),
            p.stat().st_mtime_ns,
        )
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def state(root: Path) -> tuple[dict[str, tuple[str, int]], list[Path]]:
    """File contents plus every path under ``root``: equal before and after → nothing written."""
    return snapshot(root), sorted(root.rglob("*"))


def fill(args: list[str], **paths: Path) -> list[str]:
    """Fill the ``{name}`` placeholders of a parametrized argument list."""
    return [a.format(**paths) for a in args]


@contextmanager
def locked(folder: Path) -> Iterator[Path]:
    """Create ``folder`` with no permissions; restore them afterwards so cleanup works."""
    folder.mkdir()
    folder.chmod(0)
    try:
        yield folder
    finally:
        folder.chmod(0o755)


def single_jpeg(tmp_path: Path) -> Path:
    src = tmp_path / "in" / "x.jpg"
    src.parent.mkdir()
    src.write_bytes(charmera_layout(make_jpeg()))
    return src


@pytest.fixture
def card(tmp_path: Path) -> Path:
    """A fake SD card: real samples, generated defects, a corrupt file and junk."""
    root = tmp_path / "card"
    shutil.copytree(SAMPLES_DIR, root / "DCIM")
    base = make_jpeg()
    gen = root / "DCIM" / "generated"
    gen.mkdir()
    (gen / "PICT0100.JPG").write_bytes(charmera_layout(with_exif(base, broken_charmera_tiff())))
    private = build_tiff(
        [*standard_ifd0_entries("2026:01:01:00:00:00"), ascii_(315, "Secret Artist")],
        [
            *standard_exif_entries("2026:01:01:00:00:00"),
            undefined(0x9286, b"ASCII\x00\x00\x00my diary"),
        ],
    )
    (gen / "PICT0101.jpg").write_bytes(with_exif(base, private))
    (gen / "PICT0102.jpg").write_bytes(b"\xff\xd8\xff\xe0garbage that is not a jpeg")
    (gen / "notes.jpg").write_bytes(b"plain text")
    (gen / "._PICT0100.JPG").write_bytes(b"\x00\x05\x16\x07appledouble")
    (root / ".Trashes").mkdir()
    (root / ".Trashes" / "old.jpg").write_bytes(base)
    for p in root.rglob("*"):
        if p.is_file():
            os.utime(p, (MTIME, MTIME))
    return root


def run(*args: str | Path) -> int:
    return cli.main([str(a) for a in args])


def test_batch_run(card: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out, report_path = tmp_path / "out", tmp_path / "report.json"
    before = snapshot(card)

    assert run(card, "-o", out, "--report", report_path) == 1  # one corrupt file fails
    assert snapshot(card) == before  # source tree untouched

    report = json.loads(report_path.read_text())
    assert report["options"] == pipeline.RunOptions(out).report_options()  # defaults
    by_path = {f["rel_path"]: f for f in report["files"]}
    assert set(by_path) == {
        "DCIM/model_1987/PICT0010.jpg",
        "DCIM/model_2000/PICT0023.jpg",
        "DCIM/generated/PICT0100.JPG",
        "DCIM/generated/PICT0101.jpg",
        "DCIM/generated/PICT0102.jpg",
        "DCIM/generated/notes.jpg",
    }
    assert by_path["DCIM/generated/PICT0102.jpg"]["status"] == "failed"
    assert by_path["DCIM/generated/notes.jpg"]["reason"] == "not JPEG content"
    for name in ("DCIM/model_1987/PICT0010.jpg", "DCIM/model_2000/PICT0023.jpg"):
        assert by_path[name]["status"] == "repaired"
        assert by_path[name]["date_written"] == "2026:04:02 08:30:00"
    gen = by_path["DCIM/generated/PICT0100.JPG"]
    assert gen["status"] == "repaired"
    assert gen["makernote"] == "discarded(invalid)"
    assert gen["date_written"] == "2026:03:03 12:16:29"

    s = report["summary"]
    assert (s["discovered"], s["repaired"], s["skipped"], s["failed"]) == (6, 4, 1, 1)
    assert s["timestamps_fixed"] == 4 and s["makernotes_discarded"] == 1

    # privacy: no absolute paths, no free-text tag content
    text = report_path.read_text()
    assert str(tmp_path) not in text and str(Path.home()) not in text
    assert "Secret Artist" not in text and "my diary" not in text
    assert by_path["DCIM/generated/PICT0101.jpg"]["private_text"] == {
        "Artist": "kept",
        "UserComment": "kept",
    }

    # outputs mirror the folder structure and keep the source mtime
    written = out / "DCIM/model_1987/PICT0010.jpg"
    assert written.exists() and (out / "DCIM/model_2000/PICT0023.jpg").exists()
    assert written.stat().st_mtime == MTIME
    assert not (out / ".Trashes").exists()
    assert not list(out.rglob("._*"))
    src_jf = jpeg.parse((card / "DCIM/model_1987/PICT0010.jpg").read_bytes())
    assert jpeg.image_data(jpeg.parse(written.read_bytes())) == jpeg.image_data(src_jf)
    assert "repaired" in capsys.readouterr().out

    # second run: nothing is rewritten, no _1 duplicates
    out_before = snapshot(out)
    assert run(card, "-o", out, "--report", report_path) == 1
    second = json.loads(report_path.read_text())["summary"]
    assert second["skipped_reasons"] == {"exists": 4, "not JPEG content": 1}
    assert snapshot(out) == out_before

    # running on the output: everything is already valid and copied unchanged
    out2 = tmp_path / "out2"
    assert run(out, "-o", out2, "--report", report_path) == 0
    third = json.loads(report_path.read_text())["summary"]
    assert third["already_valid"] == 4 and third["repaired"] == 0
    for rel, (digest, _) in snapshot(out).items():
        assert snapshot(out2)[rel][0] == digest


def test_force_rewrites(card: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    run(card, "-o", out)
    target = out / "DCIM/model_1987/PICT0010.jpg"
    target.write_bytes(b"stale")
    run(card, "-o", out, "--force")
    assert target.read_bytes() != b"stale"


@pytest.mark.parametrize("force", [["--force"], []], ids=["force", "no-force"])
def test_symlinked_output_folder_never_reaches_source(
    card: Path, tmp_path: Path, force: list[str]
) -> None:
    out, report = tmp_path / "out", tmp_path / "r.json"
    out.mkdir()
    (out / "DCIM").symlink_to(card / "DCIM")
    before = snapshot(card)
    assert run(card, "-o", out, *force, "--report", report) == 1
    assert snapshot(card) == before
    files = json.loads(report.read_text())["files"]
    repaired = [f for f in files if f["rel_path"] == "DCIM/model_1987/PICT0010.jpg"]
    assert repaired[0]["status"] == "failed"
    assert "outside the output folder" in repaired[0]["error"]


def test_report_replaces_a_hard_link_instead_of_writing_through(card: Path, tmp_path: Path) -> None:
    source = card / "DCIM/model_1987/PICT0010.jpg"
    report = tmp_path / "r.json"
    os.link(source, report)  # the report path is another name for a source file
    before = snapshot(card)
    run(card, "-o", tmp_path / "out", "--report", report)
    assert snapshot(card) == before
    assert json.loads(report.read_text())["tool"] == "charmfix"


def test_unwritable_report_is_fatal(
    card: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(*_: object) -> None:
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(cli, "write_report", refuse)
    assert run(card, "-o", tmp_path / "out", "--report", tmp_path / "r.json") == 2
    assert "cannot write the report: Permission denied" in capsys.readouterr().err


def test_dry_run_writes_nothing(card: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert run(card, "-o", out, "--dry-run") == 1
    assert not out.exists()


def test_flatten_and_no_recursion(card: Path, tmp_path: Path) -> None:
    out = tmp_path / "flat"
    run(card / "DCIM", "-o", out, "--no-preserve-structure")
    names = sorted(p.name for p in out.iterdir())
    assert names == ["PICT0010.jpg", "PICT0023.jpg", "PICT0100.JPG", "PICT0101.jpg"]
    out2 = tmp_path / "flat2"
    run(card / "DCIM", "-o", out2, "--no-recursive")
    assert not out2.exists()  # no JPEGs directly in DCIM


# Non-flag options cleared for the report by CLI.md's privacy rules (Make and Model are allowed).
REPORT_TEXT_OPTIONS = {"make", "model"}


def test_report_options_are_flags_or_cleared_text() -> None:
    """A path (or any non-flag) field must never reach the report without a privacy decision."""
    options = pipeline.RunOptions(Path("/abs/out")).report_options()
    text = {k for k, v in options.items() if type(v) is not bool}
    assert options and text == REPORT_TEXT_OPTIONS
    assert all(type(options[k]) is str for k in text)


def test_single_file_input(tmp_path: Path) -> None:
    src = single_jpeg(tmp_path)
    assert run(src, "-o", tmp_path / "out") == 0
    assert (tmp_path / "out" / "x.jpg").exists()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs named pipes")
def test_non_regular_input_is_skipped(tmp_path: Path) -> None:
    pipe, report = tmp_path / "pipe.jpg", tmp_path / "r.json"
    os.mkfifo(pipe)
    assert run(pipe, "-o", tmp_path / "out", "--report", report) == 0
    (entry,) = json.loads(report.read_text())["files"]
    assert (entry["rel_path"], entry["status"]) == ("pipe.jpg", "skipped")
    assert entry["reason"] == "not a regular file"


def test_no_assume_charmera_flag(card: Path, tmp_path: Path) -> None:
    report = tmp_path / "r.json"
    run(
        card / "DCIM" / "model_1987",
        "-o",
        tmp_path / "o",
        "--no-assume-charmera",
        "--report",
        report,
    )
    (entry,) = json.loads(report.read_text())["files"]
    assert entry["identity"] == "generalplus-unconfirmed"


def test_custom_make_and_model(tmp_path: Path) -> None:
    src, out, report = single_jpeg(tmp_path), tmp_path / "out", tmp_path / "r.json"
    assert run(src, "-o", out, "--make", "Acme", "--model", "Snap 1", "--report", report) == 0
    tiff = jpeg.parse((out / "x.jpg").read_bytes()).exif_tiff()
    assert tiff is not None
    exif = read_exif(tiff)
    written = [exif.get("0th", tag) for tag in (0x010F, 0x0110)]
    assert [rv and rv.value for rv in written] == [b"Acme\x00", b"Snap 1\x00"]
    options = json.loads(report.read_text())["options"]
    assert (options["make"], options["model"]) == ("Acme", "Snap 1")


@pytest.mark.parametrize("value", ["A", "Snap 1", "A" * cli.MAX_CAMERA_TEXT])
def test_camera_text_accepted(value: str) -> None:
    assert cli._camera_text(value) == value


@pytest.mark.parametrize(
    ("value", "message"),
    [
        pytest.param("", "1-64 characters", id="empty"),
        pytest.param("A" * (cli.MAX_CAMERA_TEXT + 1), "1-64 characters", id="too-long"),
        pytest.param(" Acme", "printable ASCII", id="leading-space"),
        pytest.param("Acme ", "printable ASCII", id="trailing-space"),
        pytest.param("Ačme", "printable ASCII", id="non-ascii"),
        pytest.param("Ac\tme", "printable ASCII", id="control"),
    ],
)
def test_camera_text_rejected(value: str, message: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError, match=message):
        cli._camera_text(value)


@pytest.mark.parametrize("option", ["--make", "--model"])
def test_camera_option_rejection_is_a_usage_error(
    tmp_path: Path, option: str, capsys: pytest.CaptureFixture[str]
) -> None:
    src, out = single_jpeg(tmp_path), tmp_path / "out"
    with pytest.raises(SystemExit) as exc:
        run(src, "-o", out, option, " Acme")
    assert exc.value.code == cli.EXIT_FATAL
    err = capsys.readouterr().err
    assert f"argument {option}" in err and "printable ASCII" in err
    assert not out.exists()


@pytest.mark.parametrize(
    ("args", "message"),
    [
        pytest.param(["-o", "{card}", "--force"], OVERLAP, id="output-same"),
        pytest.param(["-o", "{card}/sub/out"], OVERLAP, id="output-inside"),
        pytest.param(["-o", "{tmp}"], OVERLAP, id="output-parent"),
        pytest.param(
            ["-o", "{card_alias}/sub/out"],
            OVERLAP,
            id="output-inside-other-case",
            marks=case_insensitive_only,
        ),
        pytest.param(
            ["-o", "{tmp_alias}"],
            OVERLAP,
            id="output-parent-other-case",
            marks=case_insensitive_only,
        ),
        pytest.param(
            ["-o", "{tmp}/out", "--report", "{card}/r.json"], REPORT_INSIDE, id="report-inside"
        ),
        pytest.param(
            ["-o", "{tmp}/out", "--report", "{card_alias}/r.json"],
            REPORT_INSIDE,
            id="report-inside-other-case",
            marks=case_insensitive_only,
        ),
    ],
)
def test_folder_refusals(
    card: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], args: list[str], message: str
) -> None:
    args = fill(
        args, card=card, card_alias=other_case(card), tmp=tmp_path, tmp_alias=other_case(tmp_path)
    )
    before = state(tmp_path)
    assert run(card, *args) == 2
    assert message in capsys.readouterr().err
    assert state(tmp_path) == before


@pytest.mark.parametrize(
    ("args", "message"),
    [
        pytest.param(["-o", "{folder}", "--force"], FILE_OUTPUT, id="output-same"),
        pytest.param(
            ["-o", "{folder_alias}", "--force"],
            FILE_OUTPUT,
            id="output-same-other-case",
            marks=case_insensitive_only,
        ),
        pytest.param(["-o", "{tmp}/out", "--report", "{src}"], FILE_REPORT, id="report-is-source"),
        pytest.param(
            ["-o", "{tmp}/out", "--report", "{src_alias}"],
            FILE_REPORT,
            id="report-is-source-other-case",
            marks=case_insensitive_only,
        ),
    ],
)
def test_single_file_refusals(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], args: list[str], message: str
) -> None:
    src = single_jpeg(tmp_path)
    args = fill(
        args,
        src=src,
        src_alias=other_case(src),
        folder=src.parent,
        folder_alias=other_case(src.parent),
        tmp=tmp_path,
    )
    before = state(tmp_path)
    assert run(src, *args) == 2
    assert message in capsys.readouterr().err
    assert state(tmp_path) == before


def test_overlap_refused_when_identity_check_fails(
    card: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def unreadable(self: Path, other: Path) -> bool:
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(Path, "samefile", unreadable)
    before = snapshot(card)
    assert run(card, "-o", card, "--force") == 2
    assert OVERLAP in capsys.readouterr().err
    assert snapshot(card) == before


@pytest.mark.parametrize(
    ("args", "output"),
    [
        (["-o", "{folder}/fixed"], "{folder}/fixed/x.jpg"),
        (["-o", "{tmp}"], "{tmp}/x.jpg"),
        (["-o", "{tmp}/out", "--report", "{folder}/r.json"], "{tmp}/out/x.jpg"),
    ],
    ids=["output-inside", "output-parent", "report-next-to-file"],
)
def test_single_file_may_write_near_source(tmp_path: Path, args: list[str], output: str) -> None:
    src = single_jpeg(tmp_path)
    paths = {"src": src, "folder": src.parent, "tmp": tmp_path}
    source = src.read_bytes()
    assert run(src, *fill(args, **paths)) == 0
    assert Path(output.format(**paths)).exists()
    assert src.read_bytes() == source


def test_missing_input(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(tmp_path / "nope", "-o", tmp_path / "out") == 2
    assert "input not found" in capsys.readouterr().err


def test_disk_full_stops_batch(
    card: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def full(fd: int) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "fsync", full)  # the real write path fails, as on a full disk
    out = tmp_path / "out"
    assert run(card, "-o", out) == 2
    stdout = capsys.readouterr().out
    assert "STOPPED EARLY: output volume is full" in stdout
    assert "repaired              0" in stdout
    assert not list(out.rglob(".charmfix-*.tmp"))


@pytest.mark.parametrize("step", ["prepare", "process_file"])  # discovery, then processing
def test_interrupt_exits_130(
    card: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    step: str,
) -> None:
    def interrupt(*_: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, step, interrupt)
    report = tmp_path / "r.json"
    assert run(card, "-o", tmp_path / "out", "--report", report) == 130
    assert "STOPPED EARLY: interrupted" in capsys.readouterr().out
    assert json.loads(report.read_text())["summary"]["stopped"] == "interrupted"


def test_samples_are_present() -> None:
    assert len(REAL_SAMPLES) >= 2


@needs_permissions
def test_unreadable_input_root_is_fatal(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with locked(tmp_path / "card") as card:
        assert run(card, "-o", tmp_path / "out") == 2
    assert "cannot read" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    ("platform", "reason", "hint"),
    [
        ("darwin", "Operation not permitted", True),
        ("linux", "Operation not permitted", False),
        ("darwin", "Permission denied", False),
    ],
)
def test_macos_permission_hint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    platform: str,
    reason: str,
    hint: bool,
) -> None:
    card = tmp_path / "card"
    card.mkdir()
    monkeypatch.setattr(cli, "unreadable_reason", lambda folder: reason)
    monkeypatch.setattr(sys, "platform", platform)
    assert run(card, "-o", tmp_path / "out") == 2
    err = capsys.readouterr().err
    assert f"cannot read {card}: {reason}" in err
    assert ("Removable Volumes" in err) is hint
    assert not (tmp_path / "out").exists()


@needs_permissions
def test_unreadable_subfolder_fails_only_that_folder(card: Path, tmp_path: Path) -> None:
    report = tmp_path / "r.json"
    with locked(card / "DCIM" / "locked"):
        assert run(card, "-o", tmp_path / "out", "--report", report) == 1
    by_path = {f["rel_path"]: f for f in json.loads(report.read_text())["files"]}
    assert by_path["DCIM/locked"]["status"] == "failed"
    assert by_path["DCIM/locked"]["error"].startswith("cannot list folder")
    assert by_path["DCIM/model_1987/PICT0010.jpg"]["status"] == "repaired"


def test_empty_folder_warns(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert run(empty, "-o", tmp_path / "out") == 0
    assert "no .jpg/.jpeg files found" in capsys.readouterr().err
