# charmfix

Repair the EXIF metadata of Kodak Charmera photos so Apple Photos, and everything else, shows the
right date, size and camera. Only metadata changes: every byte of image data stays the same.

## What is wrong with Charmera files

The Charmera uses a Generalplus chip; its JPEGs carry a `GPEncoder` comment. Most files have **no
EXIF at all**, so Photos falls back to the import or copy date. Other firmware writes broken EXIF:
dates like `2026:03:03:12:16:29`, a 640×480 size on a 1440×1080 image, or a MakerNote that points
outside the EXIF block. charmfix fixes all of these; the exact rules are in
[REPAIRS.md](REPAIRS.md).

## Install

With [Homebrew](https://brew.sh/):

```sh
brew install vinklat/tap/charmfix
```

Released versions are also published to [PyPI](https://pypi.org/project/charmfix/). With
[uv](https://docs.astral.sh/uv/):

```sh
uv tool install charmfix
```

`pipx install charmfix` works too, and `uvx charmfix` runs it without installing.

## Usage

```sh
charmfix /Volumes/CHARMERA
```

It repairs the EXIF metadata of the photos on the card and writes the fixed copies to the default
output folder (`~/Pictures/Kodak Charmera Repaired` unless `-o` is given). Pictures on the card
itself are never modified, and the next run skips files it has already fixed.

All options and usage details → [full usage documentation](docs/guides/usage.md).

## Apple Photos workflow

1. Insert the card.
2. In a terminal, run `charmfix /Volumes/<card>`.
3. Check the summary.
4. In Photos, choose **File → Import…** and pick the output folder
   `~/Pictures/Kodak Charmera Repaired`.

## Documentation

| Location | Audience | Authority |
| -- | -- | -- |
| [usage.md](docs/guides/usage.md) | Users | Non-canonical user guide |
| [CLI.md](CLI.md), [REPAIRS.md](REPAIRS.md), [DEVELOPMENT.md](DEVELOPMENT.md), [TESTING.md](TESTING.md) | Developers and AI agents | Canonical contracts |
| [AGENTS.md](AGENTS.md) | AI agents | How to use the contracts |

## License

MIT → [LICENSE](LICENSE).
