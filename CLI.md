# Command-Line Contract

Owns: command options, exit codes, discovery, output files and atomic writes, source safety, console
output, JSON report and its privacy. Routing → [AGENTS.md](AGENTS.md#canonical-sources).

## Usage

```sh
charmfix INPUT [options]
```

`INPUT` is a folder (for example the mounted card) or a single JPEG. For a single file, the input
root is the file's folder.

## Options

| Option | Default | Meaning |
| -- | -- | -- |
| `-o`, `--output` | `~/Pictures/Kodak Charmera Repaired` | Output folder. |
| `--recursive` / `--no-recursive` | recursive | Descend into subfolders. |
| `-n`, `--dry-run` | off | Analyse and report; write no images and create no output folders. |
| `--force` | off | Replace existing output files. |
| `-v`, `--verbose` | off | Log every file and every change. |
| `--report` | none | Write the [JSON report](#report) to this path. |
| `--no-preserve-structure` | mirror folders | Put every output directly in the output folder. |
| `--no-assume-charmera` | assume | Turn off `charmera(assumed)` → [REPAIRS.md](REPAIRS.md#camera-identity). |
| `--make` | → [REPAIRS.md](REPAIRS.md#camera-identity) | Target Make: 1–64 printable ASCII characters, no leading or trailing space. |
| `--model` | → [REPAIRS.md](REPAIRS.md#camera-identity) | Target Model; same rules as `--make`. |
| `--version` | — | Print the version and exit. |

## Exit codes

| Code | When |
| -- | -- |
| 0 | Every file ended `repaired`, `partial`, `already_valid` or `skipped`. |
| 1 | At least one file `failed`; all other files were still processed. |
| 2 | Usage error, input missing, an input folder that cannot be listed, a [refused path](#source-safety), the output volume is full (batch stops), or the report cannot be written. |
| 130 | Interrupted (Ctrl-C); a partial summary is still printed and `--report` is still written. Wins over a report write failure. |

## Discovery

Files are visited in sorted order. Symlinked folders inside the input are not followed.

| Case | Result |
| -- | -- |
| Name ends in `.jpg` / `.jpeg` (any case), or the single file given as `INPUT` | Candidate. |
| In a folder: any other name, `._*` AppleDouble files, `.Trashes`, `.Spotlight-V100`, `.fseventsd` | Ignored; not counted. |
| Symlink inside the input folder | `skipped` (`symlink`). A symlink given as `INPUT` is followed. |
| Not a regular file | `skipped` (`not a regular file`). |
| Larger than 64 MiB | `skipped` (`too large`). |
| Does not start with `FF D8 FF` | `skipped` (`not JPEG content`). |
| File cannot be opened | `failed` (`cannot read: …`). |
| Subfolder cannot be listed | `failed` (`cannot list folder: …`). |
| Input root cannot be listed | The run stops → [exit codes](#exit-codes). On macOS a hint names the Removable Volumes / Full Disk Access permission. |
| No candidates at all | Warning `no .jpg/.jpeg files found`. |

## Output files

| Rule | Behavior |
| -- | -- |
| Path | Input-relative path under the output folder; with `--no-preserve-structure`, the file name only. |
| Symlinked folder | A folder in the output tree that leads outside the output folder → that file `failed`; nothing is written through it. |
| Name clashes in one run | Compared case-insensitively; later files get `_1`, `_2`, … before the extension. |
| Existing output | `skipped` (`exists`) and left untouched; re-runs never create `_n` copies. `--force` replaces it. |
| Atomic write | Temp file `.charmfix-*.tmp` in the target folder → fsync → read back and compare → publish under the final name. |
| Publish without `--force` | Hard link to the final name; the link fails if the name exists, so another writer is never overwritten. |
| No hard links | (FAT/exFAT and similar) Without `--force`, the name is reserved with `O_EXCL`, then the temp file is moved over it. |
| Publish with `--force` | The temp file is renamed over the final name. |
| Cleanup | The temp file is removed on every path, including errors and Ctrl-C. |
| Permissions | `0666` minus the umask. |
| File times | Source access/modification times, to the nanosecond. If they cannot be set → warning only. |
| Disk full | The batch stops → [exit codes](#exit-codes). |

## Source safety

Source files are opened read-only and never replaced. Paths are compared by identity on disk, so
another spelling of the same folder (letter case, Unicode form) counts as that folder. A folder run
reads the whole tree, so nothing may be written in it; a single-file run reads only that file, so
its folder (often `~` or `~/Pictures`) may hold the output folder or the report.

| Input | Refused → [exit codes](#exit-codes) | Why |
| -- | -- | -- |
| Folder | Output folder equals, contains or is inside the input folder | Outputs would be read on the next run. |
| Folder | `--report` inside the input folder | The input folder stays as the camera wrote it. |
| Single file | Output folder is the file's folder | The output has the source's name; `--force` would replace it. |
| Single file | `--report` is the input file | The report would replace the source. |

## Console

| Stream | Content |
| -- | -- |
| stderr, TTY only, not `-v` | One progress line, rewritten in place. |
| stderr | One line per `failed` or `partial` file, or per file with warnings. |
| stderr, `-v` | Per-file status, SOF size, old sizes, date source, identity, MakerNote status and every change. |
| stdout | The summary: discovered, repaired, partial, already valid, skipped (with reasons), failed, timestamps fixed, EXIF rebuilt, MakerNotes dropped, and a stop reason if any. |

## Report

`--report` writes UTF-8 JSON (2-space indent), also in a dry run. A temp file next to it is renamed
over the path, so an existing file there is replaced, never written through.

| Key | Content |
| -- | -- |
| `tool`, `version` | `"charmfix"` and the package version. |
| `options` | `recursive`, `preserve_structure`, `force`, `dry_run`, `assume_charmera`, `make`, `model`. |
| `summary` | The console summary counts, plus `processed`, `skipped_reasons` and `stopped`. |
| `files[]` | One object per candidate: `rel_path`, `status`, `output`, `reason`, `error`, `sof_size`, `old_sizes`, `raw_date`, `date_written`, `timestamp_source`, `changes`, `exif_rebuilt`, `makernote`, `identity`, `private_text`, `partial_reasons`, `warnings`, `image_sha256`. |

| Privacy rule | Detail |
| -- | -- |
| Paths | Relative to the input root (`rel_path`) or output folder (`output`); never absolute. |
| Errors | OS errors give only the error text, never the file name. |
| Free text | UserComment, Artist and Copyright are reported only as `kept` / `dropped` in `private_text`. Their dropped values never appear in `changes`. |
| Other tag values | Make, Model and dates appear as they are in `raw_date`, `changes` and `warnings`; the target Make and Model in `options`. |
