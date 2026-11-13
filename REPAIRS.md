# Repair Contract

Owns: image-data invariant, verification, repair rules, camera identity, defects, EXIF output
format, file statuses. Routing → [AGENTS.md](AGENTS.md#canonical-sources).

## Invariant

Only the Exif APP1 segment is rewritten. Everything else stays byte-for-byte identical and in its
original position. That covers every other APP/COM segment (JFIF APP0, ICC APP2, XMP, APP13, the
`GPEncoder` comment, …). It also covers every byte from the first non-APP/COM marker through the
scan data, EOI and any trailing bytes.

| Placement | Rule |
| -- | -- |
| New APP1 | Directly after SOI, or after the JFIF APP0 when that is the first segment. |
| Old Exif APP1s | All removed, wherever they were; exactly one remains. |
| Unchanged file | No defect → copied as is → [Status](#status) (`already_valid`). |

## Verification

The output is parsed again before it is written. Any failed check makes the file `failed`.

| Check | Must hold |
| -- | -- |
| Image data | SHA-256 of all non-APP/COM bytes equals the source. |
| Other segments | Every non-Exif APP/COM segment is identical and in the same order. |
| ICC / XMP | Byte-identical to the source. |
| Exif count | Exactly one Exif APP1 segment. |
| Exif structure | The new block reads back with no reader warning. |
| Planned values | Dates, Orientation, Make, Model and PixelX/YDimension equal the planned values. |
| Size | PixelXDimension = SOF width and PixelYDimension = SOF height. |

## Rules

Applied in this order; tag IDs are hexadecimal. Only a [defect](#defects) triggers a rewrite.

| # | Tag(s) | Rule |
| -- | -- | -- |
| 1 | DateTimeOriginal `9003`, DateTimeDigitized `9004`, DateTime `0132` | All three present and exactly `YYYY:MM:DD HH:MM:SS` → kept as they are. |
|  | Capture time | Otherwise the first value that parses, in the order above; all three tags are set to it. |
|  | Accepted forms | Standard form plus `2026:03:03:12:16:29`, `…T…` and `…-…` separators. |
| 1a | Date fallback | No value parses → the source file's modification time, as local wall-clock time (`timestamp_source=file_mtime`). The time of the run is never used. |
| 1b | Date sanity | A date before 2000 or more than a day in the future → warning; the date is still used. |
| 1c | SubSecTime\* `9290`–`9292` | Kept when the value is digits only. |
| 1d | OffsetTime\* `9010`–`9012` | Kept when the dates are kept; dropped when the dates are rewritten. A time zone is never added. |
| 2 | PixelXDimension `A002`, PixelYDimension `A003` | Always set from the SOF marker. |
| 2a | DNL height | SOF height 0 → both tags are left out; status → [Status](#status). |
| 2b | ImageWidth `0100`, ImageLength `0101` (IFD0) | Dropped: not recorded for JPEG-compressed data (EXIF spec). |
| 3 | Orientation `0112` | SHORT 1–8 → kept. Missing or invalid → 1. Pixels are never rotated. |
| 4 | MakerNote `927C` | Always dropped when rebuilding. |
|  | MakerNote report | `discarded(invalid)` when it pointed outside the block, otherwise `discarded(not relocatable)`. |
| 4a | Interop IFD, pointers `8769` `8825` `A005` `0201` `0202`, strips `0111` `0117` | Dropped; the writer recreates the pointers it needs. |
| 5 | Other IFD0, Exif and GPS tags | Kept when the tag is in piexif's table, its stored type is compatible and its count fits. Unknown tags are dropped. |
| 5a | ExposureTime `829A`, FNumber `829D`, XResolution `011A`, YResolution `011B` | Rational > 0. |
| 5b | Flash `9209`, MeteringMode `9207`, WhiteBalance `A403`, ExposureProgram `8822`, ColorSpace `A001`, ResolutionUnit `0128`, YCbCrPositioning `0213` | Value defined by the EXIF spec. |
| 5c | UserComment `9286` | Starts with an 8-byte code prefix: `ASCII`, `JIS`, `UNICODE` or undefined. |
| 5d | ASCII tags | Printable UTF-8 text. |
| 5e | IFD1 thumbnail | Kept only as a complete JPEG inside the block. |
|  | IFD1 tags | Compression 6, X/YResolution 72/1 and ResolutionUnit 2 are added if missing. |
| 5f | Thumbnail size | Rebuilt block > 65533 bytes → thumbnail dropped with a warning. Still too large → `failed`. |
| 6 | Mandatory tags | Added when missing: ExifVersion `0232`, FlashpixVersion `0100`, ComponentsConfiguration `1 2 3 0`, ColorSpace 1 (`FFFF` with an ICC profile), X/YResolution 72/1, ResolutionUnit 2, YCbCrPositioning 1. |
| 7 | Make `010F`, Model `0110` | See [Camera identity](#camera-identity). LensModel, FocalLength and FNumber are never invented. |

## Camera identity

| Identity | When | Make / Model |
| -- | -- | -- |
| `charmera(exif)` | Make or Model contains "charmera" (any case) | Set to the target. |
| `charmera(assumed)` | `GPEncoder` comment, default options | Set to the target when Make and Model are both empty; kept when both equal the target; otherwise kept with a warning. |
| `generalplus-unconfirmed` | `GPEncoder` comment with `--no-assume-charmera` | Left as they are. |
| `unknown` | Anything else | Left as they are. |

| Target | Default | Option → [CLI.md](CLI.md#options) |
| -- | -- | -- |
| Make | `Kodak` | `--make` |
| Model | `Charmera` | `--model` |

| Target rule | Detail |
| -- | -- |
| Comparison | Surrounding spaces and trailing NULs are ignored; a value equal to the target is kept as stored. |

## Defects

Each of these triggers a rebuild; with none → [Status](#status).

| Defect | Source |
| -- | -- |
| No Exif APP1, or more than one | Structure |
| Block unreadable, or any reader warning (out-of-bounds value, loop, duplicate tag, truncation, …) | Structure |
| Known tag dropped by rule 5 | Rule 5 |
| Dates missing or not in standard form | Rule 1 |
| PixelX/YDimension missing or different from SOF; IFD0 size tags present | Rule 2 |
| Orientation missing or invalid | Rule 3 |
| Mandatory tag missing | Rule 6 |
| Make/Model need setting | Rule 7 |
| Thumbnail present but not a complete JPEG | Rule 5e |

Unknown tags and a valid MakerNote are not defects; an `already_valid` file keeps them.

## Output format

The new block is written by charmfix's own TIFF writer. piexif supplies only its tag/type table: its
own writer omits sub-IFD next pointers and misaligns values, and exiftool flags both.

| Property | Value |
| -- | -- |
| Byte order | Big-endian (`MM`). |
| IFD order | IFD0, Exif, GPS, IFD1, thumbnail; empty IFDs are omitted. |
| Entries | Sorted by tag; types from piexif's table. |
| Alignment | Every IFD and out-of-line value starts on an even offset. |
| Next-IFD pointer | Present on every IFD; IFD0 links to IFD1, all others are 0. |
| Interop IFD | Never written. |

## Status

| Status | Condition |
| -- | -- |
| `repaired` | At least one defect, fixed, with no partial reason. A file with no EXIF that gets a new block dated from its file time is `repaired`. |
| `partial` | Repaired, but existing EXIF was lost: block unreadable, present without a valid date (file time used), or size unknown (DNL, rule 2a). |
| `already_valid` | No defect; copied byte-for-byte. |
| `skipped` | Not handled → reasons in [CLI.md](CLI.md#discovery) and [CLI.md](CLI.md#output-files). |
| `failed` | JPEG not safely parseable (no SOI, bad marker, truncated header, no SOF before SOS, width 0), rebuilt EXIF too large, verification failed, or an I/O error. |

Warnings (rule 1b, rule 5f, a kept foreign Make/Model, missing EOI, multiple SOF, and the
output-file warnings → [CLI.md](CLI.md#output-files)) are reported but never change the status.
