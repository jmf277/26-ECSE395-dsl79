# Assembled-board recognition checks

Run these checks with the recorded-trial application and `tools/record_classifier.py`. Keep the practical detector's demonstrations and the retained score-only model experiment as distinct configurations. A software alarm command is paired with an observation of the physical outputs when checking actuation.

## Setup and paired records

1. Confirm the existing camera/PIR/RGB/buzzer wiring and shared ground. Leave the TFT disabled. Flash the recorded-trial firmware, connect USB, and open a new recording directory.
2. Verify startup reports `mode=manual`, `armed=0` and the expected model hash. Check that no trial or alarm starts without a command. Initialization discards exposure-settling frames and runs a synthetic model-invocation check; it does not record a target trial.
3. Send `CAPTURE 1`. Inspect the retained original JPEG, exact `.input.i8` file and dequantized preview. Match the result's boot/trial/frame identity, camera dimensions, preprocessing, score, boxes and timing. Check for transfer errors before interpreting the image or score.
4. Repeat independent one-frame captures. They should not accumulate a two-frame confirmation across trial boundaries.

## Capture illumination and independent outputs

1. Keep the alarm disarmed and the camera/target fixed. Run a trial with `LIGHT AMBIENT`, then one with `LIGHT WHITE`. Confirm the selection in status and each frame result. Inspect the retained JPEGs and exact-input previews for lighting, glare, exposure and target visibility.
2. Observe white illumination before and during capture, followed by darkness during inference/transfer. It must not beep during a disarmed image trial. Attempt a lighting change while a trial is active; it should be rejected so that the trial retains one lighting condition.
3. Send `STOP` during illuminated acquisition and verify that the light goes out and the cancelled result cannot start an alarm. Disconnect USB during another illuminated trial and check the same output cessation. Reconnect before starting a new test.
4. Use `STOP`, then `TEST OUTPUT`. Observe three 1.5-second tone pulses, 0.5-second gaps and 5 Hz white flashing over 5.5 seconds. Pair observations with the separately recorded `OUTPUT_TEST` start/terminal events; no image or recognition verdict should be created.
5. During that output test, another `TEST OUTPUT` or `CAPTURE` must not restart or overlap it. Check immediate cancellation using `STOP` or `DISARM` and on USB disconnection. Check that the test command is rejected when the classifier is armed or in PIR mode. Restore the intended mode/lighting explicitly before recognition checks.

The five-second capture-light watchdog is covered by software simulation. If a real acquisition stall occurs, check physical light cessation and the invalid result rather than counting that attempt as an illuminated recognition sample.

## Recognition and clearing

Keep the alarm disarmed. Use the same identifiable deer picture for full-body and closer presentations, retaining the source picture and noting paper/screen, orientation, approximate distance and lighting. Include an empty scene and a non-deer/blank presentation. Keep settings fixed during each scored series.

Use one sufficiently long bounded trial to examine empty scene → deer presentation → target removal without resetting confirmation between those phases. Use console `MARK` entries to describe actions; host receipt/marker times are not exposure timestamps. Inspect the images to associate a physical presentation with a frame. Record every attempted frame, including acquisition/inference errors and missing transfers. Report complete encounters separately from correlated frame observations.

## PIR, response and cancellation

- Select `MODE PIR`; it cancels and disarms. After the 60-second startup warm-up, let PIR become LOW, then produce a new HIGH. Verify that one event starts at most ten frames. An unqualified window should enter the 120-second cooldown. A persistent HIGH can start another window when that cooldown expires.
- While idle, send `ARM`. A real confirmed neural decision should start the shared three-pulse/white-flash response and end the trial after its triggering frame. Observe the actual light and buzzer and pair the observation with the record. Repeated qualifying inputs must not extend that event. After it ends, PIR must return LOW before another rising trigger.
- Send `DISARM` or `STOP` during acquisition/inference. A result from the cancelled trial must be marked uncommitted and must not start an output. `STOP` also returns to manual mode. Check output cessation during an actual response if a qualifying case is available.
- Disconnect USB during an armed trial and confirm cancellation/disarming. Reconnect, inspect status and explicitly select/arm the next test. This is a supervised USB-recorded workflow, not unattended battery operation.
- Inspect measured capture, preprocessing, model and transfer times separately. Tensor/JPEG transfer can lengthen spacing between frames; a slower recording cadence is not model-compute time.

The retained model did not qualify either recorded deer presentation. If that remains true, report the failed recognition and absence of a recognition-driven response. The response connection may be implemented and software-tested without obtaining a qualifying physical AI trial. `TEST OUTPUT` checks the outputs independently and never supplies a forced model verdict.
