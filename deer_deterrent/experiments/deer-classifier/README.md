# On-device deer recognition: recorded trials

The current MVP is [firmware/motion-size](../../firmware/motion-size/). This folder holds the recognition trial program: the compact deer-box model runs on the ESP32-S3 with the assembled OV5640 camera, a computer on USB starts trials and records each frame the model scored, and an armed, confirmed decision starts the same light-and-buzzer response. The [recorded trials of November 4](../../tests/recognition/README.md) were made with this program. Flashing it replaces the MVP program on the board.

## Earlier score-only run

The ESP32-S3 ran the 499,712-byte ESP-DL deer model with observed inference around 378–379 ms. Recognition did not pass the demonstration: neither the full-body nor close-up deer-image run reached the 0.85 confirmation threshold. This prompted the pivot to large foreground-change detection. The cause of the misses was not established.

| User-presented view | Frames | Mean score | Max score | Confirmed detections |
|---|---:|---:|---:|---:|
| Full-body deer image | 33 | 0.2163 | 0.3775 | 0 |
| Close-up deer image | 33 | 0.0827 | 0.2227 | 0 |

Scores are not calibrated probabilities; framing was user-reported and exact model-input images were not independently saved/verified. This result does not show that all ESP32 deer classifiers are infeasible.

That run used the earlier score-only diagnostic, which printed scores and saved no pictures; its source is in this folder's history (for example at commit `d3e0cae`). [test-results](test-results/) keeps its raw score JSON and startup log. `model/` holds the unchanged model. Upstream attribution is in [SOURCES.md](../../SOURCES.md).

## Trial controls

The program starts in **manual mode with the alarm disarmed, ambient capture and full recording selected**. It uses the existing camera, PIR, RGB and buzzer wiring with the TFT disabled. USB to a running recorder must stay connected: without a USB host the program refuses captures, arming and the output test, and losing the connection cancels acquisition and any response. Commands are whole lines sent through `tools/record_classifier.py`.

| Command | Behavior |
|---|---|
| `CAPTURE 1` | Acquire and record one fresh image and its model result. A single independent image cannot satisfy the two-of-three gate. |
| `CAPTURE 33` | Start one bounded 33-frame trial; any count from 1 to 600 is accepted. Confirmation history is kept within the trial. |
| `MODE PIR` | Cancel the current trial, disarm and select sensor-started trials. Once armed, and after the 60-second startup warm-up, a new LOW-to-HIGH transition starts up to ten frames. |
| `MODE MANUAL` | Cancel the current trial, disarm and return to operator-started capture. |
| `LIGHT AMBIENT` / `LIGHT WHITE` | While idle, select capture without or with the white light. Selection applies to manual and sensor trials. |
| `RECORD FULL` / `RECORD JPEG` | While idle, send each frame's picture and exact model input, or the picture only. |
| `ARM` | While idle, permit a confirmed decision to start the physical response. A sensor rise starts a trial only once armed, so `ARM` follows `MODE PIR`. |
| `DISARM` | Cancel the current trial, capture light and any response or test, keeping the selected mode. |
| `STOP` | Cancel acquisition and outputs, disarm and return to manual mode. |
| `TEST OUTPUT` | While idle, disarmed and in manual mode, run the light/buzzer sequence alone. No picture, score or decision is made. |
| `INJECT DEER` | While idle, armed and in manual mode, inject a confirmed deer detection at the controller: the response starts through the decision's own branch, with no picture taken. |
| `STATUS` | Report mode, lighting, recording level, arming, acquisition, response and its source, the sensor level and the warm-up and pause time remaining. |

Starting a trial resets confirmation history. A frame qualifies at a maximum deer-box score of at least 0.85; two of the last three valid frames confirm presence. Invalid images or inference clear that history. Cancellation invalidates an in-flight result, so it cannot start an alarm afterward.

Lighting cannot change during a trial, in-flight transfer or response. With `LIGHT WHITE`, the LED turns on, waits 120 ms and stays on while two queued frames are discarded and the retained frame is acquired. It turns off before JPEG decoding, inference and transfer. A five-second watchdog also turns it off and rejects the attempt if acquisition stalls. Each record states the requested lighting and whether acquisition finished inside that window; these fields report the command, not scene brightness.

An armed confirmed decision, or `INJECT DEER`, starts three 1.5-second, 2 kHz tone pulses separated by 0.5-second gaps, with 5 Hz white flashing over 5.5 seconds, from [`shared/alarm_pattern.hpp`](../../shared/alarm_pattern.hpp). A decision ends the trial after its frame is recorded. An output timer runs every 20 ms independently of the serial transfer, so a transfer cannot lengthen the response, and no new frame is taken during it. In sensor mode another trial needs the sensor to go LOW and rise again after the response. A ten-frame window with no confirmation starts a 120-second pause; `STOP`, `DISARM` and mode changes do not clear it, and a sensor still HIGH when it ends may start another window. `PIR_STATE` lines record each change of the sensor level, the warm-up and the pause in sensor mode.

`TEST OUTPUT` runs the waveform alone; its `OUTPUT_TEST` events are recorded apart from frames. `INJECT DEER` checks the path from a confirmed decision to the response: it needs the same arming a real decision needs, its `INJECTION` started and completed or cancelled events carry the controller's alarm start time, and the status line names its source `injected_detection`. The step before the decision, scores through the two-of-three rule, is checked on a computer by `tests/test_decision_to_alarm.cpp`. Neither command is a recognition result.

## Build and flash

The image that made the recorded trials was built natively with ESP-IDF **5.4.2**, ESP-DL **3.3.14** and esp32-camera **2.1.3**. With ESP-IDF 5.4.2 activated, from this directory:

```sh
idf.py -B build -D IDF_TARGET=esp32s3 build
idf.py -B build -p COM5 flash
```

Choose the actual USB serial port, and read the board's firmware back first as [BOARD_CHECKS.md](BOARD_CHECKS.md) describes. The root CMake file registers `src/` as a component; its recipe embeds `model/deer.espdl` 16-byte aligned, as ESP-DL requires, records the model's size and SHA-256 in the program and stops if the model is missing. `dependencies.lock` pins ESP-DL, the camera driver and their dependencies; a build may rewrite its line wrapping without changing a pin. Keep this folder inside the repository, since it includes `shared/alarm_pattern.hpp`. The PlatformIO route through `build.ps1` and `platformio.ini` built the earlier score-only diagnostic and has not been built for this program.

The threshold is set at build time (`CONFIG_DEER_THRESHOLD_PERCENT=85` in `sdkconfig.defaults`); no command changes it. `CONFIG_DEER_CAMERA_HMIRROR` and `CONFIG_DEER_CAMERA_VFLIP` set the sensor's horizontal mirror and vertical flip. Both are off, the setting of every recorded trial, whose pictures are mirror images of the scene; neither has been tried on the unit. The CPU runs at 160 MHz and the program pauses 500 ms after each recorded frame. The generic camera profile in the [model-development repository](https://github.com/AyanmSheikh/F26-ECSE395-ams926/tree/main/deer_classifier) uses other pins; this program refuses camera pins that the assembled board uses for the PIR, buzzer, RGB and TFT.

## Record

```sh
python -m pip install -r tools/requirements-classifier.txt
python tools/record_classifier.py --port COM5 --output tools/captures/d1-full-body-01 --label "D1 full body" --notes "print size, distance measured with a ruler, lighting"
```

Run from the `deer_deterrent` folder, with other serial monitors closed. The output folder must be new; `tools/captures/` is ignored by git, and a session worth keeping is moved under `tests/recognition/` with an entry in its README. The recorder first records the board's `STATUS` reply in the manifest, then sends `STOP`, so each session starts manual, disarmed and idle (`--keep-state` records without the `STOP`). Commands are typed in its console. `MARK description` writes a marker tied to the frame received before it. `QUIT` sends `STOP`, waits for an outstanding frame to finish and closes the session. A lost USB connection is recorded as an error and the session still closes with its summary and gallery.

## What a record contains

Each frame has a boot, trial and frame identity linking:

- The **original JPEG actually decoded for inference**. Two queued frames are discarded before the retained capture so a frame from before the command is not used.
- With `RECORD FULL`, the **exact signed INT8 1×224×224×3 NHWC input**, copied after preprocessing and before model execution can reuse its memory. Its exponent defines `real = signed_byte * 2^exponent`.
- The maximum deer-box score, every postprocessed box in original-image pixels, confirmation, arming, commitment, the alarm command, the trigger, lighting, recording level and errors.
- The settings the program used: threshold and two-of-three rule, box floor 0.10, overlap 0.70, at most 10 boxes, JPEG quality 12, the 500 ms pause, camera clock, mirror and flip, and the firmware's project, version, ESP-IDF version, compile time and ELF SHA-256.
- Capture, JPEG decode, preprocessing, input copy, model and postprocessing times; transfer time is reported separately. In the recorded trials of November 4 the model took 363 ms, and with full recording frames were 2.1 s apart, about 0.9 s of it the USB transfer.

The recorder checks identity, order, byte lengths and FNV-1a checksums, decodes each JPEG to check its size, and saves per frame `<boot>-trial-NNNNNN-frame-NNNNNN.jpg`, `.input.i8` (authoritative tensor data), `.input-preview.png` (a dequantized view, not another photograph), `.json` (the device record) and `.received.json` (transfer completeness and hashes). Each session keeps `serial.raw`, `events.jsonl`, `summary.json` (counts, errors, warnings and device log diagnostics), `trials.csv`, `run_manifest.json` (label, notes, the board's state at the start, firmware seen, and the size and SHA-256 of every saved file) and an offline `index.html` that draws each record's boxes over the unchanged JPEG.

```sh
python tools/record_classifier.py --replay tools/captures/d1-full-body-01/serial.raw --output tools/captures/d1-full-body-01-replay
```

A replay rebuilds the recording from `serial.raw`; it does not rerun inference, and markers and host commands, which `serial.raw` does not hold, stay in the original `events.jsonl`. `smoke_check.py --replay` reads the score-only diagnostic's JSONL logs. The detector tools `record_photos.py` and `replay_records.py` read a different protocol.

## Model

The unchanged model is 499,712 bytes, SHA-256 `f4baa40014b3b5476e05e5007ab6e83a1a19bdc680c12dba86761841e02fc450`. QVGA JPEG is decoded to RGB and letterboxed to 224×224 with padding 114. These are diagnostic settings, not a validated recognition specification.

## Verification

```sh
c++ -std=c++17 -Wall -Wextra -Werror -pedantic -fsanitize=address,undefined tests/test_trial_control.cpp -o test_trial_control && ./test_trial_control
c++ -std=c++17 -Wall -Wextra -Werror -pedantic -fsanitize=address,undefined tests/test_decision_to_alarm.cpp -o test_decision_to_alarm && ./test_decision_to_alarm
python -m unittest discover -s ../../tools -p test_classifier_recording.py -v
```

Drop the sanitizer flags on a compiler without them. The controller test covers trial bounds, confirmation isolation, cancellation of in-flight results, arming before sensor trials, warm-up, rearming and the kept pause, the recording level, the capture-light watchdog, the output test and a response that outlasts a transfer. The decision test runs 15 cases with their settings and expected results fixed in the file, each 10 times: qualifying scores, 0.85 exactly and just below, hit-miss-hit, disarmed, an invalid frame, `STOP`, a sensor rise or `CAPTURE` during the response, a disarmed sensor rise, and `INJECT DEER` armed, disarmed, busy, stopped and repeated. The recorder tests cover byte-exact pairing, damaged and cancelled transfers, the recording level, injected detections, device log lines, a lost port, `QUIT`, markers, the trial table and the closing manifest. Host checks do not replace the assembled-board checks in [BOARD_CHECKS.md](BOARD_CHECKS.md).
