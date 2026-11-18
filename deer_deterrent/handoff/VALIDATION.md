# Handoff validation — November 1, 2026

- 1,047 local Markdown/HTML links resolved at validation, including original gallery links.
- All 175 curated JPEGs passed their stored byte-length and FNV-1a checks.
- All three USB photo transfer unit tests passed.
- Git whitespace check passed.
- Last normal firmware build/upload and embedded motion/approach selftests passed October 31; recorded indoor tests are linked from tests/README.md.
- Runtime firmware source was not changed by this November 1 documentation pass; no reflash or hardware retest was performed.
- Archived Arduino camera prototype was not rebuilt; its prior demo AP password is replaced by a sample value.
- Current code and model attribution retained. Build/download caches and generated files excluded.
- Battery operation is user-confirmed. On the MVP program the unit draws about 200 mA idle, 320 mA in a capture and 120 mA in the alarm, about 2 hours on the 450 mAh cell; outdoor performance is unmeasured here.

When extracting the shared-drive ZIP, retain the directory structure. The full package includes original Lab 5 for historical links. See REPO_REVISION.txt in the ZIP for the exact committed snapshot.

## Recognition, laptop and night programs

These host checks and target builds cover the programs in `experiments/deer-classifier`, `experiments/camera-wifi-bringup` and `firmware/night-control` and the tools. They establish what the software does in the tested cases, not behaviour on the board. Run from `deer_deterrent` unless noted. The three C++ tests of the experiment folders used Apple clang 21 with `-std=c++17 -Wall -Wextra -Werror -pedantic -fsanitize=address,undefined` (drop the sanitizer flags on a compiler without them); the night-controlled detector's suites build with strict warnings and the undefined-behaviour sanitizer only, as its [build note](../firmware/night-control/BUILD-STATUS.txt) says.

| Check | Result |
|---|---|
| Recognition trial controller, `experiments/deer-classifier/tests/test_trial_control.cpp` | Passes: scheduling, confirmation isolation, cancellation, arming before sensor trials, the kept 120 s pause, recording level, capture light, output test |
| Decision to alarm, `experiments/deer-classifier/tests/test_decision_to_alarm.cpp` | 15 cases with expected results fixed in the file, each run 10 times; all match. Injected scores through the two-of-three rule, and the trigger `TEST OUTPUT` injects |
| Laptop reply gate, `experiments/camera-wifi-bringup/tests/test_reply_gate.cpp` | Passes: two of the last three verdicts in capture order, one response per burst, expiry, conflicts, cancellation, timer wraparound |
| Tool tests, `python -m unittest discover -s tools -p "test_*.py"` (Pillow from `tools/requirements-classifier.txt`) | 105 tests pass: detector recorder and replay, recognition recorder, laptop client with an emulated board, a device that stops answering and stand-in model libraries |
| Night-controlled detector host suites ([its guide](../firmware/night-control/README.md)) | 1,214 production checks; independent review 10,437 core checks, 1,176 sunrise/sunset crossings, 42 handoff and 46 service checks; 79 control-page assertions |
| Recognition trial program, `idf.py -B build -D IDF_TARGET=esp32s3 build` in its folder, ESP-IDF 5.4.2 | Builds: `deer_recorded_trials.bin` in a 4,128,768-byte factory partition |
| Laptop interface program, `pio run` in its folder (espressif32 7.1.0, Arduino-ESP32 2.0.17) | Builds and links |
| Night-controlled detector, normal and diagnostic builds | Both build and fit their partition |

The trial program's source as packaged (commit `13ccee3`), built with ESP-IDF 5.4.2 outside a git checkout, reproduces the three flash images of the [recorded recognition trials](../tests/recognition/README.md) byte for byte except the compile date and time and the checksums that cover them. The night-controlled detector has not been run on the unit, and no record shows the laptop interface program run on it.
