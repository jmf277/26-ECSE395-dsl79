"""Read detector recordings without converting frame counts into accuracy claims.

Legacy event rows are accepted. Recorder schema 2 adds host receipts and recorder
messages while leaving device MOTION fields and device_message rows readable.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

SCHEMA_VERSION = 2
DETECTOR_STATES = frozenset({"CLEAR", "SMALL_CHANGE", "CONFIRMING", "WAITING_FOR_GROWTH", "LARGE_CHANGE"})
CALIBRATION_STATES = frozenset({"SETTLING", "CALIBRATING"})
HOST_FIELDS = frozenset({"host_received", "test_label", "record_schema", "motion_event_sequence", "_host"})
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SDK_DIAGNOSTIC = re.compile(r"^([EW])\s+\(\s*\d+\)")
CONTROL_RESETS = frozenset({"BOOT", "CONFIG_SAVED", "CALIBRATION_REQUEST", "REARM"})


def strict_json(text):
    def invalid_constant(value):
        raise ValueError(f"Non-finite JSON number: {value}")
    return json.loads(text, parse_constant=invalid_constant)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fnv1a(data):
    value = 2166136261
    for byte in data:
        value = ((value ^ byte) * 16777619) & 0xffffffff
    return f"{value:08x}"


def is_integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def validate_frame(item, *, detector=False):
    """Validate data before it reaches filenames or the native gate adapter."""
    if not isinstance(item, dict):
        raise ValueError("Frame metadata must be an object")
    frame = item.get("frame")
    if not is_integer(frame) or not 0 <= frame <= 0xffffffff:
        raise ValueError("Invalid frame number")
    if not isinstance(item.get("state"), str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", item["state"]):
        raise ValueError("Invalid frame state")
    for key in ("control_generation", "capture_uptime_us", "decision_uptime_us", "utc_capture_us"):
        if key in item and (not is_integer(item[key]) or not 0 <= item[key] <= 0xffffffffffffffff):
            raise ValueError(f"Invalid {key}")
    for key in ("clock_valid", "deterrent_started"):
        if key in item and not isinstance(item[key], bool):
            raise ValueError(f"Invalid {key}; expected Boolean")
    if item.get("deterrent_started") is True and item["state"] != "LARGE_CHANGE":
        raise ValueError("deterrent_started requires a LARGE_CHANGE gate state")
    if "capture_uptime_us" in item and "decision_uptime_us" in item and item["decision_uptime_us"] < item["capture_uptime_us"]:
        raise ValueError("Decision time precedes capture completion")
    if detector:
        if item.get("grid") != [80, 60]:
            raise ValueError("Detector grid must be 80 by 60")
        box = item.get("bbox")
        if not isinstance(box, list) or len(box) != 4 or not all(is_integer(v) for v in box):
            raise ValueError("Invalid bounding box")
        x, y, w, h = box
        pixels = item.get("changed_pixels")
        if not is_integer(pixels) or not 0 <= pixels <= 4800:
            raise ValueError("Invalid changed-pixel area")
        if min(x, y, w, h) < 0 or x + w > 80 or y + h > 60 or pixels > w * h:
            raise ValueError("Region exceeds its grid or bounding box")
        area = item.get("area_percent")
        if isinstance(area, bool) or not isinstance(area, (int, float)) or not math.isfinite(area):
            raise ValueError("Invalid area percentage")
        if abs(area - pixels * 100 / 4800) > 0.000501:
            raise ValueError("Area percentage disagrees with changed_pixels")
        if "growth_frames" in item:
            for key in ("growth_frames", "growth_percent", "growth_pixels", "growth_window", "growth_required_percent", "growth_min_gain_pixels"):
                if not is_integer(item.get(key)):
                    raise ValueError(f"Invalid or missing {key}")
    return item


def validate_photo_metadata(item):
    validate_frame(item)
    if not is_integer(item.get("jpeg_bytes")) or not 0 < item["jpeg_bytes"] <= 500000:
        raise ValueError("Invalid JPEG length")
    if not isinstance(item.get("fnv1a"), str) or not re.fullmatch(r"[0-9a-fA-F]{8}", item["fnv1a"]):
        raise ValueError("Invalid FNV-1a checksum")
    return item


def device_metadata(item):
    return {k: v for k, v in item.items() if k not in HOST_FIELDS}


def control_payload(message):
    clean = ANSI.sub("", message).strip()
    if not clean.startswith("CONTROL "):
        return None
    try:
        result = strict_json(clean[8:])
        return result if isinstance(result, dict) else None
    except (ValueError, TypeError):
        return None


def fields(message):
    return dict(re.findall(r"\b([A-Za-z_]+)=([^\s]+)", message))


def analyze_session(directory, *, read_saved_summary=True, verify_manifest=True):
    """Analyze observed windows; never stitch photo-only records into a streak.

    A PIR_TRIGGER opens a window. A BURST_END explicitly closes one; ARM,
    calibration, boot, policy cancellation and capture errors interrupt it.
    An EOF window is a partial prefix. Frames without a trigger are unbounded
    records and cannot establish their preceding gate history.
    """
    root = Path(directory).resolve()
    path = root / "events.jsonl"
    errors, warnings, frames, windows, transitions = [], [], [], [], []
    messages, recorder_events, application_errors, sdk_diagnostics = [], [], [], []
    active = None
    last_status = None
    unbounded = []
    counts = Counter({key + "_frame_records": 0 for key in ("detector", "calibration", "diagnostic", "unknown")})
    line_count = 0
    epoch = 0
    control_generation = None
    pending_reset = None

    def issue(collection, code, line, detail):
        collection.append({"code": code, "line": line, "detail": detail})

    def message_generation(message, line, event_name):
        value = fields(message).get("generation")
        if value is None:
            return None
        try:
            if not re.fullmatch(r"[0-9]+", value):
                raise ValueError("Invalid generation syntax")
            generation = int(value)
            if generation > 0xffffffffffffffff:
                raise ValueError("Generation is outside its unsigned 64-bit range")
            return generation
        except ValueError:
            code = f"invalid_{event_name.lower()}_generation"
            if active:
                active["issues"].append(code)
            issue(errors, code, line, f"{event_name} generation must be a nonnegative unsigned 64-bit integer")
            return None

    def close_window(reason, line, *, declared_count=None, confirmed=None):
        nonlocal active
        if active is None:
            if reason == "BURST_END":
                # A clipped recording may begin after its PIR_TRIGGER. Retain
                # the end but do not invent an initial gate state.
                observed = list(unbounded)
                active = {"number": len(windows) + 1, "start_line": None, "start_observed": False,
                          "frames": observed, "issues": ["missing_trigger"], "epoch": epoch}
                for frame in observed:
                    frame["window"] = active["number"]
                unbounded.clear()
            else:
                return
        active["end_line"] = line
        active["end_reason"] = reason
        active["declared_frames"] = declared_count
        active["firmware_confirmed"] = confirmed
        if reason == "BURST_END":
            if declared_count != len(active["frames"]):
                active["issues"].append("frame_count_mismatch")
                issue(warnings, "window_frame_count_mismatch", line,
                      f"Window {active['number']}: declared {declared_count}, retained {len(active['frames'])}")
            active["completeness"] = "complete" if active["start_observed"] and not active["issues"] else "incomplete"
        elif reason == "END_OF_RECORDING":
            active["completeness"] = "truncated"
        else:
            active["completeness"] = "aborted"
        windows.append(active)
        active = None

    with path.open(encoding="utf-8") as stream:
        for line_count, raw in enumerate(stream, 1):
            try:
                row = strict_json(raw)
                if not isinstance(row, dict):
                    raise ValueError("Event must be an object")
            except (ValueError, TypeError) as exc:
                issue(errors, "invalid_event_json", line_count, str(exc))
                if active:
                    active["issues"].append("invalid_event_json")
                continue
            if "frame" in row and "recorder_event" not in row:
                try:
                    validate_frame(row, detector=row.get("state") in DETECTOR_STATES)
                except ValueError as exc:
                    issue(errors, "invalid_frame_metadata", line_count, str(exc))
                    if active:
                        active["issues"].append("invalid_frame_metadata")
                    continue
                frame_generation = row.get("control_generation")
                if pending_reset and (pending_reset["side"] != "control"
                                      or pending_reset["generation"] is None or frame_generation is None
                                      or frame_generation >= pending_reset["generation"]):
                    # New work makes an unmatched earlier acknowledgment stale.
                    # An old capture ticket may still print after cancellation.
                    pending_reset = None
                state = row["state"]
                kind = ("detector" if state in DETECTOR_STATES else
                        "calibration" if state in CALIBRATION_STATES else
                        "diagnostic" if state == "CAMERA_TEST" else "unknown")
                counts[kind + "_frame_records"] += 1
                frame = {"line": line_count, "metadata": row, "kind": kind, "window": None, "epoch": epoch}
                frames.append(frame)
                if kind == "detector":
                    if active:
                        expected_generation = active["control_generation"]
                        generation_mismatch = (frame_generation is not None and expected_generation is not None
                                               and frame_generation != expected_generation)
                        if generation_mismatch:
                            active["issues"].append("control_generation_changed")
                            issue(warnings, "control_generation_changed", line_count,
                                  f"Frame generation {frame_generation} differs from window generation {expected_generation} without an observed reset")
                        elif expected_generation is None and frame_generation is not None:
                            active["control_generation"] = frame_generation
                        if active["frames"]:
                            previous = active["frames"][-1]["metadata"]
                            if row["frame"] != previous["frame"] + 1:
                                active["issues"].append("frame_sequence_gap")
                                issue(warnings, "frame_sequence_gap", line_count, "Missing, repeated or out-of-order frame inside a window")
                            if not generation_mismatch and frame_generation != previous.get("control_generation"):
                                active["issues"].append("control_generation_changed")
                                issue(warnings, "control_generation_changed", line_count, "Generation changed without an observed window reset")
                        frame["window"] = active["number"]
                        active["frames"].append(frame)
                        if control_generation is None and frame_generation is not None:
                            control_generation = frame_generation
                    else:
                        unbounded.append(frame)
                elif kind == "calibration":
                    close_window("CALIBRATION_FRAME", line_count)
                    unbounded.clear()
                elif active:
                    active["issues"].append("non_detector_frame_inside_window")
                continue
            if "recorder_event" in row:
                recorder_events.append({"line": line_count, **row})
                if row["recorder_event"] == "ERROR":
                    issue(errors, "recorder_error", line_count, row.get("detail", row.get("code", "Unspecified recorder error")))
                    if active and row.get("code") in {"invalid_motion", "invalid_utf8", "serial_line_too_long"}:
                        active["issues"].append("recorder_lost_measurement")
                elif row["recorder_event"] in {"TRAILING_FRAGMENT", "PHOTO_FRAGMENT"}:
                    issue(warnings, "partial_serial_capture", line_count, row.get("detail", row["recorder_event"]))
                continue
            if not isinstance(row.get("device_message"), str):
                issue(warnings, "unknown_event_record", line_count, "Preserved record has no recognized event payload")
                if active:
                    active["issues"].append("unknown_event_record")
                continue
            original = row["device_message"]
            message = ANSI.sub("", original).strip()
            messages.append({"line": line_count, "message": original})
            sdk = SDK_DIAGNOSTIC.match(message)
            if sdk:
                sdk_diagnostics.append({"line": line_count, "level": sdk.group(1), "message": original})
            control = control_payload(message)
            if message.startswith("CONTROL ") and (control is None or not isinstance(control.get("event"), str)):
                issue(errors, "invalid_control_message", line_count, "CONTROL must contain a JSON object with a string event")
                if active:
                    active["issues"].append("invalid_control_message")
                control = None
            if message.startswith("ERROR") or (control and control.get("event") == "ERROR"):
                application_errors.append({"line": line_count, "message": original})
            if message.startswith("STATUS "):
                last_status = {"line": line_count, "message": original}
            reset = None
            generation_changed = False
            if control and "generation" in control:
                generation = control["generation"]
                if not is_integer(generation) or generation < 0:
                    issue(errors, "invalid_control_generation", line_count, "CONTROL generation must be a nonnegative integer")
                    if active:
                        active["issues"].append("invalid_control_generation")
                else:
                    generation_changed = control_generation is not None and generation != control_generation
                    control_generation = generation
                    if active and active["control_generation"] is None:
                        active["control_generation"] = generation
            if message.startswith("BOOT"):
                reset = "BOOT"
                epoch += 1
            elif message.startswith("ARMED manual_rearm="):
                reset = "ARM"
            elif message.startswith("CALIBRATION clear_view"):
                reset = "CALIBRATION_REQUEST"
            elif message.startswith("CALIBRATION complete"):
                reset = "CALIBRATION_COMPLETE"
            elif message.startswith(("ERROR camera_wake", "ERROR camera_capture", "ERROR jpeg_decode")):
                reset = "CAPTURE_ERROR"
            elif message.startswith("BURST_ABORT"):
                reset = "BURST_ABORT"
            elif control and control.get("event") in CONTROL_RESETS:
                reset = "CONTROL_" + control["event"]
            elif generation_changed:
                reset = "CONTROL_GENERATION_CHANGED"
            if reset:
                if reset == "BURST_ABORT":
                    control_generation = message_generation(message, line_count, "BURST_ABORT")
                    if not (pending_reset and pending_reset["side"] == "control"
                            and control_generation is not None
                            and pending_reset["generation"] == control_generation):
                        pending_reset = None
                else:
                    reset_kind = {"ARM": "REARM", "CONTROL_REARM": "REARM",
                                  "CALIBRATION_REQUEST": "CALIBRATION_REQUEST",
                                  "CONTROL_CALIBRATION_REQUEST": "CALIBRATION_REQUEST"}.get(reset)
                    known_control = control and is_integer(control.get("generation")) and control["generation"] >= 0
                    if reset_kind and control:
                        if not known_control:
                            control_generation = None
                        if pending_reset and pending_reset["side"] == "marker" and pending_reset["kind"] == reset_kind:
                            pending_reset = None
                        else:
                            pending_reset = {"side": "control", "kind": reset_kind, "generation": control_generation}
                    elif reset_kind:
                        if (pending_reset and pending_reset["side"] == "control" and pending_reset["kind"] == reset_kind
                                and control_generation is not None and pending_reset["generation"] == control_generation):
                            # Service and main-loop reports acknowledge one reset
                            # and can arrive in either order, with an abort between.
                            pending_reset = None
                        else:
                            control_generation = None
                            pending_reset = {"side": "marker", "kind": reset_kind, "generation": None}
                    else:
                        pending_reset = None
                        if not known_control:
                            # BOOT starts a new domain. Other unpaired reset
                            # markers do not identify the post-reset generation.
                            control_generation = None
                close_window(reset, line_count)
                unbounded.clear()
                transitions.append({"line": line_count, "type": reset, "message": original})
            if message.startswith("PIR_TRIGGER"):
                pending_reset = None
                if active:
                    issue(warnings, "trigger_before_end", line_count, "New trigger before the preceding window ended")
                    close_window("NEW_TRIGGER", line_count)
                unbounded.clear()
                active = {"number": len(windows) + 1, "start_line": line_count, "start_observed": True,
                          "frames": [], "issues": [], "epoch": epoch, "control_generation": control_generation}
                generation = message_generation(message, line_count, "PIR_TRIGGER")
                if generation is not None:
                    # A newly observed trigger starts fresh gate history.
                    # Its explicit generation takes precedence over earlier
                    # session observations and stays fixed for this window.
                    active["control_generation"] = control_generation = generation
            elif message.startswith("BURST_END"):
                values = fields(message)
                try:
                    n, confirmed = int(values["frames"]), int(values["large_confirmed"])
                    if n < 0 or confirmed not in (0, 1):
                        raise ValueError("Invalid burst values")
                except (KeyError, ValueError):
                    issue(errors, "invalid_burst_end", line_count, original)
                    if active:
                        active["issues"].append("invalid_burst_end")
                    n, confirmed = None, None
                close_window("BURST_END", line_count, declared_count=n, confirmed=confirmed)
            elif "MOTION {" in message:
                issue(warnings, "embedded_unparsed_motion", line_count,
                      "MOTION text embedded in a device message was not inserted into the measurement sequence")
    close_window("END_OF_RECORDING", line_count)

    photo_results = []
    for meta_path in sorted(root.glob("frame*.json")):
        photo = {"metadata_file": meta_path.name, "image_file": meta_path.with_suffix(".jpg").name}
        try:
            meta = strict_json(meta_path.read_text(encoding="utf-8"))
            validate_photo_metadata(meta)
            image_path = meta_path.with_suffix(".jpg")
            data = image_path.read_bytes()
            if len(data) != meta["jpeg_bytes"] or fnv1a(data).lower() != meta["fnv1a"].lower():
                raise ValueError("JPEG length/checksum mismatch")
            if not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
                raise ValueError("Incomplete JPEG markers")
            matches = [frame for frame in frames if device_metadata(frame["metadata"]) == device_metadata(meta)]
            if meta.get("motion_event_sequence") is not None:
                matches = [frame for frame in matches if frame["metadata"].get("_host", {}).get("sequence") == meta["motion_event_sequence"]]
            photo.update({"frame": meta["frame"], "state": meta["state"], "sha256": sha256_file(image_path),
                          "checksum_verified": True, "matching_event_lines": [frame["line"] for frame in matches]})
            if not matches:
                issue(warnings, "photo_without_matching_event", None,
                      f"{meta_path.name}: kept separate; its position in a window is not established")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            photo["checksum_verified"] = False
            issue(errors, "photo_integrity", None, f"{meta_path.name}: {exc}")
        photo_results.append(photo)
    for image_path in root.glob("frame*.jpg"):
        if not image_path.with_suffix(".json").is_file():
            issue(errors, "image_without_metadata", None, image_path.name)

    manifest_status = {"present": (root / "run_manifest.json").is_file(), "verified_artifacts": 0}
    if verify_manifest and manifest_status["present"]:
        try:
            manifest = strict_json((root / "run_manifest.json").read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("Manifest must be an object")
            if manifest.get("schema_version") != SCHEMA_VERSION:
                raise ValueError("Unsupported or missing run-manifest schema version")
            if not isinstance(manifest.get("artifacts"), list):
                raise ValueError("Manifest artifacts must be a list")
            artifact_paths = set()
            if not manifest.get("closed", False):
                issue(warnings, "open_run_manifest", None, "Run manifest does not record a closed capture")
            for artifact in manifest.get("artifacts", []):
                relative = Path(artifact["path"])
                if str(relative) in artifact_paths:
                    raise ValueError("Duplicate manifest artifact path")
                artifact_paths.add(str(relative))
                if not is_integer(artifact["bytes"]) or artifact["bytes"] < 0 or not isinstance(artifact["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"]):
                    raise ValueError("Invalid artifact length or SHA-256")
                target = (root / relative).resolve()
                if relative.is_absolute() or ".." in relative.parts or not target.is_relative_to(root):
                    raise ValueError("Manifest artifact path escapes the session directory")
                if sha256_file(target) != artifact["sha256"] or target.stat().st_size != artifact["bytes"]:
                    issue(errors, "manifest_artifact_mismatch", None, str(relative))
                else:
                    manifest_status["verified_artifacts"] += 1
            if manifest.get("closed", False) and "events.jsonl" not in artifact_paths:
                issue(errors, "manifest_missing_events", None, "Closed manifest does not hash events.jsonl")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            issue(errors, "invalid_run_manifest", None, str(exc))

    legacy_errors = []
    if read_saved_summary and (root / "summary.json").is_file():
        try:
            saved = strict_json((root / "summary.json").read_text(encoding="utf-8"))
            if saved.get("schema_version", 1) == 1:
                legacy_errors = saved.get("errors", [])
                for detail in legacy_errors:
                    issue(warnings, "legacy_recording_error", None, str(detail))
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            issue(warnings, "unreadable_saved_summary", None, str(exc))
    scored = [frame for frame in frames if frame["kind"] == "detector"]
    areas = [frame["metadata"]["area_percent"] for frame in scored]
    counts.update({"photos_saved": len(photo_results), "verified_photos": sum(p.get("checksum_verified", False) for p in photo_results),
                   "pir_trigger_messages": sum(ANSI.sub("", m["message"]).startswith("PIR_TRIGGER") for m in messages),
                   "alarm_start_messages": sum(ANSI.sub("", m["message"]).startswith("DETERRENT_ON") for m in messages),
                   "alarm_stop_messages": sum(ANSI.sub("", m["message"]).startswith("DETERRENT_OFF") for m in messages),
                   "sdk_error_diagnostics": sum(d["level"] == "E" for d in sdk_diagnostics),
                   "sdk_warning_diagnostics": sum(d["level"] == "W" for d in sdk_diagnostics),
                   "application_error_messages": len(application_errors),
                   "detector_records_without_window": sum(frame["window"] is None for frame in scored)})
    if last_status:
        last_status["followed_by_event_records"] = line_count - last_status["line"]
    return {"schema_version": SCHEMA_VERSION, "session": root.name, "source_directory": str(root),
            "events_sha256": sha256_file(path), "event_records": line_count, "counts": dict(counts),
            "detector_area_percent": {"minimum": min(areas, default=None), "maximum": max(areas, default=None)},
            "detector_state_counts": dict(Counter(frame["metadata"]["state"] for frame in scored)),
            "windows": windows, "reset_boundaries": transitions, "frames": frames, "photos": photo_results,
            "last_observed_status": last_status, "application_errors": application_errors,
            "sdk_diagnostics": sdk_diagnostics, "errors": errors, "warnings": warnings,
            "legacy_recording_errors": legacy_errors, "run_manifest": manifest_status,
            "physical_outputs": "not established by these serial records; alarm messages report software commands, not sound or light verification",
            "interpretation": "Frames are correlated records, not independent accuracy trials. No species or field-performance result is inferred."}


def summary_from_analysis(analysis, label):
    """Schema 2 names deliberately distinguish measurements from commands."""
    keep = ("schema_version", "counts", "detector_area_percent", "detector_state_counts", "last_observed_status",
            "application_errors", "sdk_diagnostics", "errors", "warnings", "physical_outputs", "interpretation")
    result = {key: analysis[key] for key in keep}
    result["label"] = label
    result["window_counts"] = dict(Counter(w["completeness"] for w in analysis["windows"]))
    result["windows"] = [{k: v for k, v in w.items() if k != "frames"} | {"retained_frames": len(w["frames"])} for w in analysis["windows"]]
    return result
