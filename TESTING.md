# Test Contract

Owns: test harness and required cases. Routing → [AGENTS.md](AGENTS.md#canonical-sources).

Tests assert the owning contract directly; its values are not copied here.

## Harness

| Item | Rule |
| -- | -- |
| Builders | `tests/helpers.py`: JPEG images, TIFF/EXIF blocks and their entries, and the canonical defects. |
| Builder rule | Build fixtures with the builders, never as inline bytes. |
| Fixtures | `tests/conftest.py`: `base_jpeg`, `icc_profile`, `icc_jpeg` (session scope). |
| Real samples | `tests/samples/<model>/*.jpg` (`REAL_SAMPLES`); every new sample is covered automatically. |
| Sample mtime | Tests copy samples to `tmp_path` and plant a known mtime; the repo copy's mtime is never trusted. |
| File system | Only `tmp_path`; tests never write into `tests/`. |
| External tools | `exiftool` tests skip when it is missing; `sips` tests skip off macOS. |
| Letter case | Other-case rows skip when the temp folder's file system is case-sensitive. |
| Naming | `tests/test_<module or topic>.py`, `test_<behavior>`; parameterize cases that differ only in input. |
| Gates | → [DEVELOPMENT.md](DEVELOPMENT.md#quality-gates). |

## Required cases

| Area (file) | Required assertion |
| -- | -- |
| JPEG parse (`test_jpeg.py`) | Pillow and real-sample layouts parse; each unrecoverable header raises `JpegError`; missing EOI is a warning. |
| JPEG splice (`test_jpeg.py`) | `image_data` excludes exactly APP/COM; fill bytes and trailing data survive. |
| JPEG layout (`test_jpeg.py`) | Non-Exif segments kept byte-for-byte, one Exif segment, APP1 placement → [REPAIRS.md](REPAIRS.md#invariant). |
| EXIF reader damage (`test_exif_read.py`) | Every kind of damage (header, bounds, loop, entry table, type, count, pointer) gives a warning, never an exception. |
| EXIF reader values (`test_exif_read.py`) | Both byte orders; first duplicate wins; MakerNote status; thumbnail bounds. |
| Dates (`test_dates.py`) | Accepted and rejected forms, normal form, sanity warnings → [REPAIRS.md](REPAIRS.md#rules). |
| Repair rules (`test_repair.py`) | At least one case per rule → [REPAIRS.md](REPAIRS.md#rules). |
| Repair identity and status (`test_repair.py`) | Each identity → [REPAIRS.md](REPAIRS.md#camera-identity); each status → [REPAIRS.md](REPAIRS.md#status). |
| Repair target text (`test_repair.py`) | Accepted boundaries and each rejected kind → [REPAIRS.md](REPAIRS.md#camera-identity), for Make and Model, each naming its field. |
| Repair round trip (`test_repair.py`) | Every case runs plan → write → splice → verify, re-reads the output, and a second run with the same options gives `already_valid` and no warning the first did not → [REPAIRS.md](REPAIRS.md#camera-identity). |
| Output validity (`test_external.py`) | Per generated fixture: identical pixels and image-data hash, `exiftool -validate` OK, `sips` size and date. |
| Output names (`test_output.py`, `test_cli.py`) | Every row from path to other prefix setting → [CLI.md](CLI.md#output-files). |
| Atomic writes (`test_output.py`) | Every row from atomic write to file times → [CLI.md](CLI.md#output-files). |
| CLI batch (`test_cli.py`) | A corrupt file fails only that file; source hashes and mtimes are unchanged. |
| CLI re-run (`test_cli.py`) | Second run → only `exists` skips, each with its `output` and nothing built, also as a dry run; run on the output → all `already_valid`. |
| CLI modes (`test_cli.py`) | Dry run writes nothing; flatten; no recursion; single-file input; `--force`; `--no-date-prefix`; `--make`/`--model`, and a rejected value with its message. |
| CLI refusals (`test_cli.py`) | Every refusal, also with a path in another letter case and with a failing identity check; its own message is printed; nothing is written → [CLI.md](CLI.md#source-safety). |
| CLI write safety (`test_cli.py`, `test_output.py`) | Symlinked output folder, with and without `--force` → [CLI.md](CLI.md#output-files); hard-linked or unwritable report → [CLI.md](CLI.md#report); every source unchanged. |
| CLI single file (`test_cli.py`) | Output or report near the file but not refused → the run succeeds and the source is unchanged. |
| CLI stops (`test_cli.py`) | Disk full (raised by the real write) and Ctrl-C stop the batch with their exit codes and a summary → [CLI.md](CLI.md#exit-codes). |
| CLI discovery (`test_cli.py`) | Unreadable root and subfolder, macOS hint (and when it is not shown), empty-folder warning, non-regular input → [CLI.md](CLI.md#discovery). |
| CLI report (`test_cli.py`) | Shape and privacy → [CLI.md](CLI.md#report). |
| Real samples (`test_real_samples.py`) | Per sample: date fallback and size → [REPAIRS.md](REPAIRS.md#rules); identity → [REPAIRS.md](REPAIRS.md#camera-identity); file times → [CLI.md](CLI.md#output-files); second run → [REPAIRS.md](REPAIRS.md#status). |
| Real sample integrity (`test_real_samples.py`) | Unchanged pixels, image hash and `GPEncoder` comment; `--no-assume-charmera`; `exiftool`; `sips`. |
| Robustness (`test_fuzz.py`) | Random damage: `rebuild` raises only the expected failures; success keeps image data; reader needs no catch-all. |
| Docs links (`test_docs.py`) | Links and anchors resolve; each contract has the `Owns:` preamble → [AGENTS.md](AGENTS.md#writing-rules). |
| Docs parity (`test_docs.py`) | `CLI.md` options and report keys, and `REPAIRS.md` statuses match the code. |
| Module dependencies (`test_architecture.py`) | Every `charmfix` import follows → [DEVELOPMENT.md](DEVELOPMENT.md#dependencies); the import finder has its own cases; the layers match that table. |

## Maintenance

For each behavior change: update the owning contract, add success and failure cases, and edit this
table only when a new kind of assertion appears.
