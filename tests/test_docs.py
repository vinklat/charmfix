"""Doc links resolve, contract preambles match, and contract tables match the code."""

from __future__ import annotations

import dataclasses
import itertools
import os
import re
import typing
from pathlib import Path

import pytest

from charmfix import cli
from charmfix.pipeline import RunOptions
from charmfix.report import FileResult, FileStatus, Summary, build_report

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = ("REPAIRS.md", "CLI.md", "DEVELOPMENT.md", "TESTING.md")
DOCS = sorted([*ROOT.glob("*.md"), *ROOT.glob("docs/**/*.md")])

FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
HEADING_RE = re.compile(r"^#+\s+(.*?)\s*$")
LINK_RE = re.compile(r"\]\(([^)#\s]*)(?:#([^)\s]+))?\)")
ROUTING = "Routing → [AGENTS.md](AGENTS.md#canonical-sources)."
REPORT_OPTIONS = list(RunOptions(Path()).report_options())


def prose_lines(text: str) -> list[str]:
    """Lines outside fenced code blocks."""
    lines, fenced = [], False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            fenced = not fenced
        elif not fenced:
            lines.append(line)
    return lines


def slug(heading: str) -> str:
    """GitHub-style heading anchor."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def anchors(path: Path) -> set[str]:
    return {slug(m[1]) for line in prose_lines(path.read_text()) if (m := HEADING_RE.match(line))}


def section(path: Path, heading: str) -> list[str]:
    """Prose lines under ``## heading`` up to the next ``##`` heading."""
    out, inside = [], False
    for line in prose_lines(path.read_text()):
        if line.startswith("## "):
            inside = line[3:].strip() == heading
        elif inside:
            out.append(line)
    assert out, f"{path.name} has no '## {heading}' section"
    return out


def tables(lines: list[str]) -> dict[str, list[list[str]]]:
    """Each table in ``lines``, keyed by its first header cell: body rows as stripped cells."""
    found = {}
    for is_table, group in itertools.groupby(lines, key=lambda line: line.startswith("|")):
        if is_table:
            header, _separator, *body = (
                [c.strip() for c in row.strip().strip("|").split("|")] for row in group
            )
            found[header[0]] = body
    return found


def table(lines: list[str], header: str) -> list[list[str]]:
    found = tables(lines)
    assert header in found, f"no table with header {header!r}; found {sorted(found)}"
    return found[header]


def first_cells(lines: list[str], header: str) -> list[str]:
    """First cell of each body row of the table whose first header cell is ``header``."""
    return [row[0] for row in table(lines, header)]


def cell(lines: list[str], header: str, key: str) -> str:
    """Second cell of the row ``key`` in the table whose first header cell is ``header``."""
    matches = [row[1] for row in table(lines, header) if row[0] == key]
    assert len(matches) == 1, f"expected one {header} row {key!r}, found {len(matches)}"
    return matches[0]


def code_spans(text: str) -> list[str]:
    return re.findall(r"`([^`]+)`", text)


def exists_exactly(path: Path) -> bool:
    """``path`` exists under ROOT in this exact case (macOS ignores case; GitHub does not)."""
    if not path.is_relative_to(ROOT):
        return False
    here = ROOT
    for part in path.relative_to(ROOT).parts:
        if not here.is_dir() or part not in os.listdir(here):
            return False
        here /= part
    return True


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_links_and_anchors_resolve(doc: Path) -> None:
    problems = []
    for line in prose_lines(doc.read_text()):
        for target, anchor in LINK_RE.findall(re.sub(r"`[^`]*`", "", line)):  # skip code spans
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            path = (doc.parent / target).resolve() if target else doc
            if not exists_exactly(path):
                problems.append(f"missing target {target}")
            elif anchor and path.suffix == ".md" and anchor not in anchors(path):
                problems.append(f"missing anchor {target}#{anchor}")
    assert not problems, problems


@pytest.mark.parametrize("name", CONTRACTS)
def test_contract_preamble(name: str) -> None:
    owns = {
        re.sub(r"\[([^\]]+)\].*", r"\1", row[0]): row[1]
        for row in table(section(ROOT / "AGENTS.md", "Canonical sources"), "File")
    }
    domain = owns[name][0].lower() + owns[name][1:]
    lines = (ROOT / name).read_text().splitlines()
    assert lines[0].startswith("# ") and lines[1] == "", f"{name} must open with its title"
    paragraph = " ".join(lines[2 : lines.index("", 2)]) if "" in lines[2:] else ""
    expected = f"Owns: {domain}. {ROUTING}"
    assert paragraph == expected, f"{name}: the paragraph after the title must be {expected!r}"


def test_agents_lists_every_contract() -> None:
    listed = {
        re.sub(r"\[([^\]]+)\].*", r"\1", c)
        for c in first_cells(section(ROOT / "AGENTS.md", "Canonical sources"), "File")
    }
    assert listed == set(CONTRACTS)


def test_cli_options_match_parser() -> None:
    documented = {
        flag
        for c in first_cells(section(ROOT / "CLI.md", "Options"), "Option")
        for flag in re.findall(r"`(--[a-z-]+)`", c)
    }
    actual = {
        opt
        for action in cli._parser()._actions
        for opt in action.option_strings
        if opt.startswith("--") and opt != "--help"
    }
    assert documented == actual


def test_statuses_match_code() -> None:
    documented = {
        c.strip("`") for c in first_cells(section(ROOT / "REPAIRS.md", "Status"), "Status")
    }
    assert documented == set(typing.get_args(FileStatus))


def test_report_keys_match_code() -> None:
    report = section(ROOT / "CLI.md", "Report")
    keys = [k.removesuffix("[]") for c in first_cells(report, "Key") for k in code_spans(c)]
    assert keys == list(build_report([], Summary(), {}))
    assert code_spans(cell(report, "Key", "`options`")) == REPORT_OPTIONS
    files = [f.name for f in dataclasses.fields(FileResult)]
    assert code_spans(cell(report, "Key", "`files[]`")) == files


def test_tables_are_read_by_header() -> None:
    lines = ["| Key | Content |", "| -- | -- |", "| `a` | x |", "", "| Rule | Detail |"]
    lines += ["| -- | -- |", "| `b` | y |"]
    assert first_cells(lines, "Key") == ["`a`"]
    assert first_cells(lines, "Rule") == ["`b`"]
    assert cell(lines, "Rule", "`b`") == "y"
