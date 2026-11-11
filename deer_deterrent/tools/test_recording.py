"""Boundary, corruption and provenance checks; no hardware is required."""
import base64
import json
import contextlib
import io
import itertools
import os
import sys
import types
from unittest.mock import patch
import record_photos
import shutil
import tempfile
import unittest
from pathlib import Path

from record_photos import Receiver, fnv1a, save_photo
from recording import analyze_session, sha256_file, summary_from_analysis
from replay_records import NativeGate, discover_sessions, main, replay_sessions


def event(message):
    return {"device_message": message}


def frame(number, area, state="WAITING_FOR_GROWTH", *, history=1, gain=0, percent=0, cutoff=10):
    result = {"frame": number, "state": state, "area_percent": round(area * 100 / 4800, 3),
              "changed_pixels": area, "bbox": [10, 1, 30, 58] if area else [0, 0, 0, 0],
              "grid": [80, 60], "brightness_shift": 0, "pir": 1, "baseline_samples": 12,
              "cutoff_percent": cutoff}
    if cutoff == 10:
        result.update(growth_window=5, growth_required_percent=25, growth_min_gain_pixels=96,
                      growth_frames=history, growth_pixels=gain, growth_percent=percent)
    return result


def growing_frames(start=1):
    return [frame(start + i, area, "LARGE_CHANGE" if i == 4 else "WAITING_FOR_GROWTH",
                  history=i + 1, gain=gain, percent=percent)
            for i, (area, gain, percent) in enumerate(zip([720, 866, 1122, 1354, 1567],
                                                         [0, 146, 402, 634, 847], [0, 20, 55, 88, 117]))]


def write_events(root, rows):
    (root / "events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def transfer(metadata=None, ending=None):
    payload = b"\xff\xd8transport-fixture\xff\xd9"
    meta = dict(metadata or frame(7, 0, "CLEAR", history=0), jpeg_bytes=len(payload), fnv1a=fnv1a(payload))
    raw = ("MOTION " + json.dumps(meta) + "\nPHOTO_BEGIN " + json.dumps(meta) +
           "\nPHOTO_DATA " + base64.b64encode(payload).decode() + "\nPHOTO_END " + str(meta["frame"] if ending is None else ending) + "\n").encode()
    return payload, meta, raw


class RecordingAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_calibration_and_diagnostic_are_not_scored(self):
        rows = [{"frame": 1, "state": "CALIBRATING", "area_percent": 0},
                {"frame": 2, "state": "CAMERA_TEST", "area_percent": 0}, frame(3, 0, "CLEAR", history=0)]
        write_events(self.root, rows)
        result = analyze_session(self.root)
        self.assertEqual(result["counts"]["detector_frame_records"], 1)
        self.assertEqual(result["counts"]["calibration_frame_records"], 1)
        self.assertEqual(result["counts"]["diagnostic_frame_records"], 1)
        self.assertEqual(result["windows"], [])
        self.assertNotIn("measured_frames", summary_from_analysis(result, "test"))

    def test_sdk_errors_remain_separate_from_application_errors(self):
        write_events(self.root, [event("\x1b[31mE (14273) gdma: disconnect warning\x1b[0m"),
                                 event("ERROR camera_capture"), event("W (999) camera: notice")])
        result = analyze_session(self.root)
        self.assertEqual(result["counts"]["sdk_error_diagnostics"], 1)
        self.assertEqual(result["counts"]["sdk_warning_diagnostics"], 1)
        self.assertEqual(result["counts"]["application_error_messages"], 1)
        self.assertEqual(result["sdk_diagnostics"][0]["line"], 1)

    def test_photo_only_startup_frame_is_not_prepended_after_arm(self):
        payload, orphan, _ = transfer(frame(27, 612, "CONFIRMING", cutoff=5))
        save_photo(self.root, orphan, [base64.b64encode(payload).decode()], "27")
        write_events(self.root, [event("ARMED manual_rearm=1 pir=1"), event("STATUS state=ARMED pir=0"),
                                 event("PIR_TRIGGER starting_max_10_frames=1"),
                                 frame(28, 1075, "CONFIRMING", cutoff=5), frame(29, 1093, "CONFIRMING", cutoff=5),
                                 event("DETERRENT_ON frame=30 count=1"), frame(30, 814, "LARGE_CHANGE", cutoff=5),
                                 event("BURST_END frames=3 large_confirmed=1")])
        result = analyze_session(self.root)
        self.assertEqual([f["metadata"]["frame"] for f in result["windows"][0]["frames"]], [28, 29, 30])
        self.assertEqual(result["windows"][0]["completeness"], "complete")
        self.assertTrue(any(w["code"] == "photo_without_matching_event" for w in result["warnings"]))
        self.assertEqual(result["counts"]["alarm_start_messages"], 1)

    def test_explicit_end_is_required_even_when_ten_frames_are_present(self):
        write_events(self.root, [event("PIR_TRIGGER")]+[frame(i, 0, "CLEAR", history=0) for i in range(1, 11)])
        result = analyze_session(self.root)
        self.assertEqual(result["windows"][0]["completeness"], "truncated")
        self.assertIsNone(result["windows"][0]["firmware_confirmed"])

    def test_missing_trigger_is_not_fabricated(self):
        write_events(self.root, growing_frames() + [event("BURST_END frames=5 large_confirmed=1")])
        result = analyze_session(self.root)
        self.assertFalse(result["windows"][0]["start_observed"])
        self.assertEqual(result["windows"][0]["completeness"], "incomplete")

    def test_arm_calibration_and_policy_messages_are_resets_not_trials(self):
        rows = [event("ARMED manual_rearm=1 pir=0"), event('CONTROL {"event":"PAUSE","generation":2}'),
                event("CALIBRATION clear_view_now; starts_in=3_seconds"),
                {"frame": 1, "state": "CALIBRATING"}, event("CALIBRATION complete; baseline_fixed=1")]
        write_events(self.root, rows)
        result = analyze_session(self.root)
        self.assertEqual(result["windows"], [])
        self.assertEqual([item["type"] for item in result["reset_boundaries"]],
                         ["ARM", "CALIBRATION_REQUEST", "CALIBRATION_COMPLETE"])

    def test_idempotent_control_and_small_clock_sync_do_not_reset_window(self):
        rows = [event('CONTROL {"event":"STATUS","generation":2}'), event("PIR_TRIGGER")]
        first, second = growing_frames()[:2]
        first["control_generation"] = second["control_generation"] = 2
        rows += [first, event('CONTROL {"event":"TIME_SYNC","generation":2}'),
                 event('CONTROL {"event":"RESUME","generation":2}'),
                 event('CONTROL {"event":"POLICY_CHANGE","generation":2}'), second]
        write_events(self.root, rows)
        result = analyze_session(self.root)
        self.assertEqual(len(result["windows"]), 1)
        self.assertEqual(len(result["windows"][0]["frames"]), 2)
        self.assertEqual(result["windows"][0]["completeness"], "truncated")
        self.assertEqual(result["reset_boundaries"], [])

    def test_changed_control_generation_closes_active_window(self):
        item = growing_frames()[0]
        item["control_generation"] = 2
        write_events(self.root, [event("PIR_TRIGGER"), item,
                                 event('CONTROL {"event":"TIME_SYNC","generation":3}')])
        result = analyze_session(self.root)
        self.assertEqual(result["windows"][0]["end_reason"], "CONTROL_GENERATION_CHANGED")
        self.assertEqual(result["windows"][0]["completeness"], "aborted")

    def test_first_frame_must_match_trigger_or_known_control_generation(self):
        for prefix in (
            [event("PIR_TRIGGER generation=2")],
            [event('CONTROL {"event":"STATUS","generation":2}'), event("PIR_TRIGGER")],
            [event('CONTROL {"event":"STATUS","generation":2}'), event("PIR_TRIGGER generation=2")],
            [event("PIR_TRIGGER"), event('CONTROL {"event":"STATUS","generation":2}')],
        ):
            with self.subTest(prefix=prefix):
                records = growing_frames()
                for item in records:
                    item["control_generation"] = 3
                write_events(self.root, prefix + records + [event("BURST_END frames=5 large_confirmed=1")])
                result = analyze_session(self.root)
                window = result["windows"][0]
                self.assertEqual(window["completeness"], "incomplete")
                self.assertIn("control_generation_changed", window["issues"])
                self.assertTrue(any(w["code"] == "control_generation_changed"
                                    and w["line"] == len(prefix) + 1 for w in result["warnings"]))

    def test_matching_generation_and_explicit_new_trigger_remain_complete(self):
        for trigger_generation in (2, 3):
            with self.subTest(trigger_generation=trigger_generation):
                records = growing_frames()
                for item in records:
                    item["control_generation"] = trigger_generation
                write_events(self.root, [event('CONTROL {"event":"STATUS","generation":2}'),
                                         event(f"PIR_TRIGGER generation={trigger_generation}")] + records +
                             [event("BURST_END frames=5 large_confirmed=1")])
                result = analyze_session(self.root)
                self.assertEqual(result["windows"][0]["completeness"], "complete")
                self.assertEqual(result["windows"][0]["control_generation"], trigger_generation)
                self.assertEqual(result["warnings"], [])

    def test_bare_resets_do_not_carry_old_generation_into_new_window(self):
        for reset in ("BOOT motion_size", "ARMED manual_rearm=1 pir=0",
                      "CALIBRATION clear_view_now", "BURST_ABORT reason=PAUSED"):
            with self.subTest(reset=reset):
                records = growing_frames()
                for item in records:
                    item["control_generation"] = 1
                write_events(self.root, [event('CONTROL {"event":"STATUS","generation":8}'),
                                         event(reset), event("PIR_TRIGGER")] + records +
                             [event("BURST_END frames=5 large_confirmed=1")])
                result = analyze_session(self.root)
                self.assertEqual(result["windows"][0]["completeness"], "complete")
                self.assertEqual(result["windows"][0]["control_generation"], 1)

    def test_control_reset_and_bare_acknowledgment_orders_preserve_valid_window(self):
        for control, acknowledgment in (
            ('CONTROL {"event":"REARM","generation":2}', "ARMED manual_rearm=1 pir=0"),
            ('CONTROL {"event":"CALIBRATION_REQUEST","generation":2}', "CALIBRATION clear_view_now"),
        ):
            for reset_order in ((control, acknowledgment), (acknowledgment, control)):
                with self.subTest(reset_order=reset_order):
                    records = growing_frames()
                    for item in records:
                        item["control_generation"] = 2
                    write_events(self.root, [event('CONTROL {"event":"STATUS","generation":1}')]
                                 + [event(message) for message in reset_order] + [event("PIR_TRIGGER")]
                                 + records + [event("BURST_END frames=5 large_confirmed=1")])
                    result = analyze_session(self.root)
                    self.assertEqual(result["windows"][0]["completeness"], "complete")
                    self.assertEqual(result["windows"][0]["control_generation"], 2)

    def test_paired_reset_identity_rejects_mismatched_first_frame_in_both_orders(self):
        for control, acknowledgment in (
            ('CONTROL {"event":"REARM","generation":2}', "ARMED manual_rearm=1 pir=0"),
            ('CONTROL {"event":"CALIBRATION_REQUEST","generation":2}', "CALIBRATION clear_view_now"),
        ):
            for reset_order in ((control, acknowledgment), (acknowledgment, control)):
                with self.subTest(reset_order=reset_order):
                    records = growing_frames()
                    for item in records:
                        item["control_generation"] = 3
                    write_events(self.root, [event(message) for message in reset_order]
                                 + [event("PIR_TRIGGER")] + records
                                 + [event("BURST_END frames=5 large_confirmed=1")])
                    result = analyze_session(self.root)
                    self.assertEqual(result["windows"][0]["completeness"], "incomplete")
                    self.assertEqual(result["windows"][0]["control_generation"], 2)
                    self.assertIn("control_generation_changed", result["windows"][0]["issues"])

    def test_reset_identity_survives_intervening_abort_status_and_old_capture(self):
        records = growing_frames(start=2)
        for item in records:
            item["control_generation"] = 3
        delayed = frame(1, 720)
        delayed["control_generation"] = 1
        write_events(self.root, [event('CONTROL {"event":"REARM","generation":2}'), delayed,
                                 event("BURST_ABORT reason=ARMED generation=2 frames=1"),
                                 event('CONTROL {"event":"POLICY_CHANGE","generation":2}'),
                                 event("ARMED manual_rearm=1 pir=0"), event("PIR_TRIGGER")]
                     + records + [event("BURST_END frames=5 large_confirmed=1")])
        result = analyze_session(self.root)
        self.assertEqual(result["windows"][0]["completeness"], "incomplete")
        self.assertEqual(result["windows"][0]["control_generation"], 2)

    def test_paired_reset_is_consumed_before_a_later_unpaired_marker(self):
        control = 'CONTROL {"event":"REARM","generation":2}'
        acknowledgment = "ARMED manual_rearm=1 pir=0"
        for reset_order in ((control, acknowledgment), (acknowledgment, control)):
            with self.subTest(reset_order=reset_order):
                records = growing_frames()
                for item in records:
                    item["control_generation"] = 3
                write_events(self.root, [event(message) for message in reset_order]
                             + [event(acknowledgment), event("PIR_TRIGGER")] + records
                             + [event("BURST_END frames=5 large_confirmed=1")])
                result = analyze_session(self.root)
                self.assertEqual(result["windows"][0]["completeness"], "complete")
                self.assertEqual(result["windows"][0]["control_generation"], 3)

    def test_calibration_ticket_cannot_restore_generation_before_completion(self):
        for state in ("CALIBRATING", "CAMERA_TEST"):
            with self.subTest(state=state):
                records = growing_frames(start=2)
                for item in records:
                    item["control_generation"] = 3
                write_events(self.root, [event('CONTROL {"event":"STATUS","generation":2}'),
                                         event("CALIBRATION complete; baseline_fixed=1"),
                                         {"frame": 1, "state": state, "control_generation": 2},
                                         event("PIR_TRIGGER")] + records +
                             [event("BURST_END frames=5 large_confirmed=1")])
                result = analyze_session(self.root)
                self.assertEqual(result["windows"][0]["completeness"], "complete")
                self.assertEqual(result["windows"][0]["control_generation"], 3)

    def test_old_ticket_after_cancellation_does_not_replace_current_generation(self):
        for cancellation in ('CONTROL {"event":"PAUSE","generation":3}',
                             "BURST_ABORT reason=PAUSED generation=3 frames=1"):
            with self.subTest(cancellation=cancellation):
                old = frame(1, 720)
                old["control_generation"] = 2
                delayed = frame(2, 866, history=2, gain=146, percent=20)
                delayed["control_generation"] = 2
                records = growing_frames(start=3)
                for item in records:
                    item["control_generation"] = 3
                write_events(self.root, [event("PIR_TRIGGER generation=2"), old, event(cancellation),
                                         delayed, event("PIR_TRIGGER")] + records +
                             [event("BURST_END frames=5 large_confirmed=1")])
                result = analyze_session(self.root)
                self.assertEqual([window["completeness"] for window in result["windows"]], ["aborted", "complete"])
                self.assertIsNone(result["frames"][1]["window"])
                self.assertEqual(result["windows"][1]["control_generation"], 3)

    def test_invalid_trigger_generation_prevents_complete_window(self):
        for generation in ("-1", "true", "2.5", "1_0", "18446744073709551616"):
            with self.subTest(generation=generation):
                write_events(self.root, [event(f"PIR_TRIGGER generation={generation}")] + growing_frames()
                             + [event("BURST_END frames=5 large_confirmed=1")])
                result = analyze_session(self.root)
                self.assertEqual(result["windows"][0]["completeness"], "incomplete")
                self.assertIn("invalid_pir_trigger_generation", result["windows"][0]["issues"])

    def test_abort_closes_window_and_status_heartbeat_is_not_final_state(self):
        write_events(self.root, [event("STATUS state=ARMED pir=0"), event("PIR_TRIGGER"), growing_frames()[0],
                                 event("BURST_ABORT reason=PAUSED"), event('CONTROL {"event":"PAUSE"}')])
        result = analyze_session(self.root)
        self.assertEqual(result["windows"][0]["completeness"], "aborted")
        self.assertEqual(result["last_observed_status"]["followed_by_event_records"], 4)

    def test_truncated_or_corrupt_json_is_reported_with_line(self):
        (self.root / "events.jsonl").write_text(json.dumps(event("PIR_TRIGGER")) + "\n{\"frame\":", encoding="utf-8")
        result = analyze_session(self.root)
        self.assertEqual(result["errors"][0]["line"], 2)
        self.assertIn("invalid_event_json", result["windows"][0]["issues"])

    def test_nonfinite_and_inconsistent_region_values_are_rejected(self):
        bad = frame(1, 720)
        bad["area_percent"] = 99
        write_events(self.root, [bad])
        self.assertEqual(analyze_session(self.root)["errors"][0]["code"], "invalid_frame_metadata")
        (self.root / "events.jsonl").write_text('{"frame":1,"state":"CLEAR","area_percent":NaN}\n')
        self.assertEqual(analyze_session(self.root)["errors"][0]["code"], "invalid_event_json")

    def test_invalid_control_payload_does_not_crash_or_bridge_history(self):
        write_events(self.root, [event("PIR_TRIGGER"), event('CONTROL {"event":[]}')])
        result = analyze_session(self.root)
        self.assertEqual(result["errors"][0]["code"], "invalid_control_message")
        self.assertIn("invalid_control_message", result["windows"][0]["issues"])

    def test_manifest_detects_altered_artifact_and_rejects_escape(self):
        write_events(self.root, [event("STATUS state=ARMED")])
        source = self.root / "events.jsonl"
        manifest = {"schema_version": 2, "closed": True, "artifacts": [{"path": source.name, "sha256": sha256_file(source), "bytes": source.stat().st_size}]}
        (self.root / "run_manifest.json").write_text(json.dumps(manifest))
        self.assertEqual(analyze_session(self.root)["run_manifest"]["verified_artifacts"], 1)
        source.write_text(source.read_text() + json.dumps(event("changed")) + "\n")
        self.assertEqual(analyze_session(self.root)["errors"][0]["code"], "manifest_artifact_mismatch")
        manifest["artifacts"][0]["path"] = "../outside.txt"
        (self.root / "run_manifest.json").write_text(json.dumps(manifest))
        self.assertEqual(analyze_session(self.root)["errors"][0]["code"], "invalid_run_manifest")

    def test_closed_manifest_cannot_omit_event_hash(self):
        write_events(self.root, [])
        (self.root / "run_manifest.json").write_text(json.dumps({"schema_version": 2, "closed": True, "artifacts": []}))
        self.assertEqual(analyze_session(self.root)["errors"][0]["code"], "manifest_missing_events")

    def test_bad_control_timing_metadata_is_not_scored(self):
        item = frame(1, 720)
        item.update(control_generation=True, capture_uptime_us=200, decision_uptime_us=100)
        write_events(self.root, [item])
        result = analyze_session(self.root)
        self.assertEqual(result["counts"]["detector_frame_records"], 0)
        self.assertEqual(result["errors"][0]["code"], "invalid_frame_metadata")
        item["control_generation"] = 1
        write_events(self.root, [item])
        self.assertIn("Decision time", analyze_session(self.root)["errors"][0]["detail"])

    def test_deterrent_started_requires_boolean(self):
        for value in (1, 0, "false", None):
            with self.subTest(value=value):
                item = frame(1, 720)
                item["deterrent_started"] = value
                write_events(self.root, [item])
                result = analyze_session(self.root)
                self.assertEqual(result["counts"]["detector_frame_records"], 0)
                self.assertIn("expected Boolean", result["errors"][0]["detail"])

    def test_deterrent_started_cannot_be_true_for_unqualified_gate(self):
        item = frame(1, 720)
        item["deterrent_started"] = True
        write_events(self.root, [item])
        result = analyze_session(self.root)
        self.assertEqual(result["counts"]["detector_frame_records"], 0)
        self.assertIn("requires a LARGE_CHANGE", result["errors"][0]["detail"])

    def test_duplicate_inputs_do_not_inflate_session_count(self):
        write_events(self.root, [])
        self.assertEqual(discover_sessions([self.root, self.root / "events.jsonl"]), [self.root.resolve()])

    def test_report_names_sessions_relative_to_the_working_directory(self):
        session = self.root / "captures" / "session-a"
        session.mkdir(parents=True)
        write_events(session, [event("STATUS state=ARMED")])
        previous = Path.cwd()
        os.chdir(self.root)
        try:
            with patch("replay_records.NativeGate") as gate:
                gate.return_value.provenance.return_value = {}
                replay = {"checked_frames": 0, "checked_windows": 0, "mismatches": [], "scope": "test",
                          "gate_sha256": "0", "adapter_sha256": "0"}
                with patch("replay_records.replay_sessions", return_value=replay):
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(main(["captures", "--output", "report/corpus.json"]), 0)
        finally:
            os.chdir(previous)
        report = json.loads((self.root / "report" / "corpus.json").read_text())
        self.assertEqual(report["sessions"][0]["source_directory"], "captures/session-a")

    def test_report_cannot_overwrite_input_events(self):
        write_events(self.root, [event("STATUS state=ARMED")])
        before = (self.root / "events.jsonl").read_bytes()
        self.assertEqual(main([str(self.root), "--output", str(self.root / "events.jsonl")]), 2)
        self.assertEqual((self.root / "events.jsonl").read_bytes(), before)


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.receiver = Receiver(self.root, "fixture")

    def tearDown(self):
        self.receiver.finish()
        self.temporary.cleanup()

    def test_chunked_transfer_keeps_first_motion_and_exact_raw_bytes(self):
        payload, _, raw = transfer()
        self.receiver.feed(raw[:19])
        self.receiver.feed(raw[19:])
        self.receiver.finish()
        self.assertEqual((self.root / "serial.raw").read_bytes(), raw)
        self.assertEqual(next(self.root.glob("*.jpg")).read_bytes(), payload)
        result = analyze_session(self.root)
        self.assertEqual(result["counts"]["detector_frame_records"], 1)
        self.assertEqual(result["counts"]["verified_photos"], 1)
        self.assertEqual(result["photos"][0]["matching_event_lines"], [1])
        self.assertFalse(result["errors"])

    def test_corrupted_transfer_keeps_raw_but_saves_no_image(self):
        _, _, raw = transfer(ending=8)
        self.receiver.feed(raw)
        self.receiver.finish()
        self.assertEqual(list(self.root.glob("*.jpg")), [])
        self.assertEqual((self.root / "serial.raw").read_bytes(), raw)
        self.assertTrue(analyze_session(self.root)["errors"])

    def test_duplicate_frame_after_boot_never_overwrites_image(self):
        _, _, raw = transfer()
        self.receiver.feed(raw + b"BOOT motion_size\n" + raw)
        self.receiver.finish()
        self.assertEqual(len(list(self.root.glob("*.jpg"))), 2)
        self.assertTrue((self.root / "frame-000007-clear-copy-002.jpg").is_file())
        result = analyze_session(self.root)
        self.assertEqual(len(result["photos"][0]["matching_event_lines"]), 1)
        self.assertEqual(len(result["photos"][1]["matching_event_lines"]), 1)

    def test_incomplete_transfer_and_unterminated_line_are_explicit(self):
        _, _, raw = transfer()
        raw = raw[:raw.index(b"PHOTO_END")] + b"partial"
        self.receiver.feed(raw)
        self.receiver.finish("operator_interrupt")
        rows = [json.loads(line) for line in (self.root / "events.jsonl").read_text().splitlines()]
        self.assertTrue(any(row.get("code") == "photo_incomplete" for row in rows))
        self.assertTrue(any(row.get("recorder_event") == "TRAILING_FRAGMENT" for row in rows))
        self.assertEqual((self.root / "serial.raw").read_bytes(), raw)

    def test_invalid_motion_and_utf8_remain_recoverable_in_raw(self):
        raw = b'MOTION {broken}\n\xff\xfe\nE (1) gdma: diagnostic\n'
        self.receiver.feed(raw)
        self.receiver.finish()
        result = analyze_session(self.root)
        self.assertEqual(len(result["errors"]), 2)
        self.assertEqual(result["counts"]["sdk_error_diagnostics"], 1)
        self.assertEqual((self.root / "serial.raw").read_bytes(), raw)

    def test_access_credentials_stay_in_raw_evidence_not_manifest_identity(self):
        raw = b"ACCESS setup_ssid=fixture password=private-fixture username=admin\nCONTROL_ACCESS setup_ssid=fixture password=USB_ACCESS_COMMAND\n"
        self.receiver.feed(raw)
        self.receiver.finish()
        self.assertEqual((self.root / "serial.raw").read_bytes(), raw)
        self.assertIn("private-fixture", (self.root / "events.jsonl").read_text())
        self.assertEqual(self.receiver.observed_identity_messages, [])

    def test_recorder_cli_closes_manifest_with_verifiable_artifacts(self):
        _, _, raw = transfer()
        raw += b"E (1) camera: fixture diagnostic\n"

        class SerialFixture:
            def __init__(self):
                self.is_open = False
                self.payload = raw

            def open(self):
                self.is_open = True

            def close(self):
                self.is_open = False

            def read(self, size):
                payload, self.payload = self.payload, b""
                return payload

        session = self.root / "cli-session"
        ticks = itertools.count(0, 0.05)
        serial_fixture = types.SimpleNamespace(Serial=SerialFixture, SerialException=OSError)
        with patch.dict(sys.modules, {"serial": serial_fixture}), patch.object(record_photos.time, "monotonic", side_effect=lambda: next(ticks)), contextlib.redirect_stdout(io.StringIO()):
            status = record_photos.main(["--output", str(session), "--seconds", "0.2", "--label", "fixture"])
        self.assertEqual(status, 0)
        result = analyze_session(session)
        self.assertEqual(result["errors"], [])
        self.assertGreaterEqual(result["run_manifest"]["verified_artifacts"], 6)
        self.assertEqual(result["counts"]["sdk_error_diagnostics"], 1)
        manifest = json.loads((session / "run_manifest.json").read_text())
        self.assertTrue(manifest["closed"])
        self.assertFalse(manifest["flashed_binary_identity"]["verified"])
        self.assertTrue(manifest["local_source_snapshot"])

    def test_malformed_filename_state_cannot_escape_output(self):
        _, meta, _ = transfer()
        meta["state"] = "../../bad"
        self.receiver.feed(("PHOTO_BEGIN " + json.dumps(meta) + "\n").encode())
        self.receiver.finish()
        self.assertEqual(list(self.root.glob("*.jpg")), [])
        self.assertTrue(analyze_session(self.root)["errors"])


@unittest.skipUnless(shutil.which("c++"), "C++17 compiler required for native replay tests")
class NativeReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.native = NativeGate(cls.build.name)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def analyze(self, rows):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_events(root, rows)
            return analyze_session(root)

    def test_fifth_frame_confirms_using_shared_header(self):
        session = self.analyze([event("PIR_TRIGGER")] + growing_frames() + [event("BURST_END frames=5 large_confirmed=1")])
        result = replay_sessions([session], self.native)
        self.assertEqual(result["checked_frames"], 5)
        self.assertEqual(result["mismatches"], [])
        self.assertEqual(session["windows"][0]["replay"]["status"], "matched_complete_window")

    def test_qualifying_tenth_frame_can_have_alarm_start_denied(self):
        rows = [frame(i, 0, "CLEAR", history=0) for i in range(1, 6)] + growing_frames(start=6)
        for row in rows:
            row["deterrent_started"] = False
        session = self.analyze([event("PIR_TRIGGER")] + rows + [event("BURST_END frames=10 large_confirmed=0")])
        result = replay_sessions([session], self.native)
        self.assertEqual(result["checked_frames"], 10)
        self.assertEqual(result["mismatches"], [])
        self.assertEqual(session["windows"][0]["replay"]["status"], "matched_complete_window")

    def test_burst_end_must_agree_with_reported_alarm_start(self):
        rows = growing_frames()
        for row in rows:
            row["deterrent_started"] = row["state"] == "LARGE_CHANGE"
        session = self.analyze([event("PIR_TRIGGER")] + rows + [event("BURST_END frames=5 large_confirmed=0")])
        result = replay_sessions([session], self.native)
        self.assertEqual(len(result["mismatches"]), 1)
        difference = result["mismatches"][0]["differences"]["burst_confirmation"]
        self.assertEqual(difference["basis"], "deterrent_started")
        self.assertTrue(difference["terminal_frame"])

    def test_arm_between_prefixes_prevents_combined_five_frame_acceptance(self):
        rows = [event("PIR_TRIGGER")] + growing_frames()[:3] + [event("ARMED manual_rearm=1 pir=0"), event("PIR_TRIGGER"),
                frame(4, 1354), frame(5, 1567, history=2, gain=213, percent=15)]
        session = self.analyze(rows)
        result = replay_sessions([session], self.native)
        self.assertEqual(result["checked_frames"], 5)
        self.assertEqual(result["mismatches"], [])
        self.assertEqual([w["completeness"] for w in session["windows"]], ["aborted", "truncated"])
        self.assertTrue(all(w["replay"]["status"] == "matched_observed_prefix" for w in session["windows"]))

    def test_missing_frame_or_missing_trigger_is_not_replayed(self):
        missing_frame = self.analyze([event("PIR_TRIGGER")] + growing_frames()[1:] + [event("BURST_END frames=5 large_confirmed=1")])
        missing_trigger = self.analyze(growing_frames() + [event("BURST_END frames=5 large_confirmed=1")])
        result = replay_sessions([missing_frame, missing_trigger], self.native)
        self.assertEqual(result["checked_frames"], 0)
        self.assertEqual(len(result["skipped_windows"]), 2)

    def test_first_frame_generation_mismatch_is_not_replayed(self):
        records = growing_frames()
        for item in records:
            item["control_generation"] = 3
        session = self.analyze([event("PIR_TRIGGER generation=2")] + records +
                               [event("BURST_END frames=5 large_confirmed=1")])
        result = replay_sessions([session], self.native)
        self.assertEqual(result["checked_frames"], 0)
        self.assertEqual(len(result["skipped_windows"]), 1)
        self.assertIn("control_generation_changed", result["skipped_windows"][0]["reason"])

    def test_legacy_configuration_is_not_judged_by_current_gate(self):
        session = self.analyze([event("PIR_TRIGGER"), frame(1, 500, "CONFIRMING", cutoff=5), event("BURST_END frames=1 large_confirmed=0")])
        result = replay_sessions([session], self.native)
        self.assertEqual(result["checked_frames"], 0)
        self.assertIn("legacy", result["skipped_windows"][0]["reason"])

    def test_mutated_growth_claim_is_a_replay_mismatch(self):
        rows = growing_frames()
        rows[-1]["growth_pixels"] = 846
        session = self.analyze([event("PIR_TRIGGER")] + rows + [event("BURST_END frames=5 large_confirmed=1")])
        result = replay_sessions([session], self.native)
        self.assertEqual(len(result["mismatches"]), 1)
        self.assertEqual(result["mismatches"][0]["differences"]["growth_pixels"]["replayed"], 847)

    def test_configuration_mismatch_is_explicitly_skipped(self):
        rows = growing_frames()
        for row in rows:
            row["growth_required_percent"] = 30
        session = self.analyze([event("PIR_TRIGGER")] + rows + [event("BURST_END frames=5 large_confirmed=1")])
        result = replay_sessions([session], self.native)
        self.assertEqual(result["checked_frames"], 0)
        self.assertIn("configuration", result["skipped_windows"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
