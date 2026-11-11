# Laptop deer-recognition and response trials

This program captures QVGA JPEGs on the ESP32-S3, serves them over its local Wi-Fi access point and accepts a computer's deer verdict tied to each retained image. Two deer verdicts among the last three photos confirm, as on the board's own recognition path; a confirmation stops acquisition and, when armed, starts the light/buzzer response. Recognition runs on the computer. It is separate firmware from the [recognition trial program](../deer-classifier/README.md) and the MVP detector; flashing one replaces the others. It takes the place of the earlier Wi-Fi camera bring-up program of this folder, whose TFT version remains in the repository's history.

## Hardware and build

Use the assembled connections in [WIRING.md](../../WIRING.md). The RGB channels are GPIO13, GPIO36 and GPIO35; the passive buzzer is GPIO10; PIR OUT is GPIO5. The TFT is deselected with CS7 HIGH and backlight45 LOW because its SPI pins are used by the RGB module. GPIO21 remains enabled for the board rail. The camera pins are those of the assembled MVP wiring.

From this directory, build/upload with PlatformIO:

```sh
python -m pip install platformio
pio run
pio run --target upload
pio device monitor --baud 115200
```

The project pins `espressif32@7.1.0` with Arduino-ESP32 2.0.17. It includes `shared/alarm_pattern.hpp` (the response's waveform, pins and tone) and the recognition program's `src/temporal_gate.hpp` (the two-of-three rule), so keep this folder inside the repository. No TFT graphics dependency is needed. Model weights are not stored on the board in this configuration.

Join the `Feather-Camera-Test` access point and open `http://192.168.4.1`. The password in the source is a sample value; set a local test password in `src/main.cpp` before use. The page provides capture, arm and stop controls, a current image and status.

## Operating sequence

The device starts disarmed. PIR warms up for 60 seconds; a LOW followed by a HIGH can start a burst. The manual capture button also waits for warm-up. A burst makes at most ten capture attempts, with a two-second gap after each attempt. The camera is initialized for each photograph, allowed to settle, and returned to standby afterward. Capture uses ambient light; the RGB is used for the response in this program.

A response must be armed before its burst. Arming invalidates any prior reply window; start a new burst afterward. Verdicts count in the order the photos were taken: two deer verdicts among the last three photos confirm, a photo left without a verdict when a later one is judged clears that history, and a photo passed over can no longer be judged. A confirmation stops acquisition whether armed or disarmed. If armed, it starts three 1.5-second 2 kHz tones separated by 0.5-second gaps, with 5 Hz white flashes over 5.5 seconds, once per burst. A dedicated 20 ms output timer controls the waveform independently of HTTP image transfers. No new capture starts during the response. PIR must return LOW before a subsequent rising trigger. A burst that finishes without confirmation with PIR HIGH, or a capture error, enters the 120-second capture pause.

`Stop and disarm` cancels acquisition and outputs and invalidates pending verdicts. The endpoint is processed by the main HTTP loop, so its receipt can wait for an in-progress camera acquisition or image transfer; the response's natural 5.5-second limit is independently timed. A lost Wi-Fi connection does not extend a started response. These are supervised local-network trials, without night scheduling or persistent pause.

## Laptop inference client

Use [tools/laptop_classifier.py](../../tools/laptop_classifier.py) in the existing model-development Python environment. It uses the model repository's `espdet_compat.py` and the compatible ESP-Detection checkout, following the project's float-checkpoint loader. Required inputs are:

- `--checkpoint`: the trained one-class deer `.pt` file, such as the model project's `artifacts/deer_espdet_pico/weights/best.pt`.
- `--model-project`: that repository's `deer_classifier` directory, containing `espdet_compat.py`.
- `--esp-detection`: the compatible Espressif ESP-Detection checkout used by the model environment.
- `--output`: a new session directory for the actual JPEGs, predictions and response receipts.

Run `python tools/laptop_classifier.py --help` from the repository root for all arguments. `--capture` requests one manual burst once the program is warmed up and not paused, retrying if the request is refused; otherwise the client processes PIR-triggered bursts. `--arm` permits physical responses. Startup first stops/disarms the board; it then arms only when requested. The client stops/disarms on normal exit, interruption or a processing error, recording any failed stop request. The default run lasts 180 seconds.

The client applies the same two-of-three rule with the repository's `experiments/deer-classifier/temporal_gate.py` and records each photo's confirmation beside the device's; a difference is recorded as an error. A photo that fails to download, check, score or be acknowledged is recorded with its error and passed over, without ending the session; a device reboot or about ten seconds without a reachable device ends it.

Use the model project's existing `requirements-espdetection.txt` environment, including Pillow. The model project is [Ayan's deer_classifier](https://github.com/AyanmSheikh/F26-ECSE395-ams926/tree/main/deer_classifier). The float checkpoint is excluded from Git; the client requires it explicitly and never downloads a substitute model.

Prediction defaults are square 224×224 input, `rect=False`, candidate floor 0.001, NMS IoU 0.70 and verdict threshold 0.85. The loader rejects a checkpoint without the one-class `deer` label. The client records the model/helper hashes, library versions and settings. NMS truncation, invalid scores, corrupt images and transfer mismatches produce errors instead of fabricated negative verdicts.

Each saved JPEG is the exact downloaded image passed to laptop inference. Its JSON record contains the boot/burst/photo identity, checksum and SHA-256, boxes, score, verdict, confirmation, inference time and the board's acknowledgment. An acknowledgment that a response started is a controller observation, not a recording of the physical light or sound. `events.jsonl` retains status and shutdown observations. The client does not overwrite existing sessions.

## Device protocol

| Route | Meaning |
|---|---|
| `GET /status` | Protocol version 3, boot/burst IDs, PIR readiness, acquisition, confirmation, arm/response and pause state. |
| `POST /arm` | Arm while no burst or response is active; close the prior reply window. |
| `POST /stop` | Stop and disarm; invalidate pending results. |
| `POST /capture` | Start a manual burst if ready and outside response/pause. |
| `GET /burst` | Current reply-window state and retained image IDs, sizes and FNV-1a checksums, in capture order. |
| `GET /photo.jpg?boot=…&burst=…&id=…` | Download that exact retained photograph; stale identities are rejected. |
| `POST /classification` | Form fields `boot`, `burst`, `id`, `fnv1a`, `deer=true/false`; return JSON acceptance, duplicate status, whether the burst is confirmed (`present`) and whether this request started a response. |

A new verdict must arrive within 60 seconds of its photograph. Results from another boot/burst, cancelled windows, unknown image IDs, photos already passed over and checksum mismatches are rejected. An exact repeated verdict is acknowledged without restarting the response. A contradictory verdict for the same image is rejected. New bursts invalidate the preceding burst's results. The client derives the image URL from validated identity fields instead of following arbitrary URLs supplied in a manifest.

## Verification and board checks

```sh
c++ -std=c++17 -Wall -Wextra -Werror -pedantic -fsanitize=address,undefined tests/test_reply_gate.cpp -o test_reply_gate && ./test_reply_gate
python -m unittest discover -s ../../tools -p test_laptop_classifier.py -v
```

The reply-gate test covers two of the last three verdicts in capture order, skipped and passed-over photos, arming, stale/unknown/expired/conflicting results, idempotency, cancellation, finite pulse timing and timer wraparound. The client tests cover image/result pairing, the rule and its agreement with the device, failed photos and replies, closed windows, a loopback HTTP exchange, `main()` against an emulated board (an armed confirmation, a pause, a refused capture) and the predictor's checks with stand-in model libraries. None of them runs the trained float checkpoint, which is in neither repository, or observes physical hardware; no record shows this program run on the unit.

On the assembly, first leave the response disarmed and verify the camera image, actual checkpoint loading and returned negative/positive scores. Then arm a fresh burst and pair an actual confirmation with observed light/buzzer operation. Check duplicate/late-result rejection and stop behavior. If the trained model never qualifies, record that outcome; a protocol test or independent output check does not establish a successful model-driven physical trial.
