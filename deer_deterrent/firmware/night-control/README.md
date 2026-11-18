# Night-controlled detector: PIR, foreground growth, network time and pause

This ESP32-S3 program runs the MVP's approach detector inside automatic night operation. It adds network time, a sunset-to-sunrise window, a pause that survives restarts, explicit reference learning and a local setup page. The detection rule is the MVP program's own: the growth gate `approach_gate.hpp` and the status display `status_display.hpp` are included from [firmware/motion-size/src](../motion-size/src/), so the rule exists once. The alarm waveform comes from [shared/alarm_pattern.hpp](../../shared/alarm_pattern.hpp), the definition the recognition programs use.

**Status: written and tested on a computer, never run on the unit.** It builds with ESP-IDF 5.4.2 and passes the host tests listed below. It has not been flashed to the assembled unit, so its network provisioning, pause, restart behaviour, clock handling, output timing and power use on the board are unchecked. The program the unit ran in the bench and outdoor tests is the MVP program in [firmware/motion-size](../motion-size/README.md), which this folder leaves unchanged.

## Setup and use

1. Build and flash the normal firmware using ESP-IDF 5.4.2 and the wiring in [WIRING.md](../../WIRING.md). No coordinates or network credentials are embedded in the source. A fresh device is unconfigured and paused.
2. Open its USB serial console at 115200 baud and send the exact command `ACCESS` followed by a newline. This explicitly prints its unique setup password. The password is not printed in ordinary startup or recording logs. Keep it private.
3. Join the WPA2 network `DeerSetup-<device suffix>`. Open `http://192.168.4.1`, using username `admin` and the same password. The access point remains available during normal operation; simultaneous station Wi-Fi supplies network time.
4. Enter the installed latitude and east-positive longitude, the WPA2 network name/password, and margins of 0–120 minutes after sunset and before sunrise. Save pauses the device and discards its reference. A blank Wi-Fi password retains the saved password only when the network name is unchanged.
5. Keep the view empty and select **Resume automatic operation**. After a fresh network-time response, the device waits for its night window and learns 12 illuminated reference frames. Its 60-second PIR startup warmup must also finish before a motion burst can start.

**Pause and hold** stops the white-light command and buzzer waveform, cancels any active check/alarm, and saves the pause. Only an explicit Resume clears a successfully saved pause. Daylight, time synchronization, `ARM`, and reference commands cannot clear it. The controller shows separate `UNCONFIGURED`, `PAUSED`, `WAIT_TIME`, `DAYTIME`, `SOLAR_UNAVAILABLE`, `NEEDS_BASELINE`, `CALIBRATING`, `ARMED`, `ALARM_ACTIVE`, and `FAULT` states.

**Relearn empty view** is an intentional setup operation: while enabled, it permits 12 reference captures with white illumination even during daytime or without valid time, starting after at least three seconds. It cannot trigger an alarm. It is blocked while paused. Ordinary startup reference learning remains gated by valid time and night. Pause, configuration changes, or a changed control generation discard a partial reference; a completed reference survives pause/resume until configuration, recalibration, or restart.

On an ordinary restart, a previously enabled, configured device stays inhibited until a new network-time response and required night/reference conditions. It does not require a person to Resume again. A saved pause remains paused. No trusted time or reference image is restored from flash.

A storage read/write failure latches outputs off for the current boot and reports an error; NVS is never automatically erased. A failed pause save is **not** reported as a durable pause. If flash cannot accept any write, complete power loss can leave an older enabled record; resolve that fault before relying on restart persistence.

The page uses HTTP Basic authentication, a per-boot request token, and POST-only JSON changes. Wi-Fi protects the setup link; HTTP has no separate TLS layer. Use a trusted local network. Credentials are stored in NVS, not encrypted against physical flash access. The always-available access point and time synchronization add to the power load, which has not been measured.

## Time and night policy

Network time uses `pool.ntp.org` and `time.cloudflare.com`, immediate synchronization, startup/reconnect requests, hourly updates, and a refresh within five minutes before the next configured evening boundary. The time source is ordinary unauthenticated SNTP. A plausible wall-clock value alone is never trusted: a callback from this boot must establish a timestamp within 2020–2099.

Validity ages from that response using monotonic elapsed time, independent of subsequent operating-system clock changes. The clock expires at 26 hours without a fresh response. A correction exceeding two seconds invalidates pending capture work. Daylight, stale/invalid time, an unsupported solar window, pause, or a fault disables ordinary monitoring and alarms.

`solar.cpp` uses iterative NOAA/Meeus Julian-century equations with east-positive longitude and a conventional 90.833° upper-limb horizon. The interval begins at sunset plus the configured start margin, inclusive, and ends at following sunrise minus the stop margin, exclusive. Events and comparisons use UTC, so timezone and daylight-saving changes do not shift the physical window. Coordinates near the date line are handled by chronological UTC event pairing. If no valid sunrise/sunset pair exists, automatic operation remains inhibited. Terrain, local obstructions, and unusual atmospheric refraction are not modeled.

Sources: [NOAA calculation details](https://gml.noaa.gov/grad/solcalc/calcdetails.html), [NREL SPA report](https://www.nlr.gov/docs/fy08osti/34302.pdf), and [USNO rise/set definitions](https://aa.usno.navy.mil/faq/RST_defs).

## Detection and outputs

Detection uses the MVP program's rule from `../motion-size/src/approach_gate.hpp`:

1. A new PIR LOW-to-HIGH transition wakes the camera after warmup, outside an alarm/cooldown, and only when the control policy permits monitoring. Camera exposure settles before checking at most ten frames.
2. Decode 320×240 JPEG; average 4×4 pixels into an 80×60 grayscale grid; subtract the median brightness shift; threshold absolute differences at 25; majority-filter each 3×3 neighborhood at five marked pixels; select the largest four-connected changed region.
3. Track five associated region frames, requiring neighboring boxes to overlap by at least 30% of the smaller box. An area below 48 pixels resets tracking.
4. The last three regions must each reach 480 pixels (10%). First-to-fifth growth must reach both 25% relative and 96 pixels (two percentage points of the full grid). At least two individual increases must reach `max(5, previous_area/20)` with integer division.

These are apparent foreground growth, not deer recognition, measured range, or depth. `LARGE_CHANGE` denotes growth confirmation; `WAITING_FOR_GROWTH` denotes a sufficiently large region without that confirmation. Changing the gate changes the MVP program as well, since both compile the same header.

Reference and monitoring captures turn white on, request a 120 ms settling delay, discard two queued frames, obtain a JPEG, then turn white off. Acquisition time extends illumination. Camera wake/discard steps and frame processing check a control-generation ticket; a frame or history from before a policy cancellation cannot start a later alarm.

An alarm commands three 1.5-second 2 kHz waveform intervals separated by 0.5-second gaps, plus 5 Hz white flashes over 5.5 seconds, from `deterrent::pattern` in the shared header. The policy computes outputs from elapsed monotonic time; an independent 20 ms timer callback applies them without depending on USB image streaming. Pause and other inhibition also apply outputs immediately when handled. These are the source's timings; nobody has measured them on the board for this program. “Buzzer off” holds GPIO10 high (the tone output is inverted), because the buzzer module, marked low-level trigger, conducts while its input is low; this has not been run on the board.

After a confirmed event, there is no timed cooldown; PIR must go LOW after the alarm before a new HIGH. An unconfirmed ten-frame burst enters a 120-second cooldown, where a still-HIGH PIR at expiry may start a new check. Policy cancellations reset burst history and require a new LOW-to-HIGH. Capture/decode errors abort the burst and require a fresh PIR transition.

RGB remains R13/G36/B35. TFT CS7 stays high and backlight45 low because RGB uses its SPI pins. Do not enable the TFT with this wiring. Camera standby remains between checks; the MCU and networking remain awake. There is no persistent photo storage.

## Commands and local API

Exact newline-terminated USB commands are `ACCESS`, `STATUS`, `PAUSE`, `RESUME`, `BASELINE`, and `ARM`. `ARM` only clears the current check/cooldown and requires a fresh PIR transition; it cannot clear pause or bypass time/night conditions.

All HTTP endpoints require Basic authentication. `GET /` serves the page; `GET /api/status` returns status and the request token. Mutations require `Content-Type: application/json` and the status response's token in `X-Deer-Control`. No CORS access is enabled.

| POST endpoint | JSON body |
|---|---|
| `/api/pause` | `{}` |
| `/api/resume` | `{}` |
| `/api/calibrate` | `{}` |
| `/api/config` | `latitude`, `longitude`, `after_sunset_minutes`, `before_sunrise_minutes`, `wifi_ssid`, optionally `wifi_password` |

Configuration parsing rejects missing/duplicate/unknown fields, invalid types, non-finite or out-of-range coordinates, fractional margins, oversized values, embedded NULs, and bodies over 1024 bytes. SSID/password support printable ASCII, with lengths 1–32 and 8–63 respectively. Saving settings always pauses.

`CONTROL` JSON records include event, state, pause/time/config/reference flags, clock age, and generation. `TIME_SYNC` or `POLICY_CHANGE` need not reset a burst if generation is unchanged. `BURST_ABORT` records cancellation. `MOTION` keeps the detector's fields and adds `control_generation`, `clock_valid`, `utc_capture_us`, `capture_uptime_us`, `decision_uptime_us`, `policy_state`, and `deterrent_started`. Capture timestamps mark JPEG acquisition completion, not exposure start; zero UTC means invalid time. `tools/record_photos.py` records these messages and `tools/replay_records.py` reads them ([tools guide](../../tools/README.md)).

## Build and tests

With ESP-IDF 5.4.2 activated (its `export.sh`, which also sets `IDF_PATH`), from this directory:

```sh
idf.py -B build -D IDF_TARGET=esp32s3 -D DEER_CAMERA_DIAGNOSTIC=OFF build
idf.py -B build-diagnostic -D IDF_TARGET=esp32s3 -D DEER_CAMERA_DIAGNOSTIC=ON build
python tests/run_host_tests.py --idf "$IDF_PATH" --sanitize
python tests/night-review/run_review.py --idf "$IDF_PATH" --output build/independent-review
node tests/test_control_page.js
```

The first two lines build the normal program and the bounded camera diagnostic in separate build directories; use the explicit `OFF`/`ON` selection each time. The diagnostic requires an explicit enabled calibration request, captures at most 12 valid frames, and compiles alarm activation out. It obeys held pause and the same illumination gate, and it does not flash the light on its own at boot. `platformio.ini` keeps the PlatformIO environments `classifier_test` and `camera_diagnostic`; that route has not been built for this program.

The production host suite (`run_host_tests.py`) compiles the solar, policy and configuration code against ESP-IDF's own cJSON with a fake clock and checks 1,214 assertions: night boundaries, margins, stale clocks, correction and reboot, setup calibration, held pause, consumed and stale tickets, every alarm waveform transition, faults and strict configuration parsing. `--address-sanitize` adds address and leak checks on a host whose runtime supports them. The independent review in [tests/night-review](tests/night-review/README.md) checks the same code against separate references. `test_control_page.js` runs the exact page script embedded in `control_page.hpp` with minimal DOM and API stand-ins (79 assertions on request payloads and tokens, deferred and obsolete replies, polling and form edits); it does not establish browser layout or HTTP and board execution. [BUILD-STATUS.txt](BUILD-STATUS.txt) gives the current results.

Host tests and target builds establish what the software does in the stated cases. They do not establish Wi-Fi or NTP availability, restart behaviour in the field, board timing, electrical output behaviour, battery endurance or detection outdoors with these controls. The detector's hardware records belong to the MVP program: [motion-size/HISTORY.md](../motion-size/HISTORY.md) and the saved sessions in [tests](../../tests/README.md); none was made with this program.
