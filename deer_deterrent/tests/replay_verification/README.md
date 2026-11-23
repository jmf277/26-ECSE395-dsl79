# Replay of the detector records

The saved detector sessions in [../captures](../captures/) were checked and replayed with [tools/replay_records.py](../../tools/replay_records.py). The tool verifies each saved photo's length, FNV-1a checksum and JPEG markers against its record and rebuilds the sensor windows from `events.jsonl`. Each window whose start was recorded, whose sequence is consistent and whose records carry the growth values and the gate's own settings is replayed: the recorded region of every frame goes through the MVP program's own growth gate, [approach_gate.hpp](../../firmware/motion-size/src/approach_gate.hpp), compiled into [tools/gate_replay.cpp](../../tools/gate_replay.cpp), and the gate's state and growth values for each frame, and each complete window's confirmation, are compared with the recorded ones.

## Result

24 sessions, 175 verified photos. 45 region records in 5 complete windows were replayed through the gate, with 0 mismatches. The sessions hold 377 detector records, 132 calibration records and 7 camera-diagnostic records. Eleven earlier size-only windows, whose records carry no growth values, are skipped as not comparable with the growth gate, and 277 detector records outside an observed sensor window are not replayed. The reports keep 21 ESP-IDF error-level log lines and 6 warnings: two errors listed in the saved summaries of two sessions (an incomplete last photo transfer, invalid motion metadata), a motion line embedded in another device message and left out of the measurement sequence, and three saved photos that no recorded event places in a window. There are no application errors, integrity errors or replay mismatches. The tools' host tests pass, 105 of 105.

| File | Content |
|---|---|
| `corpus.json` | Complete per-session analysis: windows, frame and photo records, diagnostics, and the gate replay with the gate's and adapter's SHA-256 and the compiler used |
| `corpus.md` | Readable overview of the same analysis |
| `host_tests.txt` | Verbose output of the tools' host test suite, including the native gate tests |
| `replay_run.txt` | The replay command's own output |

## Reproduce

From the `deer_deterrent` folder, with Pillow installed (`python -m pip install -r tools/requirements-classifier.txt`) and a C++17 compiler:

```sh
python -m unittest discover -s tools -p "test_*.py" -v > tests/replay_verification/host_tests.txt 2>&1
python tools/replay_records.py tests/captures --output tests/replay_verification/corpus.json --markdown tests/replay_verification/corpus.md > tests/replay_verification/replay_run.txt
```

These files were made that way, on a copy of the repository, with Apple clang 21; the reports name each session folder relative to the working directory. The gate's SHA-256 in `corpus.json` is that of `firmware/motion-size/src/approach_gate.hpp` as committed; a change to the gate or the adapter needs the commands run again.

## Scope

This is a replay of recorded region measurements: the reference image was not saved, so camera image processing is not rerun. Matching decisions show that the saved records agree with this gate; they do not show which binary was on the board when a session was recorded. Frames are correlated observations, not independent accuracy trials, and alarm messages are software events, not measurements of sound or light. See the [tools guide](../../tools/README.md) for the window states and the summary fields.
