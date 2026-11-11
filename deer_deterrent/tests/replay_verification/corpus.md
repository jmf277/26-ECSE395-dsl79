# Detector recording analysis

Analysis of retained records; no new hardware experiment was performed.

24 sessions; 175 verified saved photos; 45 region records replayed through the shared C++ gate in 5 windows; 0 replay mismatches.

Frames are correlated observations, not independent accuracy trials. Alarm start/stop messages are software events and do not establish physical sound or light output. Legacy size-only windows and records with unknown initial history are not evaluated against the current gate.

| Session | Detector records | Calibration / diagnostic | Verified photos | Window status | Alarm start / stop messages | SDK E diagnostics |
|---|---:|---:|---:|---|---:|---:|
| 20261029-162522-225761-initial-check | 63 | 23 / 0 | 12 | no observed PIR window | 0 / 0 | 0 |
| 20261029-162748-872374-default-view | 45 | 23 / 0 | 10 | no observed PIR window | 0 / 0 | 0 |
| 20261029-162902-877406-small-phone | 85 | 0 / 0 | 12 | no observed PIR window | 0 / 0 | 0 |
| 20261029-163019-306672-large-person | 84 | 0 / 0 | 12 | no observed PIR window | 0 / 0 | 0 |
| 20261029-163653-891180-deterrent-startup | 0 | 14 / 0 | 2 | no observed PIR window | 0 / 0 | 1 |
| 20261029-163718-083304-armed-check | 0 | 0 / 0 | 0 | no observed PIR window | 0 / 0 | 0 |
| 20261029-163821-275152-deterrent-reference | 10 | 15 / 0 | 13 | 1 complete | 0 / 1 | 2 |
| 20261029-164009-749997-phone-deterrent | 3 | 0 / 0 | 3 | 1 complete | 1 / 2 | 1 |
| 20261029-164345-639863-five-percent-startup | 0 | 0 / 0 | 0 | no observed PIR window | 0 / 0 | 0 |
| 20261029-164408-046795-five-percent-live | 4 | 0 / 0 | 4 | 1 truncated | 0 / 0 | 0 |
| 20261029-164500-946892-five-percent-window-end | 0 | 0 / 0 | 0 | no observed PIR window | 0 / 0 | 0 |
| 20261029-164554-843353-person-five-percent | 3 | 0 / 0 | 3 | 1 complete | 1 / 2 | 1 |
| 20261029-180609-968612-display-startup | 0 | 10 / 0 | 2 | no observed PIR window | 0 / 0 | 1 |
| 20261029-180701-165773-display-armed-check | 0 | 0 / 0 | 0 | no observed PIR window | 0 / 0 | 0 |
| 20261031-163435-551083-final-rgb-startup | 0 | 22 / 0 | 6 | no observed PIR window | 0 / 0 | 1 |
| 20261031-163535-273574-final-rgb-large-target | 13 | 0 / 0 | 14 | 2 complete | 1 / 1 | 3 |
| 20261031-180337-326915-d12-camera-diagnostic | 0 | 0 / 7 | 8 | no observed PIR window | 0 / 0 | 0 |
| 20261031-182221-565944-rearmed-live-test | 10 | 0 / 0 | 10 | 1 complete | 0 / 0 | 1 |
| 20261031-182520-584322-large-target-recorded-repeat | 12 | 0 / 0 | 12 | 4 complete | 4 / 4 | 4 |
| 20261031-183354-551487-approach-10-percent-startup | 0 | 25 / 0 | 7 | no observed PIR window | 0 / 0 | 1 |
| 20261031-183457-917906-stationary-no-approach | 10 | 0 / 0 | 10 | 1 complete | 0 / 0 | 1 |
| 20261031-183551-894064-approaching-growth-test | 15 | 0 / 0 | 15 | 2 complete | 1 / 1 | 2 |
| 20261031-183651-967957-small-target-growth-rule | 10 | 0 / 0 | 10 | 1 complete | 0 / 0 | 1 |
| 20261031-183751-692936-centered-small-target-repeat | 10 | 0 / 0 | 10 | 1 complete | 0 / 0 | 1 |

## Scope and integrity

Region-metadata replay; does not reconstruct the unsaved baseline or rerun camera image processing.

Gate SHA-256: `2b34faa2bbb1519208b84fd5018884d436c9f1907cf237a9f859e7cc017b908f`. Adapter SHA-256: `839e49020bc56c12a22d0d28d6959d1a899caaa82827cc7e14c2cb5035b42fb1`.

Local source hashes identify this replay engine. They do not establish which binary was flashed for a historical session.

### 20261029-162522-225761-initial-check

- Warning — saved files: legacy_recording_error: Final photo transfer incomplete; discarded

### 20261029-162748-872374-default-view

- Warning — events.jsonl:1: embedded_unparsed_motion: MOTION text embedded in a device message was not inserted into the measurement sequence
- Warning — saved files: photo_without_matching_event: frame-000540-large_change.json: kept separate; its position in a window is not established

### 20261029-163019-306672-large-person

- Warning — saved files: legacy_recording_error: Invalid motion metadata

### 20261031-163535-273574-final-rgb-large-target

- Warning — saved files: photo_without_matching_event: frame-000027-confirming.json: kept separate; its position in a window is not established

### 20261031-180337-326915-d12-camera-diagnostic

- Warning — saved files: photo_without_matching_event: frame-000002-camera_test.json: kept separate; its position in a window is not established
