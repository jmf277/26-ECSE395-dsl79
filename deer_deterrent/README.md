# Deer deterrent MVP

**Start with [the team handoff](handoff/START_HERE.md).** Current device: Adafruit ESP32-S3 TFT Feather + OV5640 + PIR + RGB, with optional passive buzzer. The MVP program was last flashed October 31, 2026; handoff organized November 1. On November 4 the recognition trial program was flashed for the [recorded recognition trials](tests/recognition/README.md).

| Location | Purpose |
|---|---|
| [handoff](handoff/START_HERE.md) | Current state, findings by part, runbook, known issues and outdoor-test template |
| [firmware/motion-size](firmware/motion-size/README.md) | Current 10%-plus-growth firmware and optional camera diagnostic |
| [firmware/night-control](firmware/night-control/README.md) | The same detector with automatic night operation, network time and a saved pause; written and tested on a computer, never run on the unit |
| [experiments/deer-classifier](experiments/deer-classifier/README.md) | Recognition trial program: the deer model on the board with recorded manual and sensor-started trials, the light-and-buzzer response and an injected detection; earlier score logs and failed recognition result |
| [experiments/camera-wifi-bringup](experiments/camera-wifi-bringup/README.md) | Laptop interface program: camera bursts over Wi-Fi, classified on a computer by `tools/laptop_classifier.py`, with the same response |
| [shared](shared/alarm_pattern.hpp) | One definition of the light-and-buzzer response (waveform, pins, tone) for the recognition, laptop and night programs; the MVP program keeps its own copy of the same values |
| [tests](tests/README.md) | Test results, original local photo galleries, recorded recognition trials and replay results |
| [tools](tools/README.md) | USB recorders for the detector and the recognition trials, replay analysis, the laptop client, requirements and tests |
| [WIRING.md](WIRING.md) | Current wiring; replaces older prototype diagrams |
| [SOURCES.md](SOURCES.md) | Attribution and evidence provenance |

Current trigger: PIR starts up to ten photos; five spatially associated frames must show at least 25% net growth and two percentage points of image-area gain, with the last three at least 10% of the image. At least two growth steps must exceed the per-step floor. Confirmation produces 5.5 seconds of white flashes and the buzzer signal. No timed cooldown follows confirmation; unconfirmed windows pause two minutes. This is an approaching foreground-region heuristic, **not deer recognition**.

Stationary large object rejected; approaching object triggered; visible small-object repeat rejected in recorded indoor trials. User confirmed battery-only operation. On the MVP program the unit draws about 200 mA idle, 320 mA in a capture and 120 mA in the alarm, about 2 hours on the 450 mAh cell. Outdoors, on its battery, a person standing about 4 m away drew capture flashes and no alarm and a person walking up from about 3 m drew the alarm; outdoor accuracy against deer has not been measured. The buzzer was physically unplugged during the later tests, though the firmware still drives GPIO10. TFT and Wi-Fi are disabled in current firmware.

Download/clone the repository, then open `tests/index.html` locally for the detector's saved sessions; each recorded recognition session has its own gallery. GitHub displays HTML source rather than running the gallery. Existing Lab 5 and the collaborator's upstream repository are preserved.
