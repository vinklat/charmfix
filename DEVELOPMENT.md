# Development Contract

Owns: setup, quality gates, release, layout, module dependencies, style. Routing →
[AGENTS.md](AGENTS.md#canonical-sources).

## Prerequisites and setup

| Item | Rule |
| -- | -- |
| Python | `requires-python = ">=3.11"`; local development uses the version in `.python-version`. |
| Supported Python | 3.11–3.14; the `pyproject.toml` classifiers and the `ci.yml` test matrix list exactly these. |
| Tooling | [uv](https://docs.astral.sh/uv/); `uv sync` installs the package and the `dev` group. |
| Runtime dependencies | `piexif` only; its role → [REPAIRS.md](REPAIRS.md#output-format). Adding one needs a stated reason. |
| Dev dependencies | `pytest`, `pillow` (fixtures and pixel checks; never imported by `src/`), `hypothesis`, `ruff`, `mypy`. |
| External tools | `exiftool`, and `sips` on macOS; optional. Test behavior without them → [TESTING.md](TESTING.md#harness). |
| Version | `pyproject.toml` `version` only; `charmfix.__version__` reads it from the installed package metadata (`0+unknown` when not installed). |

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
| CI | `.github/workflows/ci.yml` runs every gate above on each push to `main` and each pull request. |
| CI test matrix | Ubuntu and macOS × every [supported Python](#prerequisites-and-setup), with `exiftool` installed. |

## Release

`.github/workflows/release.yml` publishes to PyPI with trusted publishing; no API token is stored.

| Item | Rule |
| -- | -- |
| Trigger | Pushing a tag `vX.Y.Z`, with or without a suffix (e.g. `v0.2.0rc1`). |
| Tag on main | The tagged commit must be on `main`, or the release stops. |
| Version check | The tag without its `v` must equal the `pyproject.toml` version, or the release stops. |
| Gates | `ci.yml` runs in full before the build. |
| Build | sdist and wheel, without the uv cache. |
| PyPI description | `README.md`, with its relative links rewritten to the tag on GitHub; the repo copy is unchanged. |
| Check order | The checks below run after the artifact upload. |
| Check failure | Any failed check stops the release. |
| Link check | No relative link is left in the wheel's description. |
| Metadata check | `twine check --strict` passes. |
| Smoke test | The wheel's `charmfix --version` prints the tag version. |
| Stages | TestPyPI, then PyPI, then a GitHub release. |
| GitHub release | The sdist and wheel, with generated notes; a tag with a suffix makes a pre-release. |
| GitHub environments | `testpypi` and `pypi`; `pypi` has a required reviewer, so the PyPI upload waits for approval. |
| Tag ruleset | A repository ruleset limits who creates `v*` tags. |
| Trusted publisher identity | Owner `vinklat`, repo `charmfix`, workflow `release.yml`. |
| Trusted publisher on TestPyPI | That identity with environment `testpypi`. |
| Trusted publisher on PyPI | That identity with environment `pypi`. |
| Version bump | `uv version --bump patch` (or `minor`, `major`). |
| Steps | Version bump → commit and push to `main` → `git tag vX.Y.Z` → `git push origin vX.Y.Z`. |

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
| `.github/workflows/` | CI → [Quality gates](#quality-gates); release → [Release](#release). |

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
