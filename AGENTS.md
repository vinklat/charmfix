# AI Coding Agent Guide

Read the contract that owns a fact before changing behavior; keep one owner per fact and link to it
instead of copying.

## Source priority

1. The root contract that owns the domain (table below). `DEVELOPMENT.md` prevails on style.
2. The implementation in `src/charmfix/` and the tests, when checking current behavior.
3. `README.md` and `docs/guides/`: the non-canonical user guides.

Resolve drift by updating the owning contract and the code together, never by adding a second
definition.

## Canonical sources

| File | Owns |
| -- | -- |
| [REPAIRS.md](REPAIRS.md) | Image-data invariant, verification, repair rules, camera identity, defects, EXIF output format, file statuses |
| [CLI.md](CLI.md) | Command options, exit codes, discovery, output files and atomic writes, source safety, console output, JSON report and its privacy |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Setup, quality gates, release, layout, module dependencies, style |
| [TESTING.md](TESTING.md) | Test harness and required cases |

## Task routing

| Task | Primary | Also check |
| -- | -- | -- |
| Change or add a repair rule | `REPAIRS.md` rules | `TESTING.md` repair rules, `docs/guides/usage.md` |
| New EXIF defect from a real camera file | Add the file under `tests/samples/<model>/` → [REPAIRS.md](REPAIRS.md#defects) | `TESTING.md` real samples |
| CLI option or exit code | `CLI.md` | `docs/guides/usage.md`, `README.md` usage, `TESTING.md` CLI |
| File reading/writing, report fields | `CLI.md` | `DEVELOPMENT.md` layout, `docs/guides/usage.md` |
| New module or import | `DEVELOPMENT.md` layout and dependencies | — |
| Tests and fixtures | `TESTING.md` | The contract for the behavior under test |
| Build, release, version bump | `DEVELOPMENT.md` release | `.github/workflows/release.yml` |
| Quality gate or CI change | `DEVELOPMENT.md` quality gates | `.github/workflows/ci.yml` |
| Any `*.md` edit | [Writing rules](#writing-rules) | `tests/test_docs.py` |

## Workflow

1. Read only the owning contract sections.
2. Change the contract and the code in the same change.
3. Add or update the required cases in `TESTING.md`'s table.
4. Run every gate in [DEVELOPMENT.md](DEVELOPMENT.md#quality-gates); CI runs them again.

## Writing rules

- One owner per fact. Link it as `→ [FILE.md](FILE.md#anchor)`; never copy its table or values.
- Each contract opens with its title, then one paragraph: `Owns: <its row in the table above>.`
  followed by `Routing → [AGENTS.md](AGENTS.md#canonical-sources).` `tests/test_docs.py` checks that
  the domain matches the table.
- Prefer tables with one fact per row. Keep cells short: split rows rather than pad them.
- Wrap prose at 100 columns; table rows are exempt.
- No TODOs or planned work in contracts.
- `README.md` and `docs/guides/` explain and link; they never define behavior.
- `tests/test_docs.py` must pass after any `*.md` edit.
