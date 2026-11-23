# Independent night-control regression review

These tests exercise the same solar, policy, configuration, and growth code as the firmware. They require Python 3 and `g++`/`gcc`-named host compiler commands supporting C++17 and UndefinedBehaviorSanitizer, with no Python packages. The current execution used Apple Clang 21 through those command names. Supplying the exact ESP-IDF checkout also enables the persistence/concurrency checks with its cJSON source.

From any working directory:

```sh
python path/to/firmware/night-control/tests/night-review/run_review.py --idf /path/to/esp-idf --output /path/to/review-results
```

`IDF_PATH` can replace `--idf`. Without either, the runner explicitly skips service transaction checks. The default output directory is `firmware/night-control/build/independent-review`.

The suite covers:

- Boot inhibition, paused priority, calibration-only illumination, exact sunrise/sunset and clock-age boundaries, alarm phases and consumed tickets, invalid values, and randomized paused command sequences.
- Eight published NREL SPA event values, plus a separate USNO coordinate/sidereal-time altitude-crossing oracle. The matrix covers both hemispheres, longitude signs, the date line, leap day, year boundaries, DST dates, and post-2038 timestamps. Four host timezone settings must produce identical UTC schedules.
- The exact app generation guard, extracted on each run, with a cancellation placed between the loop snapshot and the capture ticket. This is a narrow handoff test using the real core and growth gate.
- Exact service `pause`, `resume`, `configure`, and synchronization function bodies, extracted on each run. Host mutex/output/NVS seams permit deterministic overlapping PAUSE/RESUME operations and persistence failures. The real policy and configuration implementations run unchanged.

Primary formula and vector provenance is recorded in `usno_oracle.py` and `solar_reference_vectors.json`. The published SPA tester's local sunset clock time belongs to the preceding local calendar date in its normalized UTC event calculation; the vector records the actual UTC event, not an invented following-day sunset.

Passing results establish host software behavior for the stated cases. They do not establish successful board flashing, radio association, NTP reachability, physical output latency, NVS power-loss durability, camera quality, or field deterrence. UndefinedBehaviorSanitizer is enabled; AddressSanitizer is not claimed by this suite.
