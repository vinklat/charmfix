from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

import pytest

from charmfix.output import OutputExistsError, OutsideOutputError, assign_outputs, write_atomic

TIMES = (1_700_000_000_000_000_000, 1_600_000_000_123_456_789)


def tmp_files(folder: Path) -> list[Path]:
    return list(folder.glob(".charmfix-*.tmp"))


def test_assign_mirrors_structure(tmp_path: Path) -> None:
    rels = [Path("a/x.jpg"), Path("b/x.jpg")]
    assert assign_outputs(rels, tmp_path, True) == [tmp_path / "a/x.jpg", tmp_path / "b/x.jpg"]


def test_assign_flatten_dedupes(tmp_path: Path) -> None:
    rels = [Path("a/x.jpg"), Path("b/x.jpg"), Path("c/X.JPG")]
    assert assign_outputs(rels, tmp_path, False) == [
        tmp_path / "x.jpg",
        tmp_path / "x_1.jpg",
        tmp_path / "X_2.JPG",
    ]


def test_assign_ignores_existing_files(tmp_path: Path) -> None:
    (tmp_path / "x.jpg").write_bytes(b"old")
    assert assign_outputs([Path("x.jpg")], tmp_path, True) == [tmp_path / "x.jpg"]


def test_write_creates_file_with_times(tmp_path: Path) -> None:
    dest = tmp_path / "sub" / "x.jpg"
    assert write_atomic(dest, b"data", force=False, root=tmp_path, times_ns=TIMES) == []
    assert dest.read_bytes() == b"data"
    assert dest.stat().st_mtime_ns == TIMES[1]
    umask = os.umask(0)
    os.umask(umask)
    assert stat.S_IMODE(dest.stat().st_mode) == 0o666 & ~umask  # not mkstemp's 0600
    assert tmp_files(dest.parent) == []


def test_read_back_mismatch_publishes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "read_bytes", lambda self: b"corrupted")
    dest = tmp_path / "x.jpg"
    with pytest.raises(OSError) as raised:
        write_atomic(dest, b"data", force=False, root=tmp_path, times_ns=TIMES)
    assert raised.value.errno == errno.EIO
    assert not dest.exists()
    assert tmp_files(tmp_path) == []


def test_no_overwrite_without_force(tmp_path: Path) -> None:
    dest = tmp_path / "x.jpg"
    dest.write_bytes(b"old")
    with pytest.raises(OutputExistsError):
        write_atomic(dest, b"new", force=False, root=tmp_path, times_ns=TIMES)
    assert dest.read_bytes() == b"old"
    assert tmp_files(tmp_path) == []


def test_race_lost_to_other_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dest = tmp_path / "x.jpg"
    real_link = os.link

    def link(src: str, dst: str) -> None:
        Path(dst).write_bytes(b"theirs")
        real_link(src, dst)

    monkeypatch.setattr(os, "link", link)
    with pytest.raises(OutputExistsError):
        write_atomic(dest, b"mine", force=False, root=tmp_path, times_ns=TIMES)
    assert dest.read_bytes() == b"theirs"
    assert tmp_files(tmp_path) == []


def test_force_replaces(tmp_path: Path) -> None:
    dest = tmp_path / "x.jpg"
    dest.write_bytes(b"old")
    write_atomic(dest, b"new", force=True, root=tmp_path, times_ns=TIMES)
    assert dest.read_bytes() == b"new"


def test_symlinked_folder_out_of_root_is_refused(tmp_path: Path) -> None:
    elsewhere, root = tmp_path / "elsewhere", tmp_path / "out"
    (elsewhere / "sub").mkdir(parents=True)
    (elsewhere / "sub" / "x.jpg").write_bytes(b"source")
    root.mkdir()
    (root / "DCIM").symlink_to(elsewhere)
    with pytest.raises(OutsideOutputError):
        write_atomic(root / "DCIM/sub/x.jpg", b"new", force=True, root=root, times_ns=TIMES)
    with pytest.raises(OutsideOutputError):  # not even a new folder is created there
        write_atomic(root / "DCIM/new/x.jpg", b"new", force=True, root=root, times_ns=TIMES)
    assert (elsewhere / "sub" / "x.jpg").read_bytes() == b"source"
    assert not (elsewhere / "new").exists()


def test_symlinked_folder_inside_root_is_allowed(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    write_atomic(tmp_path / "link/x.jpg", b"data", force=False, root=tmp_path, times_ns=TIMES)
    assert (tmp_path / "real/x.jpg").read_bytes() == b"data"


@pytest.mark.parametrize("err", [errno.EPERM, errno.EXDEV, errno.ENOTSUP])
def test_fallback_without_hard_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, err: int
) -> None:
    def no_link(src: str, dst: str) -> None:
        raise OSError(err, "no links here")

    monkeypatch.setattr(os, "link", no_link)
    dest = tmp_path / "x.jpg"
    write_atomic(dest, b"data", force=False, root=tmp_path, times_ns=TIMES)
    assert dest.read_bytes() == b"data"
    assert tmp_files(tmp_path) == []


def test_temp_cleaned_on_interrupt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(src: str, dst: str) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(KeyboardInterrupt):
        write_atomic(tmp_path / "x.jpg", b"data", force=True, root=tmp_path, times_ns=TIMES)
    assert list(tmp_path.iterdir()) == []


def test_utime_failure_is_a_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_utime(*args: object, **kwargs: object) -> None:
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "utime", no_utime)
    warnings = write_atomic(tmp_path / "x.jpg", b"data", force=False, root=tmp_path, times_ns=TIMES)
    assert warnings and "file times" in warnings[0]
