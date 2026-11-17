# Recording and replay tools

Use these tools to retain serial evidence, check photo transfers, and replay compatible detector windows through the firmware's shared C++ growth gate. `record_classifier.py` records the recognition trial program's sessions and `laptop_classifier.py` runs the laptop side of the laptop interface program. The tools do not estimate independent accuracy, identify animal species, or verify physical sound/light output from command messages.

## Requirements

- Python 3.10 or later.
- A C++17 compiler with GCC/Clang command-line options for replay. `--compiler /path/to/c++` selects a compiler; otherwise `CXX`, then `c++`, is used.
- `pyserial` only for live recording: `python -m pip install -r tools/requirements.txt`.
- Pillow for the recognition recorder, the laptop client and their tests: `python -m pip install -r tools/requirements-classifier.txt`, which includes `requirements.txt`.

Run the examples from the repository root. Replay and automated checks do not require a board, PlatformIO or network access.

## Analyze retained recordings

```sh
python tools/replay_records.py tests/captures --output tests/replay_verification/corpus.json --markdown tests/replay_verification/corpus.md
```

Inputs can be session directories, `events.jsonl` files, or parent directories containing sessions. Multiple inputs are accepted; the same resolved session path is processed once. Outputs must be outside every input session directory. Existing inputs are read without modification. Directory names and timestamp-like filenames are identifiers, not evidence of chronology between sessions.

The CLI compiles `tools/gate_replay.cpp`, which includes `firmware/motion-size/src/approach_gate.hpp`. No Python copy of the growth algorithm is used. It verifies saved JPEG lengths, FNV-1a checksums and start/end markers, matches metadata to event rows, and checks artifact hashes when a run manifest exists. These transfer checks do not decode or visually judge an image.

Replay uses the recorded largest-region area and bounding box. The original fixed background is not saved, so this is **region-metadata replay**, not a rerun of JPEG decoding, background subtraction, majority filtering, or connected-component extraction. A successful replay shows that retained region records agree with this gate revision; it does not prove which binary was flashed when a historical record was made. Gate, adapter and compiler provenance are included in the report.

A `PIR_TRIGGER` establishes a fresh window. `BURST_END` establishes its observed ending. Manual ARM, calibration, boot, capture failure, explicit burst aborts and changed control generations interrupt the relevant history. Routine CONTROL messages, idempotent pause/resume requests and small time corrections do not reset a window merely because of their event name. A host command request alone is not treated as a device acknowledgment. Photo-only records are retained separately; gallery order never supplies missing gate history. Missing/repeated frames, a changed control generation, invalid measurement data, or missing trigger history prevent exact replay of the affected window. Windows with older size-only metadata or a configuration incompatible with the current growth gate are explicitly skipped.

An explicit trigger generation fixes the expected generation for that window. When it is absent, the analyzer uses the last known generation after the most recent observed reset, or establishes it from the first generation-bearing control/frame record. The first frame is checked against that expectation as well as subsequent frames; a mismatch keeps the window incomplete and excludes it from exact replay. An unpaired bare reset clears a prior expectation. A reset carrying its new generation establishes that value, which its corresponding main-loop acknowledgment preserves in either message order. A delayed frame from an aborted capture cannot overwrite a newer control generation. Calibration metadata describes its capture ticket, which may precede the generation change when reference learning completes. Entirely legacy recordings without generation fields remain readable and use their observed trigger, frame sequence and ending.

| Window status | Meaning |
|---|---|
| `complete` | Trigger and ending observed; the declared frame count matches the valid retained sequence. |
| `truncated` | A trigger was observed, but recording ended before a closing event. Ten retained frames alone do not establish a completed burst or cooldown. |
| `aborted` | An observed reset, failure, cancellation, or replacement trigger interrupted the window. |
| `incomplete` | An ending was observed, but trigger history or sequence integrity is missing. |

A valid partial prefix can be replayed and reported as `matched_observed_prefix`. It is never promoted to a complete window. A complete compatible window may be `matched_complete_window`; either can be `mismatch`. Skipped windows include an explicit reason. Records outside an observed window remain visible but are not replayed. Cooldown expiry, retriggering and physical output duration cannot be inferred from a truncated record or a last status heartbeat.

Exit codes are `0` for analysis completed without integrity/application errors or replay mismatches, `1` when such problems are found, and `2` for invalid inputs or setup/native-adapter failure. Warnings, SDK `E (...)` diagnostics, skipped windows and partial recordings remain explicit even with exit `0`; that exit code is not a performance result.

## Make a new recording

```sh
python tools/record_photos.py --port COM5 --seconds 60 --label approach --output tools/captures/approach-01 --notes "Target and setup; state whether the buzzer is connected"
```

Use the appropriate serial port on your system, for example `/dev/ttyACM0`. The output directory must be new. The recorder does not clear the serial input or discard its first line. Attaching during a photo or stopping during a serial line is recorded explicitly. At the duration limit, a pending photo gets up to five extra seconds to finish.

Optional actions are mutually exclusive:

```sh
python tools/record_photos.py --port COM5 --seconds 30 --label baseline --calibrate
python tools/record_photos.py --port COM5 --seconds 60 --label controlled-approach --arm
```

`--calibrate` sends `BASELINE`; clear the camera view before using it. `--arm` sends `ARM` for a controlled observation. Device pause, clock and night policy still govern whether it can arm. Check the separate device acknowledgment and policy state. Neither option describes an independent trial by itself.

`--config-file path` and `--firmware-binary path` record hashes of supplied files. They do not apply a configuration, flash a binary, or establish the identity of the running binary. `--notes` stores operator-provided setup context. Available source files are hashed locally; the manifest explicitly marks flashed-binary identity as unverified.

Each session contains:

| File | Content |
|---|---|
| `serial.raw` | Exact received bytes, including base64 transfers, SDK diagnostics, invalid text and unfinished fragments. Host commands are represented separately in the event log. |
| `events.jsonl` | Device MOTION objects, device-message rows, and explicit recorder events with host receipt metadata. |
| `frame-*.jpg` / `frame-*.json` | Verified transferred bytes and matching metadata. Repeated frame IDs get a `-copy-NNN` suffix rather than replacing earlier files. |
| `summary.json` | Schema 2 measurement, diagnostic, command and window summaries. |
| `run_manifest.json` | Recorder settings, operator notes, local/config/binary hashes, observed configuration/identity messages, close status, and SHA-256/length for saved artifacts. |
| `index.html` | Local gallery with metadata links and bounding boxes. Setup/diagnostic values are labeled as placeholders. |

If an `ACCESS` command is deliberately recorded, `serial.raw` and `events.jsonl` contain the setup credentials exactly as received. Review captures before sharing them. ACCESS responses are not copied into manifest identity messages; existing evidence is never silently redacted.

A normal close updates the manifest and hashes the final artifacts. An interrupted process or failed disk write can leave an open manifest or `.part` files; preserve that directory for diagnosis. Ctrl+C finalizes what can be retained and exits `130`. Integrity/application errors exit `1`; SDK diagnostics are reported separately. A disconnected cable or an application capture failure remains part of the record, not an empty successful run.

## Schema 2 and legacy compatibility

The reader accepts existing records without schema markers. Historical `summary.json` files remain unchanged; their old error list is carried forward as warnings. Embedded `MOTION` text inside a device-message row is flagged without inserting an assumed measurement into a sequence.

New event rows retain device fields and unknown future fields. `_host` contains a monotonically increasing `sequence`, `received_utc`, elapsed monotonic receipt time, and zero-based `[raw_start, raw_end)` byte offsets into `serial.raw`. Recorder-only events have `recorder_event`, including `HOST_COMMAND`, `PHOTO_BEGIN`, `PHOTO_SAVED`, `ERROR`, `PHOTO_FRAGMENT`, `TRAILING_FRAGMENT` and `RECORDING_END`. Rows that are not associated with received bytes have null offsets. A photo's `motion_event_sequence` identifies the matching MOTION receipt when one was observed.

When present, the Boolean `deterrent_started` reports the firmware alarm-start result after gate qualification. A `LARGE_CHANGE` frame can report `false` if cancellation denies the start. `BURST_END large_confirmed` is checked against that result; historical records without the field use their terminal gate state. A true value on a nonqualifying frame is invalid. This field does not measure physical output.

Host receipt times include serial transfer and processing delays. They are not exposure timestamps. Firmware fields such as `capture_uptime_us`, `decision_uptime_us`, `utc_capture_us`, `clock_valid` and `control_generation` are retained when supplied. UTC is not considered valid merely because a nonzero field or a timestamp-like filename exists.

Schema 2 intentionally replaces ambiguous legacy summary names:

| Summary field | Meaning |
|---|---|
| `counts.detector_frame_records` | Only actual detector states: CLEAR, SMALL_CHANGE, CONFIRMING, WAITING_FOR_GROWTH, LARGE_CHANGE. |
| `counts.calibration_frame_records` | SETTLING / CALIBRATING placeholders, excluded from detector area and state summaries. |
| `counts.diagnostic_frame_records` | CAMERA_TEST records, excluded from detector area and state summaries. |
| `counts.alarm_start_messages` / `alarm_stop_messages` | Observed software command messages; not counts of verified physical activations. |
| `counts.sdk_error_diagnostics` / `sdk_warning_diagnostics` | Preserved SDK `E (...)` / `W (...)` lines, separate from application ERROR messages. |
| `detector_state_counts.LARGE_CHANGE` | Frames explicitly assigned that state; not every frame over a cutoff. |
| `last_observed_status` | Last heartbeat and how many later event rows followed it; not necessarily the final state. |
| `window_counts` / `windows` | Observed qualification windows and their completeness; not independent trials or accuracy denominators. |

`STATUS deterrents=...` is a cumulative firmware counter, not a Boolean indicating an active output. `CAMERA_TEST` zero area and its reported cutoff are diagnostic placeholders. Summary consumers must select a schema version; schema 1 aliases are not emitted with changed semantics.

## Automated verification

```sh
python -m unittest discover -s tools -p 'test_*.py'
```

The suite runs 107 tests and needs Pillow. The detector tests cover transfer corruption, exact raw-byte retention, partial transfers, duplicate frame IDs after boot, invalid metadata, manifest corruption, missing trigger/end/frame history, first-frame generation continuity, delayed canceled captures, ARM/calibration/policy boundaries, legacy configurations, and native-gate agreement/mismatch. The C++ replay tests require `c++`; the suite explicitly skips them if it is absent. The recognition recorder and laptop client tests are described below. These are host-side software tests. Use the retained corpus command above to check real saved records as well.

## Neural recognition recordings

Use `record_classifier.py` with the recognition-trial firmware. It saves the original inference JPEG, the exact pre-run INT8 input, boxes, scores, settings and trial/controller state. Install `requirements-classifier.txt`. See the [recognition guide](../experiments/deer-classifier/README.md) for commands, files and board checks, and [tests/recognition](../tests/recognition/README.md) for the recorded sessions.

```sh
python tools/record_classifier.py --port COM5 --output tools/captures/d1-full-body-01 --label "D1 full body" --notes "print size, distance, lighting"
```

A session first records the board's `STATUS` reply in its manifest and then sends `STOP`, so it starts manual, disarmed and idle; `--keep-state` records without the `STOP`. `MARK description` writes a marker tied to the frame received before it. `QUIT` sends `STOP`, waits for an outstanding frame transfer and a quiet moment, and closes the session. A lost serial port is recorded as an error, and the session still closes with its summary and gallery. Each command is logged after it was sent.

`LIGHT AMBIENT` and `LIGHT WHITE` select the next idle trial's capture lighting, and `RECORD FULL` or `RECORD JPEG` whether each frame's exact model input is sent; both appear in each frame record and gallery card. `TEST OUTPUT`, sent while manual, idle and disarmed, injects the alarm trigger a confirmed deer result sends. Its start and completion/cancellation events are recorded in their own summary counts and gallery section, without generating an image or score. A missing terminal event stays incomplete. Record physical observations separately from the controller's reported events.

The summary keeps recording errors apart from warnings (a box list it cannot check; the frame is kept) and from device log diagnostics (ESP-IDF `E (...)`/`W (...)` lines). A failed transfer gives one error, a transfer cancelled by `STOP` is counted as cancelled, and `trials.csv` lists each trial's frames, highest score and decision. The gallery draws each record's detection boxes and scores over the unchanged JPEG. The manifest is closed with the board's state at the start, the firmware seen, the model hash and the size and SHA-256 of every saved file. The tests cover byte-exact pairing, damaged and cancelled transfers, the recording level, `TEST OUTPUT`'s events, device log lines, a lost port, `QUIT` and the opening state over a stand-in port, markers, the trial table, drawn boxes and the closing manifest.

The practical `record_photos.py`/`replay_records.py` pipeline handles changed-area/growth records. Neural scores and tensors are not motion percentages; the two protocols and analyses are kept distinct. Both retain raw bytes and explicit incomplete/error records.

## Laptop recognition and verdict return

`laptop_classifier.py` works with the [Wi-Fi laptop-interface firmware](../experiments/camera-wifi-bringup/README.md). It downloads a retained camera JPEG, verifies its size/checksum, runs the existing one-class ESPDet float checkpoint on the computer and posts a verdict bound to the boot/burst/photo identity and checksum. Two deer verdicts among the last three photos confirm, on the board and in the client, which applies `experiments/deer-classifier/temporal_gate.py`. It saves the original JPEG, model/helper hashes, score/boxes, settings, timing, its confirmation and the device acknowledgment. A photo whose download, check, score or reply fails is recorded with its error and passed over; a controller acknowledgment does not prove physical actuation.

Use the model repository's existing compatible Python environment and supply the trained `.pt` checkpoint, its `deer_classifier` directory and the ESP-Detection checkout. No model is downloaded automatically. `--arm` permits responses and `--capture` requests a manual burst once the board is ready and not paused; otherwise PIR starts acquisition. The default is disarmed, with a stop/disarm request on exit. See `python tools/laptop_classifier.py --help` for arguments. This network client is distinct from `record_classifier.py`, whose model runs on the ESP32 and transfers exact INT8 inputs over USB. Its tests run `main()` against an emulated board over loopback HTTP and the predictor's own checks with stand-in model libraries; none runs the trained checkpoint, which is in neither repository.
