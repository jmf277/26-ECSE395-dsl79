"""Record exact serial bytes, device events and checksum-verified photo transfers.

Recorder schema 2 separates detector measurements, placeholders, SDK diagnostics
and software alarm commands. It does not verify physical sound or light output.
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from recording import (SCHEMA_VERSION, analyze_session, device_metadata, fnv1a,
                       sha256_file, strict_json, summary_from_analysis,
                       validate_frame, validate_photo_metadata)

TOOLS = Path(__file__).resolve().parent
REPOSITORY = TOOLS.parent
MAX_ENCODED_PHOTO = 700000
MAX_SERIAL_LINE = 800000


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def save_photo(root, metadata, encoded, ending):
    """Verify a transfer before saving; never overwrite a previous capture."""
    validate_photo_metadata(metadata)
    if str(metadata["frame"]) != ending.strip():
        raise ValueError("Photo frame number mismatch")
    photo = base64.b64decode("".join(encoded), validate=True)
    if len(photo) != metadata["jpeg_bytes"] or fnv1a(photo).lower() != metadata["fnv1a"].lower():
        raise ValueError("Photo length/checksum mismatch")
    if not photo.startswith(b"\xff\xd8") or not photo.endswith(b"\xff\xd9"):
        raise ValueError("Incomplete JPEG")
    stem = f"frame-{metadata['frame']:06d}-{metadata['state'].lower()}"
    name, copy = stem, 1
    while (root / f"{name}.jpg").exists() or (root / f"{name}.json").exists():
        copy += 1
        name = f"{stem}-copy-{copy:03d}"
    # Temporary files make a failed write distinguishable from a completed pair.
    jpeg_temp, json_temp = root / f"{name}.jpg.part", root / f"{name}.json.part"
    jpeg_temp.write_bytes(photo)
    json_temp.write_text(json.dumps(metadata, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    jpeg_temp.replace(root / f"{name}.jpg")
    json_temp.replace(root / f"{name}.json")
    return name


def write_gallery(root, photos):
    cards = []
    for name, info in photos:
        box = info.get("bbox", [0, 0, 0, 0])
        grid = info.get("grid", [80, 60])
        overlay = ""
        if (isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box)
                and isinstance(grid, list) and len(grid) == 2 and all(isinstance(v, (int, float)) and v > 0 for v in grid)):
            x, y, w, h = box
            if w > 0 and h > 0:
                border = "#b91c1c" if info["state"] == "LARGE_CHANGE" else "#b45309"
                overlay = f'<div class="box" style="left:{x/grid[0]*100}%;top:{y/grid[1]*100}%;width:{w/grid[0]*100}%;height:{h/grid[1]*100}%;border-color:{border}"></div>'
        placeholder = info["state"] in {"SETTLING", "CALIBRATING", "CAMERA_TEST"}
        area_value = info.get("area_percent")
        area = ("placeholder; not a detector measurement" if placeholder else
                f"{area_value:.3f}% changed area" if isinstance(area_value, (int, float)) else "area not reported")
        growth = f' | growth {info["growth_percent"]}% across {info["growth_frames"]} retained frames' if "growth_percent" in info and not placeholder else ""
        cards.append(f'<article><div class="image"><img loading="lazy" alt="Camera frame {info["frame"]}" src="{name}.jpg">{overlay}</div><p>Frame {info["frame"]}: {html.escape(info["state"])} | {area}{growth}</p><a href="{name}.jpg">Original JPEG</a> · <a href="{name}.json">Metadata</a></article>')
    page = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Recorded camera frames</title><style>body{font:16px/1.5 system-ui;margin:24px;background:#f3f4f6;color:#1f2937}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:20px}article{background:white;padding:12px}.image{position:relative;line-height:0}img{width:100%;height:auto}.box{position:absolute;border:2px solid;box-sizing:border-box}a{color:#0369a1}</style><h1>Recorded camera frames</h1><p>Original received image bytes are retained. Boxes bound the largest changed region; they are not animal recognition boxes. Gallery order alone does not establish a continuous qualification window: consult events.jsonl for PIR triggers, resets and burst endings. Frames are not independent accuracy trials. Alarm logs report commands, not measured sound or light output.</p><p><a href="summary.json">Summary</a> · <a href="run_manifest.json">Run manifest</a> · <a href="events.jsonl">Events</a></p><main>'''
    (root / "index.html").write_text(page + "".join(cards) + "</main></html>\n", encoding="utf-8")


class Receiver:
    """Streaming serial receiver, testable without a board or pyserial."""
    def __init__(self, root, label, *, clock=time.monotonic, now=utc_now):
        self.root, self.label, self.clock, self.now = Path(root), label, clock, now
        self.started = clock()
        self.raw = (self.root / "serial.raw").open("wb")
        self.events = (self.root / "events.jsonl").open("w", encoding="utf-8")
        self.buffer = bytearray()
        self.offset = 0
        self.sequence = 0
        self.pending = None
        self.parts = []
        self.encoded_length = 0
        self.photos = []
        self.last_motion = {}
        self.orphan_photo = False
        self.observed_configurations = []
        self.observed_identity_messages = []
        self.closed = False

    def emit(self, row, span=None):
        self.sequence += 1
        item = dict(row)
        item["_host"] = {"sequence": self.sequence, "received_utc": self.now(),
                         "elapsed_s": round(self.clock() - self.started, 6),
                         "raw_start": span[0] if span else None, "raw_end": span[1] if span else None}
        self.events.write(json.dumps(item, allow_nan=False) + "\n")
        self.events.flush()
        return item

    def error(self, code, detail, span=None):
        return self.emit({"recorder_event": "ERROR", "code": code, "detail": detail}, span)

    def command_sent(self, command):
        self.emit({"recorder_event": "HOST_COMMAND", "command": command,
                   "meaning": "host transmission request; device acknowledgment is a separate event"})

    def clear_pending(self):
        self.pending, self.parts, self.encoded_length = None, [], 0

    def feed(self, data):
        if self.closed:
            raise ValueError("Receiver is closed")
        self.raw.write(data)
        self.raw.flush()
        self.buffer.extend(data)
        while b"\n" in self.buffer:
            raw, _, remaining = self.buffer.partition(b"\n")
            self.buffer = bytearray(remaining)
            span = (self.offset, self.offset + len(raw) + 1)
            self.offset = span[1]
            if len(raw) > MAX_SERIAL_LINE:
                self.error("serial_line_too_long", "Oversized serial line retained in serial.raw but not decoded", span)
                self.clear_pending()
                continue
            self.process_line(bytes(raw), span)
        if len(self.buffer) > MAX_SERIAL_LINE:
            span = (self.offset, self.offset + len(self.buffer))
            self.error("serial_line_too_long", "Unterminated oversized serial data retained in serial.raw", span)
            self.offset = span[1]
            self.buffer.clear()
            self.clear_pending()

    def process_line(self, raw, span):
        try:
            line = raw.decode("utf-8").strip()
        except UnicodeDecodeError:
            self.error("invalid_utf8", "Undecodable serial line retained exactly in serial.raw", span)
            return
        if not line:
            return
        if line.startswith("PHOTO_BEGIN "):
            if self.pending is not None:
                self.error("photo_interrupted", "New PHOTO_BEGIN interrupted the preceding transfer", span)
            self.clear_pending()
            self.orphan_photo = False
            try:
                candidate = strict_json(line[12:])
                validate_photo_metadata(candidate)
                self.pending = candidate
                self.emit({"recorder_event": "PHOTO_BEGIN", "frame": candidate["frame"], "metadata": candidate}, span)
            except (ValueError, TypeError, KeyError) as exc:
                self.error("invalid_photo_header", str(exc), span)
            return
        if line.startswith("PHOTO_DATA "):
            if self.pending is None:
                if not self.orphan_photo:
                    self.emit({"recorder_event": "PHOTO_FRAGMENT", "detail": "Photo data without a retained PHOTO_BEGIN; possibly attached mid-transfer"}, span)
                    self.orphan_photo = True
            else:
                part = line[11:]
                self.parts.append(part)
                self.encoded_length += len(part)
                if self.encoded_length > MAX_ENCODED_PHOTO:
                    self.error("photo_oversized", "Encoded photo transfer exceeded its limit", span)
                    self.clear_pending()
            return
        if line.startswith("PHOTO_END "):
            if self.pending is None:
                self.emit({"recorder_event": "PHOTO_FRAGMENT", "detail": "PHOTO_END without a retained complete transfer"}, span)
            else:
                try:
                    metadata = self.pending
                    matching = self.last_motion.get(metadata["frame"])
                    sequence = matching["_host"]["sequence"] if matching and device_metadata(matching) == metadata else None
                    info = dict(metadata, host_received=self.now(), test_label=self.label,
                                record_schema=SCHEMA_VERSION, motion_event_sequence=sequence)
                    name = save_photo(self.root, info, self.parts, line[10:])
                    self.photos.append((name, info))
                    self.emit({"recorder_event": "PHOTO_SAVED", "metadata_file": name + ".json", "image_file": name + ".jpg",
                               "motion_event_sequence": sequence}, span)
                except (ValueError, TypeError, KeyError) as exc:
                    self.error("photo_rejected", str(exc), span)
            self.clear_pending()
            self.orphan_photo = False
            return
        if line.startswith("MOTION "):
            try:
                item = strict_json(line[7:])
                validate_frame(item, detector=item.get("state") in {"CLEAR", "SMALL_CHANGE", "CONFIRMING", "WAITING_FOR_GROWTH", "LARGE_CHANGE"})
                stored = self.emit(item, span)
                self.last_motion[item["frame"]] = stored
                configuration = {key: item[key] for key in ("grid", "cutoff_percent", "growth_window", "growth_required_percent", "growth_min_gain_pixels") if key in item}
                if configuration not in self.observed_configurations:
                    self.observed_configurations.append(configuration)
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                self.error("invalid_motion", str(exc), span)
            return
        self.emit({"device_message": line}, span)
        if line.startswith(("BOOT", "READY", "APPROACH_SELFTEST", "CONTROL ")):
            # Keep observed configuration/identity messages apart from local
            # source hashes: neither asserts a verified flashed binary hash.
            if line not in self.observed_identity_messages:
                self.observed_identity_messages.append(line)
        if line.startswith("BOOT"):
            if self.pending is not None:
                self.error("photo_interrupted_by_boot", "BOOT interrupted a photo transfer", span)
            self.clear_pending()
            self.last_motion.clear()

    def finish(self, reason="duration_limit"):
        if self.closed:
            return
        if self.pending is not None:
            self.error("photo_incomplete", "Final photo transfer incomplete; no image was saved")
            self.clear_pending()
        if self.buffer:
            self.emit({"recorder_event": "TRAILING_FRAGMENT", "bytes": len(self.buffer),
                       "detail": "Final unterminated line is preserved in serial.raw"},
                      (self.offset, self.offset + len(self.buffer)))
        self.emit({"recorder_event": "RECORDING_END", "reason": reason})
        self.raw.close()
        self.events.close()
        self.closed = True
        write_gallery(self.root, self.photos)


def source_snapshot(extra_files=()):
    paths = [TOOLS / "record_photos.py", TOOLS / "recording.py", TOOLS / "requirements.txt",
             REPOSITORY / "firmware/motion-size/src/approach_gate.hpp",
             REPOSITORY / "firmware/motion-size/src/app_main.cpp",
             REPOSITORY / "firmware/motion-size/platformio.ini"]
    results = []
    for path in [*paths, *extra_files]:
        path = Path(path).resolve()
        if path.is_file():
            label = str(path.relative_to(REPOSITORY)) if path.is_relative_to(REPOSITORY) else path.name
            results.append({"path": label, "sha256": sha256_file(path), "bytes": path.stat().st_size})
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM5")
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--label", default="test")
    parser.add_argument("--output", type=Path, help="New session directory; must not already exist")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--calibrate", action="store_true", help="Send BASELINE; clear the camera view first")
    action.add_argument("--arm", action="store_true", help="Send ARM for a controlled test; device policy still governs arming")
    parser.add_argument("--notes", default="", help="Operator-provided setup notes, e.g. target and whether the buzzer is connected")
    parser.add_argument("--config-file", type=Path, help="Hash a supplied configuration file; does not apply it to the board")
    parser.add_argument("--firmware-binary", type=Path, help="Hash a supplied binary; does not prove that it was flashed")
    args = parser.parse_args(argv)
    if not 0 < args.seconds <= 3600:
        parser.error("--seconds must be between 0 and 3600")
    for path in (args.config_file, args.firmware_binary):
        if path and not path.is_file():
            parser.error(f"File does not exist: {path}")
    try:
        import serial
    except ImportError:
        parser.error("pyserial is required for a live recording: python -m pip install -r tools/requirements.txt")
    label = re.sub(r"[^a-zA-Z0-9_-]", "-", args.label)[:60] or "test"
    root = (args.output or TOOLS / "captures" / (datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + label)).resolve()
    try:
        root.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error(f"Output already exists; choose a new directory: {root}")
    started = utc_now()
    command = "BASELINE" if args.calibrate else "ARM" if args.arm else None
    manifest = {"schema_version": SCHEMA_VERSION, "label": label, "started_utc": started, "closed": False, "artifacts": [],
                "recorder_configuration": {"port": args.port, "baudrate": 115200, "requested_seconds": args.seconds,
                                           "requested_command": command, "photo_grace_seconds": 5,
                                           "max_jpeg_bytes": 500000, "max_encoded_photo_bytes": MAX_ENCODED_PHOTO},
                "operator_notes": args.notes,
                "local_source_snapshot": source_snapshot([p for p in (args.config_file, args.firmware_binary) if p]),
                "flashed_binary_identity": {"verified": False, "note": "Local source or supplied binary hashes do not verify the running board binary."}}
    manifest_path = root / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    receiver = Receiver(root, label)
    connection = serial.Serial()
    connection.port, connection.baudrate, connection.timeout = args.port, 115200, 0.2
    connection.dtr = connection.rts = False
    reason, interrupted = "duration_limit", False
    print(f"Recording to {root}", flush=True)
    try:
        connection.open()
        # Do not clear or discard the first line: attaching mid-transfer is
        # explicitly represented and all received bytes remain in serial.raw.
        if command:
            receiver.command_sent(command)
            connection.write((command + "\n").encode("ascii"))
            print(f"Sent {command}; consult device acknowledgment and policy state.", flush=True)
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline or (receiver.pending is not None and time.monotonic() < deadline + 5):
            receiver.feed(connection.read(8192))
    except KeyboardInterrupt:
        reason, interrupted = "operator_interrupt", True
    except (serial.SerialException, OSError) as exc:
        reason = "serial_error"
        receiver.error("serial_error", str(exc))
    finally:
        if connection.is_open:
            connection.close()
        receiver.finish(reason)
        analysis = analyze_session(root, read_saved_summary=False, verify_manifest=False)
        summary = summary_from_analysis(analysis, label)
        summary["recording_end_reason"] = reason
        (root / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        manifest.update({"closed": True, "ended_utc": utc_now(), "end_reason": reason,
                         "observed_metadata_configurations": receiver.observed_configurations,
                         "observed_identity_messages": receiver.observed_identity_messages,
                         "artifacts": [{"path": f.name, "sha256": sha256_file(f), "bytes": f.stat().st_size}
                                       for f in sorted(root.iterdir()) if f.is_file() and f != manifest_path]})
        manifest_path.write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        print(json.dumps(summary, indent=2), flush=True)
        print(f"Gallery: {root / 'index.html'}", flush=True)
    if interrupted:
        return 130
    return 1 if analysis["errors"] or analysis["application_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
