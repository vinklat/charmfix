# charmfix usage guide

How to run charmfix, what it writes and how to read its output. This guide explains and links: the
exact rules are in [CLI.md](../../CLI.md) and [REPAIRS.md](../../REPAIRS.md), and they prevail if
this guide differs. Installation → [README.md](../../README.md#install).

## Quick start

Insert the camera's card and run charmfix on it:

```sh
charmfix /Volumes/CHARMERA
```

`/Volumes/CHARMERA` is where macOS mounts the card; use the name your card shows in Finder. On
Linux, use the card's mount point instead.

charmfix then:

1. finds every `.jpg` and `.jpeg` file on the card, including subfolders;
2. writes a repaired copy of each one to `~/Pictures/Kodak Charmera Repaired`;
3. prints a [summary](#reading-the-summary).

The card is only read: nothing on it is changed, renamed or deleted →
[CLI.md](../../CLI.md#source-safety).

## What gets repaired

Only the metadata (EXIF) is rewritten. The picture itself is copied byte for byte, and charmfix
checks that before it writes each file → [REPAIRS.md](../../REPAIRS.md#verification).

| Shown in Photos | What charmfix writes |
| -- | -- |
| Date and time | The capture time → [where the date comes from](#where-the-date-comes-from). |
| Size | The real pixel size of the image. |
| Camera | `Kodak Charmera`, or [your own text](#camera-name). |
| Orientation | Kept; set to normal when missing or invalid. Pixels are never rotated. |

| Good to know | Detail |
| -- | -- |
| Healthy files | A photo with nothing wrong is copied unchanged. |
| Rebuilt EXIF | The camera's MakerNote and tags charmfix does not know are dropped. |
| Never added | A time zone, lens or exposure data. |
| Other files | Videos and anything else that is not a JPEG are ignored. |

All repair rules → [REPAIRS.md](../../REPAIRS.md#rules).

## Where the date comes from

Most Charmera photos carry no date inside the file. The only record of when the photo was taken is
the file's modification time, which the camera sets when it takes the photo. charmfix uses that
time whenever the file has no readable EXIF date.

**So run charmfix on the card itself**, or on a copy that kept the file times (Finder copy,
`cp -p`). Files that were downloaded, exported or sent through a chat app usually carry the time of
the copy, and that is the date you would get.

| Situation | Date written |
| -- | -- |
| EXIF date present and readable, even in the camera's broken form | That date. |
| No readable EXIF date | The file's modification time. |
| Date before 2000 or in the future | Still used, with a warning: the camera clock was probably not set. |

charmfix cannot correct a camera clock that was wrong when the photo was taken.

## Where the copies go

Copies keep the card's folder layout under the output folder, and each name starts with the capture
time:

```text
/Volumes/CHARMERA/DCIM/PICT0010.jpg
~/Pictures/Kodak Charmera Repaired/DCIM/20260402-083000_PICT0010.jpg
```

The prefix keeps photos apart after the camera restarts its numbering: a new `PICT0010.jpg` is not
mistaken for the one you copied last month. Each copy also keeps the file times of its source, so
sorting by date in Finder works too.

| To change | Use |
| -- | -- |
| The output folder | `-o ~/Desktop/fixed` |
| Names as the camera wrote them | `--no-date-prefix` |
| All copies in one folder, no subfolders | `--no-preserve-structure` |
| Only the given folder, not its subfolders | `--no-recursive` |

Two files that would get the same name in one run are told apart with `_1`, `_2`, … before the
extension. The output folder must not be on the card or contain it →
[messages](#messages-and-fixes). All naming rules → [CLI.md](../../CLI.md#output-files).

## Reading the summary

```text
Summary:
  discovered            2
  repaired              2
  partial               0
  already valid         0
  skipped               0
  failed                0
  timestamps fixed      2
  EXIF rebuilt          2
  MakerNotes dropped    0
```

| Line | Meaning | What to do |
| -- | -- | -- |
| `discovered` | JPEG files found. | — |
| `repaired` | Fixed and written. | — |
| `partial` | Written, but EXIF the file already had could not all be kept. | Check the date of these photos. |
| `already valid` | Nothing to fix; copied unchanged. | — |
| `skipped` | Not written; the reasons are listed below the line. | `exists` is normal on a [second run](#running-it-again). |
| `failed` | Not written; the other files were still processed. | Read the message printed for the file. |

Every `partial` and `failed` file, and every file with a warning, gets its own line above the
summary. The last three lines count repairs of interest: dates written, metadata blocks rebuilt and
MakerNotes dropped. Exact conditions → [REPAIRS.md](../../REPAIRS.md#status); skip reasons →
[CLI.md](../../CLI.md#discovery).

## Running it again

Run charmfix after every outing: photos already in the output folder are skipped as `exists`, and
only the new ones are written.

A photo is matched by its output name, so it is written again, next to the older copy, when that
name changes:

| Change since the last run | Photos affected |
| -- | -- |
| The file's modification time changed (a copy that did not keep it) | Those dated from the file time. |
| The computer's time zone changed | Those dated from the file time. |
| `--no-date-prefix` switched on or off | All. |

To write everything again over the existing copies, add `--force`.

## Options

| Option | What it does |
| -- | -- |
| `-o DIR`, `--output DIR` | Write the copies to `DIR` instead of `~/Pictures/Kodak Charmera Repaired`. |
| `-n`, `--dry-run` | Show what would happen; write no photos and create no folders. |
| `-v`, `--verbose` | List every file and every change. |
| `--report FILE` | Also save the results as JSON → [report](#report). |
| `--force` | Replace copies that already exist in the output folder. |
| `--no-date-prefix` | Keep the camera's file names. |
| `--no-preserve-structure` | Put all copies directly in the output folder. |
| `--no-recursive` | Do not read subfolders. |
| `--make TEXT`, `--model TEXT` | Camera name to write → [camera name](#camera-name). |
| `--no-assume-charmera` | Name the camera only on files that say Charmera themselves. |
| `--version` | Print the version. |
| `-h`, `--help` | Print the option list. |

Defaults and exact meaning → [CLI.md](../../CLI.md#options).

### Preview first

```sh
charmfix /Volumes/CHARMERA --dry-run -v
```

Nothing is written. For each file you see its status, where the date came from and every value that
would change:

```text
repaired DCIM/PICT0010.jpg
  SOF (1440, 1080), old sizes {…}; date None -> 2026:04:02 08:30:00 (source: file_mtime); …
  change: Exif.DateTimeOriginal: None -> 2026:04:02 08:30:00
  change: Exif.PixelXDimension: None -> 1440
  change: 0th.Make: None -> Kodak
  …
```

`source: file_mtime` means the date was taken from the file's modification time.

### A single photo

```sh
charmfix ~/Downloads/PICT0010.jpg -o ~/Desktop/fixed
```

Give the file instead of a folder. The output folder must be a different folder from the one the
photo is in.

### Camera name

charmfix treats a file as a Charmera photo when its EXIF says so, or when it carries the
`GPEncoder` comment the camera's chip writes. Those files get the Make `Kodak` and the Model
`Charmera`.

| To | Use |
| -- | -- |
| Write another name | `--make "My Brand" --model "My Camera"` (plain ASCII text) |
| Leave files that only have the `GPEncoder` comment unnamed | `--no-assume-charmera` |
| Change the name on photos already copied | Run on the originals again with the new name and `--force`. |

A file that already names another camera keeps that name, with a warning. All identity rules →
[REPAIRS.md](../../REPAIRS.md#camera-identity).

### Report

```sh
charmfix /Volumes/CHARMERA --report ~/charmfix-report.json
```

The report lists, for every file, its status, output name, the date written and where it came from,
each change, and any warning or error. It is also written in a dry run.

| Property | Detail |
| -- | -- |
| Location | Anywhere outside the folder charmfix reads. |
| Paths | Relative only; your folder names are not included. |
| Private text | Comments, artist and copyright text are not included. |
| Existing file | Replaced. |

All keys and privacy rules → [CLI.md](../../CLI.md#report).

## Import into Apple Photos

1. Insert the card and run `charmfix /Volumes/<card>`.
2. Check the summary; look at any `partial` or `failed` line.
3. In Photos, choose **File → Import…** and pick the output folder.

## Messages and fixes

| Message or symptom | Cause | Fix |
| -- | -- | -- |
| `macOS is blocking access` | The app you run charmfix from may not read removable volumes. | Allow it under System Settings → Privacy & Security → Files and Folders → Removable Volumes (or Full Disk Access), then restart that app. |
| `input not found` | The card is not mounted, or the name is different. | Check the name in Finder or with `ls /Volumes`. |
| `no .jpg/.jpeg files found` | The folder holds no JPEG files. | Point charmfix at the card or the folder with the photos. |
| `input and output folders must not overlap` | The output folder is on the card, or contains it. | Choose an output folder elsewhere with `-o`. |
| `the output folder must not be the input file's folder` | A single photo would be written next to itself. | Choose another folder with `-o`. |
| `the report must not be written inside the input folder` | `--report` points at the card. | Save the report elsewhere. |
| `stopping: output volume is full` | The disk with the output folder is full. | Free some space and run again; finished photos are skipped. |
| Every photo has the date of the copy | The files were not read from the card → [where the date comes from](#where-the-date-comes-from). | Run on the card, then delete the wrongly dated copies. |
| A photo appears twice in the output folder | Its output name changed → [running it again](#running-it-again). | Delete the copy you do not want. |

## Exit codes

For scripts:

| Code | Meaning |
| -- | -- |
| 0 | Every file was repaired, already valid or skipped. |
| 1 | At least one file failed; the others were still processed. |
| 2 | charmfix could not run or had to stop: wrong option, missing input, refused path, full disk. |
| 130 | Interrupted with Ctrl-C; the summary and the report are still written. |

All cases → [CLI.md](../../CLI.md#exit-codes).
