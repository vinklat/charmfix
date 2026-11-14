from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import fields
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
from charmfix.repair import RepairOptions

MTIME = datetime(2026, 4, 2, 8, 30, 0).timestamp()
PREFIX = "20260402-083000_"  # MTIME as an output name prefix
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
    os.utime(src, (MTIME, MTIME))  # no EXIF: the output is named by this time
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


def test_batch_run(
    card: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    out, report_path = tmp_path / "out", tmp_path / "report.json"
    before = snapshot(card)

    assert run(card, "-o", out, "--report", report_path) == 1  # one corrupt file fails
    assert snapshot(card) == before  # source tree untouched

    report = json.loads(report_path.read_text())
    assert report["options"] == pipeline.RunOptions(out).report_options()  # defaults
    assert (report["options"]["make"], report["options"]["model"]) == ("Kodak", "Charmera")
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
    assert by_path["DCIM/generated/PICT0102.jpg"]["output"] is None  # failed before naming
    assert by_path["DCIM/generated/notes.jpg"]["reason"] == "not JPEG content"
    assert by_path["DCIM/generated/notes.jpg"]["output"] is None  # skipped before naming
    for name in ("DCIM/model_1987/PICT0010.jpg", "DCIM/model_2000/PICT0023.jpg"):
        assert by_path[name]["status"] == "repaired"
        assert by_path[name]["date_written"] == "2026:04:02 08:30:00"
    gen = by_path["DCIM/generated/PICT0100.JPG"]
    assert gen["status"] == "repaired"
    assert gen["output"] == "DCIM/generated/20260303-121629_PICT0100.JPG"  # the EXIF date
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

    # outputs mirror the folder structure, start with the capture time and keep the source mtime
    written = out / f"DCIM/model_1987/{PREFIX}PICT0010.jpg"
    assert written.exists() and (out / f"DCIM/model_2000/{PREFIX}PICT0023.jpg").exists()
    assert written.stat().st_mtime == MTIME
    assert not (out / ".Trashes").exists()
    assert not list(out.rglob("._*"))
    src_jf = jpeg.parse((card / "DCIM/model_1987/PICT0010.jpg").read_bytes())
    assert jpeg.image_data(jpeg.parse(written.read_bytes())) == jpeg.image_data(src_jf)
    assert "repaired" in capsys.readouterr().out

    # second run: nothing is built or rewritten, no _1 duplicates
    def build_called(*_: object) -> bytes:
        raise AssertionError("an existing output was built again")

    out_before = snapshot(out)
    with monkeypatch.context() as m:
        m.setattr(pipeline, "build", build_called)
        assert run(card, "-o", out, "--report", report_path) == 1
    second = json.loads(report_path.read_text())
    assert second["summary"]["skipped_reasons"] == {"exists": 4, "not JPEG content": 1}
    exists = {f["rel_path"]: f["output"] for f in second["files"] if f["reason"] == "exists"}
    assert exists["DCIM/model_1987/PICT0010.jpg"] == f"DCIM/model_1987/{PREFIX}PICT0010.jpg"
    assert snapshot(out) == out_before

    # running on the output: everything is already valid and copied unchanged, under its name
    out2 = tmp_path / "out2"
    assert run(out, "-o", out2, "--report", report_path) == 0
    third = json.loads(report_path.read_text())["summary"]
    assert third["already_valid"] == 4 and third["repaired"] == 0
    for rel, (digest, _) in snapshot(out).items():
        assert snapshot(out2)[rel][0] == digest


def test_force_rewrites(card: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    run(card, "-o", out)
    target = out / f"DCIM/model_1987/{PREFIX}PICT0010.jpg"
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


def test_dry_run_into_filled_output(
    card: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out, report = tmp_path / "out", tmp_path / "r.json"
    run(card, "-o", out, "--report", report)
    written = {f["rel_path"]: f["output"] for f in json.loads(report.read_text())["files"]}
    before = state(out)

    def build_called(*_: object) -> bytes:
        raise AssertionError("an existing output was built again")

    monkeypatch.setattr(pipeline, "build", build_called)
    assert run(card, "-o", out, "--dry-run", "--report", report) == 1
    second = json.loads(report.read_text())
    assert second["summary"]["skipped_reasons"] == {"exists": 4, "not JPEG content": 1}
    exists = {f["rel_path"]: f["output"] for f in second["files"] if f["reason"] == "exists"}
    assert len(exists) == 4 and all(written[rel] == output for rel, output in exists.items())
    assert state(out) == before


def test_flatten_and_no_recursion(card: Path, tmp_path: Path) -> None:
    out = tmp_path / "flat"
    run(card / "DCIM", "-o", out, "--no-preserve-structure")
    names = sorted(p.name for p in out.iterdir())
    assert names == [
        "20260101-000000_PICT0101.jpg",
        "20260303-121629_PICT0100.JPG",
        f"{PREFIX}PICT0010.jpg",
        f"{PREFIX}PICT0023.jpg",
    ]
    out2 = tmp_path / "flat2"
    run(card / "DCIM", "-o", out2, "--no-recursive")
    assert not out2.exists()  # no JPEGs directly in DCIM


# Non-flag options cleared for the report → CLI.md#report (privacy rules).
REPORT_TEXT_OPTIONS = {"make", "model"}


def test_report_options_are_flags_or_cleared_text() -> None:
    """A path (or any non-flag) field must never reach the report without a privacy decision.

    New keys of any type also fail test_docs.py::test_report_keys_match_code until CLI.md lists
    them; this test guards their type.
    """
    options = pipeline.RunOptions(Path("/abs/out")).report_options()
    text = {k for k, v in options.items() if type(v) is not bool}
    assert options and text == REPORT_TEXT_OPTIONS
    assert all(type(options[k]) is str for k in text)


def test_report_option_names_do_not_collide() -> None:
    """report_options() merges two dataclasses; a shared name would drop one value silently."""
    run = {f.name for f in fields(pipeline.RunOptions)}
    assert not run & {f.name for f in fields(RepairOptions)}


def test_single_file_input(tmp_path: Path) -> None:
    src = single_jpeg(tmp_path)
    assert run(src, "-o", tmp_path / "out") == 0
    assert (tmp_path / "out" / f"{PREFIX}x.jpg").exists()


def reused_card(root: Path, session: int, capture_time: datetime) -> Path:
    """A card emptied and reused: each session restarts the camera's file numbers."""
    src = root / f"session{session}" / "DCIM" / "PICT0001.jpg"
    src.parent.mkdir(parents=True)
    src.write_bytes(charmera_layout(make_jpeg()))
    os.utime(src, (capture_time.timestamp(), capture_time.timestamp()))
    return src.parents[1]


def test_date_prefix_keeps_reused_card_numbers_apart(tmp_path: Path) -> None:
    out, report = tmp_path / "out", tmp_path / "r.json"
    first = reused_card(tmp_path, 1, datetime(2026, 4, 2, 8, 30, 0))
    second = reused_card(tmp_path, 2, datetime(2026, 5, 9, 17, 5, 42))
    assert run(first, "-o", out) == 0
    assert run(second, "-o", out, "--report", report) == 0
    assert json.loads(report.read_text())["summary"]["repaired"] == 1  # not skipped as `exists`
    assert sorted(p.name for p in (out / "DCIM").iterdir()) == [
        f"{PREFIX}PICT0001.jpg",
        "20260509-170542_PICT0001.jpg",
    ]


def test_no_date_prefix_keeps_source_names(tmp_path: Path) -> None:
    out, report = tmp_path / "out", tmp_path / "r.json"
    for session, day in ((1, 2), (2, 9)):
        reused_card(tmp_path / "cards", session, datetime(2026, 4, day, 8, 30, 0))
    args: list[str | Path] = ["-o", out, "--no-date-prefix", "--no-preserve-structure"]
    args += ["--report", report]
    assert run(tmp_path / "cards", *args) == 0
    assert sorted(p.name for p in out.iterdir()) == ["PICT0001.jpg", "PICT0001_1.jpg"]
    assert json.loads(report.read_text())["options"]["date_prefix"] is False


def summary_of(report: Path) -> dict[str, object]:
    summary: dict[str, object] = json.loads(report.read_text())["summary"]
    return summary


def test_flatten_rerun_keeps_numbered_names(tmp_path: Path) -> None:
    out, report = tmp_path / "out", tmp_path / "r.json"
    for session in (1, 2):  # the same name and capture time on two cards
        reused_card(tmp_path / "cards", session, datetime(2026, 4, 2, 8, 30, 0))
    args: list[str | Path] = [tmp_path / "cards", "-o", out, "--no-preserve-structure"]
    args += ["--report", report]
    assert run(*args) == 0
    names = [f"{PREFIX}PICT0001.jpg", f"{PREFIX}PICT0001_1.jpg"]
    assert sorted(p.name for p in out.iterdir()) == names
    assert run(*args) == 0
    assert summary_of(report)["skipped_reasons"] == {"exists": 2}
    assert sorted(p.name for p in out.iterdir()) == names


def test_other_prefix_setting_writes_a_new_copy(tmp_path: Path) -> None:
    src, out, report = single_jpeg(tmp_path), tmp_path / "out", tmp_path / "r.json"
    os.utime(src, (MTIME, MTIME))
    assert run(src, "-o", out, "--no-date-prefix") == 0
    assert run(src, "-o", out, "--report", report) == 0
    assert summary_of(report)["repaired"] == 1  # not matched as `exists`
    assert sorted(p.name for p in out.iterdir()) == [f"{PREFIX}x.jpg", "x.jpg"]


def test_changed_mtime_writes_a_new_copy(tmp_path: Path) -> None:
    out, report = tmp_path / "out", tmp_path / "r.json"
    card = reused_card(tmp_path, 1, datetime(2026, 4, 2, 8, 30, 0))  # no EXIF date: rule 1a
    assert run(card, "-o", out) == 0
    later = datetime(2026, 11, 14, 10, 0, 0).timestamp()
    os.utime(card / "DCIM" / "PICT0001.jpg", (later, later))  # e.g. copied again without -p
    assert run(card, "-o", out, "--report", report) == 0
    assert summary_of(report)["repaired"] == 1
    assert sorted(p.name for p in (out / "DCIM").iterdir()) == [
        f"{PREFIX}PICT0001.jpg",
        "20261114-100000_PICT0001.jpg",
    ]


def test_failed_file_shifts_clash_numbers_on_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The documented limit of CLI.md "Clash numbers on a re-run"."""
    out, report = tmp_path / "out", tmp_path / "r.json"
    for session in (1, 2):
        reused_card(tmp_path / "cards", session, datetime(2026, 4, 2, 8, 30, 0))
    args: list[str | Path] = [tmp_path / "cards", "-o", out, "--no-preserve-structure"]
    args += ["--report", report]

    def flaky(path: Path, *rest: object, **kw: object) -> object:
        if "session1" in Path(path).parts:  # a read error that goes away
            raise OSError(errno.EIO, "Input/output error")
        return open(path, *rest, **kw)  # type: ignore[call-overload]

    with monkeypatch.context() as m:
        m.setattr(pipeline, "open", flaky, raising=False)
        assert run(*args) == 1
    assert [p.name for p in out.iterdir()] == [f"{PREFIX}PICT0001.jpg"]  # session 2's image

    assert run(*args) == 0
    files = {f["rel_path"]: f for f in json.loads(report.read_text())["files"]}
    first, second = files["session1/DCIM/PICT0001.jpg"], files["session2/DCIM/PICT0001.jpg"]
    assert (first["status"], first["reason"]) == ("skipped", "exists")
    assert first["output"] == f"{PREFIX}PICT0001.jpg"
    assert second["output"] == f"{PREFIX}PICT0001_1.jpg"  # a second copy of session 2


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
    tiff = jpeg.parse((out / f"{PREFIX}x.jpg").read_bytes()).exif_tiff()
    assert tiff is not None
    exif = read_exif(tiff)
    written = [exif.get("0th", tag) for tag in (0x010F, 0x0110)]
    assert [rv and rv.value for rv in written] == [b"Acme\x00", b"Snap 1\x00"]
    options = json.loads(report.read_text())["options"]
    assert (options["make"], options["model"]) == ("Acme", "Snap 1")


@pytest.mark.parametrize("option", ["--make", "--model"])
def test_camera_option_rejection_is_a_usage_error(
    tmp_path: Path, option: str, capsys: pytest.CaptureFixture[str]
) -> None:
    src, out = single_jpeg(tmp_path), tmp_path / "out"
    with pytest.raises(SystemExit) as exc:
        run(src, "-o", out, option, " Acme")
    assert exc.value.code == cli.EXIT_FATAL
    err = capsys.readouterr().err
    assert f"argument {option}: must be printable ASCII" in err  # field named once
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
        (["-o", "{folder}/fixed"], "{folder}/fixed/{prefix}x.jpg"),
        (["-o", "{tmp}"], "{tmp}/{prefix}x.jpg"),
        (["-o", "{tmp}/out", "--report", "{folder}/r.json"], "{tmp}/out/{prefix}x.jpg"),
    ],
    ids=["output-inside", "output-parent", "report-next-to-file"],
)
def test_single_file_may_write_near_source(tmp_path: Path, args: list[str], output: str) -> None:
    src = single_jpeg(tmp_path)
    paths = {"src": src, "folder": src.parent, "tmp": tmp_path}
    source = src.read_bytes()
    assert run(src, *fill(args, **paths)) == 0
    assert Path(output.format(**paths, prefix=PREFIX)).exists()
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


@pytest.mark.parametrize("step", ["discover", "process_file"])  # discovery, then processing
def test_interrupt_exits_130(
    card: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    step: str,
) -> None:
    def interrupt(*_: object, **__: object) -> None:
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
