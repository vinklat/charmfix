# Development Contract

Owns: setup, quality gates, layout, module dependencies, style. Routing →
[AGENTS.md](AGENTS.md#canonical-sources).

## Prerequisites and setup

| Item | Rule |
| -- | -- |
| Python | `requires-python = ">=3.11"`; local development uses the version in `.python-version`. |
| Tooling | [uv](https://docs.astral.sh/uv/); `uv sync` installs the package and the `dev` group. |
| Runtime dependencies | `piexif` only; its role → [REPAIRS.md](REPAIRS.md#output-format). Adding one needs a stated reason. |
| Dev dependencies | `pytest`, `pillow` (fixtures and pixel checks; never imported by `src/`), `hypothesis`, `ruff`, `mypy`. |
| External tools | `exiftool`, and `sips` on macOS; optional. Test behavior without them → [TESTING.md](TESTING.md#harness). |
| Version | `pyproject.toml` `version` only; `charmfix.__version__` reads it from the installed package metadata (`0+unknown` when not installed). |
| Version bump | `uv version --bump patch` (or `minor`, `major`). |

## Quality gates

Run all of these before handing over a change.

| Check | Command |
| -- | -- |
| Tests (including doc checks) | `uv run pytest -q` |
| Format | `uv run ruff format .` (check: `uv run ruff format --check .`) |
| Lint | `uv run ruff check .` |
| Types | `uv run mypy src tests` |
| Build | `uv build` |
| Install the CLI | `uv tool install .`, or run without installing: `uvx --from . charmfix` |

## Layout

| Path | Owns |
| -- | -- |
| `src/charmfix/cli.py` | Argument parsing, path refusals, console output, exit codes. |
| `src/charmfix/pipeline.py` | Batch preparation; `rebuild()` (in memory) and `process_file()` (one file, errors → result). |
| `src/charmfix/discovery.py` | Walking the input and classifying candidates. |
| `src/charmfix/output.py` | Output path mapping and atomic writes; the only module that writes image files. |
| `src/charmfix/report.py` | `FileResult`, `Summary`, JSON report. |
| `src/charmfix/repair.py` | Pure repair rules → `RepairPlan`; no file access. |
| `src/charmfix/exif_write.py` | TIFF/Exif serializer. |
| `src/charmfix/verify.py` | Independent re-check of the output. |
| `src/charmfix/jpeg.py` | JPEG segment parser, image-data hash, splice. |
| `src/charmfix/exif_read.py` | Tolerant TIFF/IFD reader. |
| `src/charmfix/dates.py` | EXIF date parsing and formatting. |
| `tests/` | Tests, builders and real samples → [TESTING.md](TESTING.md#harness). |

## Dependencies

Imports point downwards only.

| Layer | Modules | May import |
| -- | -- | -- |
| Entry | `cli` | Any layer below, and `__version__` from the package. |
| Orchestration | `pipeline` | Steps, I/O and primitives. |
| Steps | `repair`, `exif_write`, `verify` | `repair` and primitives (`exif_write` and `verify` depend on `repair`). |
| I/O | `discovery`, `output`, `report` | Nothing from `charmfix` except `__version__`. |
| Primitives | `jpeg`, `exif_read`, `dates` | Nothing from `charmfix`. |

## Style

| Rule | Detail |
| -- | -- |
| Formatting and lint | Ruff, line length 100, rule set in `pyproject.toml`. |
| Types | mypy `strict`; `from __future__ import annotations` in every module. |
| Data | Frozen dataclasses for values; `ExifData`, `RepairPlan` and the report types are the mutable exceptions. |
| EXIF reader | `exif_read` never raises on bad input: it records a warning and skips. |
| JPEG parser | `jpeg.parse` raises only `JpegError`. |
| Errors | `process_file` turns any exception into a `failed` result; batch behavior → [CLI.md](CLI.md#exit-codes). |
| Source files | Opened only with `open(path, "rb")` → [CLI.md](CLI.md#source-safety). |
| Report text | Follows every privacy rule → [CLI.md](CLI.md#report). |
| Docstrings | Every module has one; public functions that are not obvious get one. |
