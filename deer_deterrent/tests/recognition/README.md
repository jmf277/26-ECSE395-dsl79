# Recorded recognition trials, November 4, 2026

These ten sessions were recorded on the evening of November 4, 2026 with the [recognition trial program](../../experiments/deer-classifier/README.md) on the assembled unit. Recorder and file times are UTC; the sessions ran from 19:27 to 22:21 Eastern time. The unit was the printed base with its inside rebuilt so the USB cable could reach the controller, cover off, powered over USB with the battery out. Printed pictures stood in front of the camera, and a computer on USB sent the commands and kept the records.

Each session folder holds what the recorder saved: `serial.raw` (every byte received), `events.jsonl` (the device's lines and the commands sent, with the recorder's own entries for each saved file, transfer check and recording error; the notes typed during the session are not included), `summary.json`, `run_manifest.json`, an offline `index.html` gallery, and for each frame the JPEG the model scored (`.jpg`), its exact INT8 model input (`.input.i8`), a dequantized view of that input (`.input-preview.png`), the device's record (`.json`) and the transfer check (`.received.json`). Open a session's `index.html` from a downloaded copy of the repository. Replaying a session with the current recorder rebuilds the same pictures, inputs and records and draws each record's boxes over its picture:

```sh
python tools/record_classifier.py --replay tests/recognition/p3-demo-20261104-02/serial.raw --output tools/captures/p3-demo-replay
```

## Program, image and recorder

- **Image.** [firmware/](firmware/) holds the three images flashed for these sessions, with their [SHA-256 checksums](firmware/SHA256SUMS): `bootloader.bin` (20,784 bytes), `partition-table.bin` (3,072 bytes) and `classifier.bin` (1,902,736 bytes: project `deer_camera_smoke`, compiled `Nov 4 2026 16:48:41`, ESP-IDF v5.4.2, ELF SHA-256 `7145d186b221970fa84c8108b9cb43b06aa8813ac443f3c646a07eb60e67be60`). Every session's boot lines show that compile time, ELF prefix `7145d186b` and a partition table equal to `partition-table.bin`, and report the embedded model (499,712 bytes, SHA-256 `f4baa400…`), a passed PSRAM memory test and gate self-test and, in every session where the camera started, a passed inference self-test. The three images were written at `0x0`, `0x8000` and `0x10000` (DIO, 4 MB, 80 MHz) after their SHA-256 checksums had been verified, and the flashing tool verified each write. The board reported an ESP32-S3 revision v0.2 with 4 MB flash and 2 MB PSRAM.
- **Source.** The program's source as it ran is commit `13ccee3` ("Add the recorded recognition trial program and the laptop recognition path"). Built with ESP-IDF 5.4.2 outside a git checkout, that tree reproduces all three images byte for byte except the compile date and time and the checksums that cover them; inside a checkout ESP-IDF takes the program's version from git, while the flashed image's version is 1. Later commits add fixes, so the current source does not rebuild these images.
- **Recorder.** `tools/record_classifier.py` as of that commit; its SHA-256, `7337bd0d…`, is in every session's `run_manifest.json`.
- **Settings, the same in every session.** Threshold 0.85, with two qualifying frames among the last three valid frames to confirm; 320×240 JPEG at quality 12, letterboxed to the 224×224 model input with padding 114; box floor 0.10, overlap 0.70, at most 10 boxes; 500 ms pause after each recorded frame; CPU 160 MHz. Neither mirror nor flip was set, so every picture is a mirror image of the scene. From session 06 on the camera board was upside down, so the room appears upside down, and the prints were placed to read upright to the camera.
- Opening the recorder reset the board, so every session begins with the board's boot lines and the 60-second sensor warm-up started again.

To put these images back on a board, from `tests/recognition/firmware`, check the images against their checksums, keep a copy of what the board holds (use the board's actual port), then write them; the flashing tool verifies each write:

```sh
shasum -a 256 -c SHA256SUMS
python -m esptool --chip esp32s3 --port COM5 read_flash 0 0x400000 board-before-flash.bin
python -m esptool --chip esp32s3 --port COM5 --before default_reset --after hard_reset write_flash --flash_mode dio --flash_size 4MB --flash_freq 80m 0x0 bootloader.bin 0x8000 partition-table.bin 0x10000 classifier.bin
```

The read-back file holds everything on the board, its saved settings and any Wi-Fi password among them; git ignores it, and it belongs in no shared folder. `python -m pip install "esptool>=4,<5"` installs the flashing tool, and on Windows PowerShell `Get-FileHash -Algorithm SHA256 <file>` prints the checksums `shasum -a 256` checks.

## What the trials set out to do

Three deer photographs (D1, D2, D3) and one non-deer photograph (N1, a dog) were each printed on a sheet of about 8.5 × 11 inches. The series run was: five armed sensor trials with the white capture light (D1, D2, D3, N1, and D1 closer), each started by the operator's movement beside the unit; one manual, disarmed, continuous trial of 18 frames, empty view, then D1, then empty view again, six frames each, with no command between the phases; and, since no armed trial started the response, one output test, `TEST OUTPUT` injecting the alarm trigger a confirmed deer result sends. The threshold and the two-of-three rule stayed fixed, and every miss was kept. A blank-sheet control, a matched ambient-against-white pair, closer views of D2 and D3, and checks of the 120-second pause, of the startup warm-up and of cancellation were not part of this series.

## The sessions

| Session | Start (UTC) | What it is | Result |
|---|---|---|---|
| `setup-check-20261104-01` | 00:27:48 | First start after flashing | The camera failed to start: repeated timeouts on its control bus, probe error `0x106`, `ERROR camera_init`, no `READY`, no capture. The camera's 3V connection was then fixed. |
| `setup-check-20261104-02` | 00:40:25 | Retry after the 3V fix and a check of the other connections | The bus timeouts were gone; probe error `0x106` (`Detected camera not supported`) and `ERROR camera_init` remained. A full USB and battery power cycle and a check of the camera's clock jumper followed. |
| `setup-check-20261104-03` | 00:49:51 | Retry on USB only, battery disconnected; the clock jumper was on EXT | Same failure. |
| `setup-check-20261104-04` | 01:09:13 | After a row of pins placed one position off, starting at SCK, was corrected and the MOSI, MISO, RX and TX connections checked | The OV5640 answered (PID `0x5640`, address `0x3c`) and the program reached `READY`. One ambient capture of the room, mostly dark: score 0.1192. |
| `setup-check-20261104-05` | 01:12:02 | Setup retake | Picture sideways with strong lamp glare; score 0. |
| `setup-check-20261104-06` | 01:15:16 | Setup retake | Picture upside down with less glare; score 0.1192. |
| `setup-check-20261104-07` | 02:31:17 | Reference picture before the prints, which were to stand about 3 feet away | No sheet in view, the room upside down; score 0.3775 from four boxes on the round ceiling light. |
| `setup-check-20261104-08` | 02:36:02 | D1 held by hand, about 9 inches away | The deer upright on the sheet, a hand in view; score 0.4378 on the ceiling light, with a box of 0.148 on the deer. |
| `d1-practice-20261104-01` | 02:40:02 | D1 with the white capture light, disarmed | Score 0.1192 on the deer's lit rear body; the operator saw the capture light turn on briefly and then off. |
| `p3-demo-20261104-02` | 02:50:54 | The main session, boot `a60fdd6cc327c384` | 68 frames in six trials and one output test; 68 complete picture-and-input pairs, no invalid inference, no incomplete frame, no recording error. |

An earlier main session, `p3-demo-20261104-01`, which is not among these (it was closed and deleted at the operator's request so that D1 could start again cleanly), began with a D1 run that started at a rise of the motion sensor 0.6 seconds after `MODE PIR`, before `ARM` could be sent. From then on the procedure sent `ARM` at once after `MODE PIR`, as in every sensor trial of the main session, and a later fix makes the program start a run only when it is armed. The three failed start-ups' summaries list 38, 4 and 4 errors: all but one in each are the board's own log lines of the camera failure, and the last is `ERROR camera_init`. The current recorder counts such log lines as device diagnostics.

### The main session

| Trial | Frames | Print | Lens to print | Start | Armed | Scores | Light and buzzer observed |
|---:|---|---|---|---|---|---|---|
| 1 | 1–10 | D1 | about 5 in | motion sensor | yes | 0.148–0.321 | capture flashes, no buzzer |
| 2 | 11–20 | D2 | about 5 in | motion sensor | yes | 0–0.119 | capture flashes, no buzzer |
| 3 | 21–30 | D3 | about 5 in | motion sensor | yes | 0–0.095 | capture flashes, no buzzer |
| 4 | 31–40 | N1, the dog | about 5 in | motion sensor | yes | 0–0.095 | capture flashes, no buzzer |
| 5 | 41–50 | D1 closer | 2.5 in | motion sensor | yes | 0 in all ten | not recorded |
| 6 | 51–68 | empty 51–56, D1 57–62, empty 63–68 | about 5 in | `CAPTURE 18` | no | empty 0.5, 0.5, 0.5, 0.5, 0.5, 0.5; D1 0, 0.119, 0.119, 0.095, 0.119, 0.148; empty 0.5, 0.438, 0.5, 0.5, 0.438, 0.5 | not recorded |
| output test | none | none | | `TEST OUTPUT` | no | none | three buzzer pulses and flashing, then both stopped |

The distances were measured with a ruler. All 68 frames used the white capture light, and every frame was valid and committed. The operator filmed each trial and, separately, the output test. Each sensor trial followed `MODE PIR`, `ARM` and `STATUS` and was started by the operator's movement beside the unit, which the motion sensor picked up 1.0 to 3.6 seconds after `MODE PIR`; its frames carry `trigger: pir`. After each trial `STOP` returned the program to manual, disarmed and idle. In trial 6 the phases were located from the pictures. The output test was run disarmed in manual mode: its `OUTPUT_TEST` events, started and completed, are 5.519 seconds apart by the board's clock, no picture was taken, and the status line named its source `output_test`.

## What they showed

- No frame reached 0.85, so no decision confirmed and no armed trial started the response. The highest score was 0.5, in the empty view of trial 6; the highest in an armed trial was 0.321.
- The highest boxes mostly sit on the round ceiling light, which appears low in the upside-down picture: every box of trial 1 is on it, and the empty views of trial 6 score 0.5 on it. When the sheet hid the light, as in trial 5, where D1 at 2.5 inches fills the picture with its antlers partly beyond the top edge, there were no boxes at all. Boxes on a deer reached 0.148 (D1, frame 62, on its lit torso) and 0.095 (D3's head, frames 21 and 22); no box fell on the dog. In the pictures of the prints taken with the white capture light, the light leaves bright, colored patches across the sheet.
- Frames within a trial are repeated looks at one scene, about 2.1 seconds apart (0.9 seconds of it the USB transfer, 363 ms the model), not independent trials. Without a confirmed state, trial 6 cannot show the history clearing after a confirmation.
- The output test shows the light and buzzer running the shared 5.5-second sequence on the rebuilt unit from the injected trigger; it took no picture.
