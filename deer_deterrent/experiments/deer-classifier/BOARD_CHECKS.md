# Assembled-board recognition checks

Run these checks with the recorded-trial application and `tools/record_classifier.py`. Keep the practical detector's demonstrations and the retained score-only model experiment as distinct configurations. A software alarm command is paired with an observation of the physical outputs when checking actuation.

## Before flashing

1. Read the firmware now on the board back to a file and keep it, before anything is written: `python -m esptool --chip esp32s3 --port COM5 read_flash 0 0x400000 board-before-flash.bin` (use the actual port). Writing that file back at `0x0` restores the board exactly. Without such a file, going back to the MVP detector means building [firmware/motion-size](../../firmware/motion-size/README.md) again, since no image of it is kept in the repository.
2. The recorded-trial program and the MVP detector use the same flash layout. The laptop-interface images use another layout and also write `0xE000`–`0xFFFF`, inside the settings area of the ESP-IDF programs; after them, the night-controlled detector's saved settings may have to be entered again.
3. Flash with the commands in the [classifier guide](README.md) and confirm the existing camera/PIR/RGB/buzzer wiring and shared ground against [WIRING.md](../../WIRING.md). Leave the TFT disabled.

## Setup and paired records

1. Connect USB and open a new recording directory under `tools/captures/`. On the computer used for the recorded trials, opening the port resets the board: its boot lines are saved in the session and the 60-second sensor warm-up starts again. Wait for `READY`.
2. Verify that `BOOT` names the expected model hash and firmware, and that `READY` reports `mode=manual`, `armed=0`, `lighting=ambient` and `recording=full`. The recorder keeps its first `STATUS` reply in the manifest, then sends `STOP`, so each session starts manual, disarmed and idle. Check that no trial or alarm starts without a command. Initialization discards exposure-settling frames and runs a synthetic model-invocation check; it does not record a target trial.
3. Send `CAPTURE 1`. Inspect the retained original JPEG with its drawn boxes, the exact `.input.i8` file and the dequantized preview. Match the result's boot/trial/frame identity, camera dimensions, preprocessing settings, score, boxes and timing. Check for transfer errors before interpreting the image or score.
4. Repeat independent one-frame captures. They should not accumulate a two-frame confirmation across trial boundaries.

## Capture illumination

1. Keep the alarm disarmed and the camera/target fixed. Run a trial with `LIGHT AMBIENT`, then one with `LIGHT WHITE`. Confirm the selection in status and each frame result. Inspect the retained JPEGs and exact-input previews for lighting, glare, exposure and target visibility.
2. Observe white illumination before and during capture, followed by darkness during inference/transfer. It must not beep during a disarmed image trial. Attempt a lighting change while a trial is active; it should be rejected so that the trial retains one lighting condition.
3. Send `STOP` during illuminated acquisition and verify that the light goes out and the cancelled result cannot start an alarm. During a model-input transfer, `STOP` ends the transfer within one group of chunks (`TRIAL_BLOB_CANCELLED`); the frame's picture is kept and the summary counts a cancelled transfer.
4. Disconnect USB during another illuminated trial and check that the light goes out. The recorder records the lost port as an error and closes the session with its summary and gallery. Reconnect and open a new session: its manifest keeps the board's state as first reported, before the recorder's `STOP`.

The five-second capture-light watchdog is covered by software simulation. If a real acquisition stall occurs, check physical light cessation and the invalid result rather than counting that attempt as an illuminated recognition sample.

## Injected detection

`TEST OUTPUT` injects the alarm trigger a confirmed deer result sends: the controller starts the response through the same alarm start a confirmed frame takes, the shared 5.5-second sequence, with no picture taken and no score. It is accepted in manual mode, disarmed and idle, once the last run's end has been reported, and refused while armed or in PIR mode. Use `STOP`, then `TEST OUTPUT`. Observe three 1.5-second tone pulses, 0.5-second gaps and 5 Hz white flashing over 5.5 seconds, and pair the observation with the separately recorded `OUTPUT_TEST` start and terminal events and the status line's `alarm_source` `output_test`; no image or recognition verdict is created. Another `TEST OUTPUT` or a `CAPTURE` during the response must be refused, and `STOP`, `DISARM` or disconnecting USB must end it at once (`cancelled`). Restore the intended mode and lighting explicitly before recognition checks. The steps before the decision, qualifying scores through the two-of-three rule, are checked on a computer by `tests/test_decision_to_alarm.cpp`.

## Recognition and clearing

Keep the alarm disarmed. Use the same identifiable printed deer picture for full-body and closer presentations, retaining its source file and noting its print size, orientation, the lens-to-print distance measured with a ruler, and the lighting; `--label` and `--notes` keep these with the session. Include an empty scene and a non-deer or blank printed sheet. Keep settings fixed during each scored series. `RECORD JPEG` leaves each frame's model input out of the transfer, so frames follow each other faster, but the exact inputs are then not saved; `RECORD FULL` restores them.

Use one sufficiently long bounded trial to examine empty scene → deer presentation → target removal without resetting confirmation between those phases. Use console `MARK` entries to describe actions; each marker is tied to the frame received before it, and host receipt/marker times are not exposure timestamps. Inspect the images to associate a physical presentation with a frame. Record every attempted frame, including acquisition/inference errors and missing transfers. Report complete encounters separately from correlated frame observations; `trials.csv` lists each trial's frames, highest score and decision.

## PIR, response and cancellation

- Select `MODE PIR`; it cancels and disarms. Send `ARM` right after it: a sensor rise starts a trial only when armed. If `ARM` is refused because a trial, frame or alarm is still active, send `STOP`, then `MODE PIR` and `ARM` again. After the 60-second startup warm-up, let PIR become LOW, then produce a new HIGH. Verify that one event starts at most ten frames. `PIR_STATE` lines record each change of the sensor level, the end of warm-up and the pause.
- An unqualified window starts the 120-second pause; `STOP`, `DISARM` and mode changes do not clear it, and the status line shows `pause_remaining_s`. A persistent HIGH can start another window when the pause expires.
- A real confirmed neural decision should start the shared three-pulse/white-flash response and end the trial after its triggering frame. Observe the actual light and buzzer and pair the observation with the record. Repeated qualifying inputs must not extend that event. After it ends, PIR must return LOW before another rising trigger.
- Send `DISARM` or `STOP` during acquisition/inference. A result from the cancelled trial must be marked uncommitted and must not start an output. `STOP` also returns to manual mode. Check output cessation during an actual response if a qualifying case is available.
- Disconnect USB during an armed trial and confirm cancellation/disarming. Reconnect, open a new session and explicitly select and arm the next test. This is a supervised USB-recorded workflow, not unattended battery operation.
- Inspect measured capture, preprocessing, model and transfer times separately. Tensor/JPEG transfer can lengthen spacing between frames; a slower recording cadence is not model-compute time.

In the recorded trials of 4 November no frame reached 0.85, so no recognition decision started the response. If that remains true, report the failed recognition and the absence of a recognition-driven response. `TEST OUTPUT` checks the path from a confirmed decision to the response by injecting the trigger that decision sends.
