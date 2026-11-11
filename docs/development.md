# Development

## Running the tests

The tests are plain scripts (no test framework): each one exits with 0 when everything is fine. They use the
reference logs in `data/` and the built-in emulator, never a real serial port or network.

```bat
python scripts\run_tests.py
```

Single test: `python test_mk2_link.py`. The GUI tests open a hidden Tk window, so a desktop session is needed
(on Linux without a desktop: `xvfb-run python scripts/run_tests.py`). The whole suite takes about a minute; the
emulator tests run at the real 2400 baud.

**Continuous integration:** `.github/workflows/tests.yml` runs the suite and `build_release.py --check` on every
push and pull request (Ubuntu, Python 3.12, Tk under Xvfb).

| Test | What it covers |
|---|---|
| `test_mk2_config.py` | `mk2.toml` parsing, safety limits that can only be narrowed, software alarms, GX service helpers |
| `test_mk2_mqtt.py` | conversion to Venus values, the registration protocol with the driver, current limit and Mode control |
| `test_mk2_link.py` | frames against captured ones, whitelist, settings writes and flag handling, repeating of switch and knob, monitor only (nothing sent without control, also against the emulator), firmware lock, stopping the write chain at the first failure |
| `test_write_dialog.py` | the settings tabs and the write chain (preview, validation, derived fields, presets, firmware lock, the message of a stopped write) |
| `test_panel_settings.py` | the panel against a capture: tabs, All settings table, knob, DMC layouts |
| `test_settings_view.py` | settings view values against reference values |
| `test_knob_cal.py` | knob calibration |
| `test_mk2_emu.py` | emulator replies against captured frames; importing the emulator must not read the command line |
| `test_mk2_survey.py` | the summary logic of the survey recorder and a short run against the built-in emulator |

## Reference data

- `data/reference_settings_read.log`: a capture of reading all settings (the source of the emulator).
- `data/reference_settings_write.log`: a capture of a settings write session.
- `knob_calibration.json`: the measured knob to mains current calibration.

## Packaging a release

```bat
python scripts\build_release.py
```

checks the tracked files for leftovers that must not be published (private addresses, personal paths, passwords,
images with EXIF/XMP metadata, non-English text) and writes `dist\phoenix-multiplus-ve9bit-mk2-<version>.zip` with the program, documentation,
the Venus install scripts and `PhoenixConfig.bat`. The version is in the file `VERSION`.

Personal identifiers (serial numbers of your own equipment, host names, user names) are not kept in the repository.
Put one regular expression per line in `.release_blocklist` in the project folder (ignored by git); the check then
also refuses files that match them, including the recordings in `data/`. Local recordings (`survey_*.jsonl`,
`survey_*_summary.txt` from `mk2_survey.py`, `capture_*.log`) are ignored by git and refused by the check if they
are ever added; only the two reference logs in `data/` are published. Strip image metadata before adding
screenshots, e.g. `exiftool -all= docs/images/*.jpg`.

## Conventions

- Code, comments and messages are in English.
- Settings can only narrow the safety limits that are built into the program, never widen them.
- Anything that sends to the device goes through the whitelist in `mk2_link.py`.
- Control commands (knob, switch) go through `MK2.control_block()` (monitor only, firmware lock); settings writes
  and reset through `MK2.firmware_block()`. Do not bypass them.
- Findings are labelled **verified** (tested on the real device) or **inferred** in [protocol.md](protocol.md).
