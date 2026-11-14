# charmfix

Repair the EXIF metadata of Kodak Charmera photos so Apple Photos, and everything else, shows the
right date, size and camera. Only metadata changes: every byte of image data stays the same (→
[REPAIRS.md](REPAIRS.md#invariant)), and charmfix checks this before it writes each file (→
[REPAIRS.md](REPAIRS.md#verification)). Source files are never modified (→
[CLI.md](CLI.md#source-safety)). Repaired copies go to a separate folder.

## What is wrong with Charmera files

The Charmera uses a Generalplus chip; its JPEGs carry a `GPEncoder` comment. Most files have **no
EXIF at all**, so Photos falls back to the import or copy date. Other firmware writes broken EXIF:
dates like `2026:03:03:12:16:29`, a 640×480 size on a 1440×1080 image, or a MakerNote that points
outside the EXIF block. charmfix fixes all of these; the exact rules are in
[REPAIRS.md](REPAIRS.md).

## Install

Released versions are published to [PyPI](https://pypi.org/project/charmfix/). With
[uv](https://docs.astral.sh/uv/):

```sh
uv tool install charmfix   # puts `charmfix` on your PATH
uvx charmfix -h            # or run it without installing
```

`pipx install charmfix` works too. Before the first release, or to run unreleased changes, install
from a checkout of this repository (→ [DEVELOPMENT.md](DEVELOPMENT.md#quality-gates)):

```sh
uv tool install .
uvx --from . charmfix -h
```

Supported Python versions → [DEVELOPMENT.md](DEVELOPMENT.md#prerequisites-and-setup).

## Usage

```sh
charmfix /Volumes/CHARMERA                     # writes to ~/Pictures/Kodak Charmera Repaired
charmfix /Volumes/CHARMERA -o ~/Desktop/fixed --report ~/Desktop/charmfix-report.json
charmfix /Volumes/CHARMERA --dry-run -v        # show what would change, write nothing
```

Without `-o`, copies go to the default output folder → [CLI.md](CLI.md#options). All options, exit
codes and the report format are in [CLI.md](CLI.md). Each file gets a status →
[REPAIRS.md](REPAIRS.md#status). Running charmfix again on the same card skips files it has already
written, unless a file dated from its modification time ([rule 1a](REPAIRS.md#rules)) got another
time or time zone in between (e.g. a copy made without `cp -p`) → [CLI.md](CLI.md#output-files).

**Output names start with the capture time**, e.g. `20260402-083000_PICT0010.jpg`, so a later card
whose camera numbering restarted is not skipped as already written. `--no-date-prefix` keeps the
camera's names. A folder filled with the other setting is not matched →
[CLI.md](CLI.md#output-files).

**Run it on the card itself.** When a file has no usable EXIF date (often no EXIF at all), the
capture date is the file's modification time, which the camera sets when it takes the photo
([rule 1a](REPAIRS.md#rules)). Use the mounted card, or a copy that kept the times (`cp -p`, Finder
copy). Downloaded or exported files usually carry the copy time instead.

**macOS blocks the card?** Follow the hint charmfix prints (→ [CLI.md](CLI.md#discovery)): allow the
app you run it from under System Settings → Privacy & Security → Files and Folders → Removable
Volumes (or Full Disk Access), then restart that app.

## Apple Photos workflow

1. Insert the card and run `charmfix /Volumes/<card> --report ~/charmfix-report.json`.
2. Check the summary and look at any `partial` or `failed` lines.
3. In Photos, choose **File → Import…** and pick the output folder.

## Documentation

| Location | Audience | Authority |
| -- | -- | -- |
| [CLI.md](CLI.md) | Users, developers and AI agents | Canonical contract |
| [REPAIRS.md](REPAIRS.md), [DEVELOPMENT.md](DEVELOPMENT.md), [TESTING.md](TESTING.md) | Developers and AI agents | Canonical contracts |
| [AGENTS.md](AGENTS.md), [CLAUDE.md](CLAUDE.md) (imports AGENTS.md) | AI agents | How to use the contracts |
| `README.md` | Users | Non-canonical user guide |

## License

MIT → [LICENSE](LICENSE).
