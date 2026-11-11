"""Record paired ESP32 recognition images, exact INT8 inputs and decisions.

The computer sends trial commands and saves records; it does not run the model.
Run --help for live USB recording or reconstruction from retained serial.raw.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import html
import io
import json
import math
from pathlib import Path
import queue
import re
import threading
import time
from datetime import datetime, timezone

MAX_LINE = 65536
MAX_JPEG = 1000000
INPUT_BYTES = 224 * 224 * 3
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SDK_DIAGNOSTIC = re.compile(r"^([EW])\s+\(\s*\d+\)")


def valid_command(command):
    return bool(re.fullmatch(r"CAPTURE [1-9][0-9]{0,2}|MODE (MANUAL|PIR)|LIGHT (AMBIENT|WHITE)|RECORD (FULL|JPEG)|TEST OUTPUT|INJECT DEER|ARM|DISARM|STOP|STATUS", command)) and not (
        command.startswith("CAPTURE ") and int(command[8:]) > 600)


def utc():
    return datetime.now(timezone.utc).isoformat()


def fnv1a(data):
    value = 2166136261
    for byte in data:
        value = ((value ^ byte) * 16777619) & 0xffffffff
    return f"{value:08x}"


def load_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    def reject(value):
        raise ValueError(f"nonfinite JSON constant: {value}")
    return json.loads(text, object_pairs_hook=pairs, parse_constant=reject)


def identity(item):
    boot = item.get("boot")
    if not isinstance(boot, str) or not re.fullmatch(r"[0-9a-f]{16}", boot):
        raise ValueError("invalid boot identifier")
    for key in ("trial", "frame"):
        if type(item.get(key)) is not int or not 1 <= item[key] <= 0xffffffff:
            raise ValueError(f"invalid {key}")
    return boot, item["frame"]


def expected_artifacts(item):
    """Blobs that follow a frame record: schema 2 says whether the input is sent."""
    kinds = {"jpeg"} if item["jpeg_bytes"] else set()
    if item["input_bytes"] and (item["schema"] == 1 or item.get("input_sent")):
        kinds.add("input")
    return kinds


def validate_result(item):
    identity(item)
    if type(item.get("schema")) is not int or item["schema"] not in (1, 2):
        raise ValueError("unsupported record schema")
    if item["schema"] == 2:
        if item.get("recording") not in ("full", "jpeg") or type(item.get("input_sent")) is not bool:
            raise ValueError("invalid recording level")
        if item["recording"] == "jpeg" and item["input_sent"]:
            raise ValueError("JPEG-only record must not send its input")
    if item.get("trigger") not in ("manual", "pir"):
        raise ValueError("invalid trigger")
    if "lighting" in item and item["lighting"] not in ("ambient", "white"):
        raise ValueError("invalid capture lighting")
    if "capture_lighting_ok" in item:
        if type(item["capture_lighting_ok"]) is not bool or (item.get("valid") and not item["capture_lighting_ok"]):
            raise ValueError("valid inference requires intact capture lighting")
    for key in ("valid", "committed", "present", "armed", "alarm_started"):
        if type(item.get(key)) is not bool:
            raise ValueError(f"invalid {key}")
    if not isinstance(item.get("model_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", item["model_sha256"]):
        raise ValueError("model identity missing")
    if item["valid"]:
        score = item.get("score")
        if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("invalid score")
        if item.get("input_bytes") != INPUT_BYTES or not item.get("jpeg_bytes"):
            raise ValueError("valid inference lacks declared image/input")
    elif item.get("score") is not None:
        raise ValueError("invalid inference must not carry a success score")
    if item["alarm_started"] and not (item["valid"] and item["committed"] and item["present"] and item["armed"]):
        raise ValueError("alarm contradicts decision/arming state")
    for key, upper in (("jpeg_bytes", MAX_JPEG), ("input_bytes", INPUT_BYTES)):
        if type(item.get(key)) is not int or not 0 <= item[key] <= upper:
            raise ValueError(f"invalid {key}")
    if item["input_bytes"]:
        if item["input_bytes"] != INPUT_BYTES or item.get("input_shape") != [1, 224, 224, 3] or item.get("input_dtype") != "int8" or item.get("input_layout") != "NHWC":
            raise ValueError("unexpected input tensor contract")
        if type(item.get("input_exponent")) is not int or not -128 <= item["input_exponent"] <= 127:
            raise ValueError("invalid input exponent")
    if item["jpeg_bytes"] and any(type(item.get(k)) is not int or not 1 <= item[k] <= 640 for k in ("camera_width", "camera_height")):
        raise ValueError("invalid camera dimensions")


def box_problem(item):
    """A box list the recorder cannot check is reported, never a reason to drop the frame."""
    boxes = item.get("boxes")
    if not isinstance(boxes, list) or len(boxes) > 10:
        return "box list is not a list of at most 10 boxes"
    for box in boxes:
        coords = box.get("box") if isinstance(box, dict) else None
        if (not isinstance(coords, list) or len(coords) != 4 or any(type(x) is not int for x in coords)
                or not 0 <= coords[0] <= coords[2] < item.get("camera_width", 0)
                or not 0 <= coords[1] <= coords[3] < item.get("camera_height", 0)
                or type(box.get("category")) is not int or box["category"] != 0
                or type(box.get("score")) not in (int, float) or not math.isfinite(box["score"]) or not 0 <= box["score"] <= 1):
            return "a detection box is outside the picture or malformed"
    return None


class Receiver:
    """Strict streaming parser. Partial/invalid transfers never become complete trials."""
    def __init__(self, root, on_event=None, context=None):
        self.root = Path(root)
        self.context = context or {}  # label and notes shown in the gallery
        self.timeline = []  # frame keys and markers in the order they arrived
        self.boots = {}     # boot identifier -> firmware identity from its BOOT line
        self.raw = (self.root / "serial.raw").open("xb")
        self.events = (self.root / "events.jsonl").open("x", encoding="utf-8")
        self.buffer = bytearray()
        self.frames = {}
        self.output_tests = {}
        self.injections = {}
        self.pending = None
        self.skip_blob = None  # (frame, kind) of a failed blob whose remaining chunks are ignored
        self.errors = []
        self.warnings = []
        self.diagnostics = []  # ESP-IDF E (...) and W (...) log lines: device logs, not recording errors
        self.status_replies = []
        self.closed = False
        self.discard_line = False
        self.on_event = on_event

    def emit(self, **row):
        self.events.write(json.dumps(dict(received_utc=utc(), **row), allow_nan=False) + "\n")
        self.events.flush()
        if self.on_event:
            self.on_event(row)

    def error(self, message):
        self.errors.append(message)
        self.emit(recorder_error=message)

    def warn(self, message):
        self.warnings.append(message)
        self.emit(recorder_warning=message)

    def mark(self, text):
        """An operator marker, tied to the frame received just before it."""
        last = next((entry[1] for entry in reversed(self.timeline) if entry[0] == "frame"), None)
        after = self.frames[last]["stem"] if last else None
        self.timeline.append(("marker", text, after))
        self.emit(marker=text, after_frame=after)

    def transfer_open(self):
        """True while a blob or a frame without its TRIAL_END is outstanding."""
        return self.pending is not None or any(not row["ended"] for row in self.frames.values())

    def feed(self, data):
        if self.closed:
            raise ValueError("receiver closed")
        self.raw.write(data)
        self.raw.flush()
        self.buffer.extend(data)
        while b"\n" in self.buffer:
            raw, _, rest = self.buffer.partition(b"\n")
            self.buffer = bytearray(rest)
            if self.discard_line:
                self.discard_line = False
                continue
            if len(raw) > MAX_LINE:
                self.error("oversized serial line")
                self.pending = None
                continue
            try:
                self.process(raw.decode("utf-8").strip())
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
                self.error(str(exc))
                if self.pending is not None:
                    # One error per failed transfer: its remaining chunks are ignored.
                    self.skip_blob = (self.pending[0]["frame"], self.pending[0]["kind"])
                self.pending = None
        if len(self.buffer) > MAX_LINE:
            self.error("oversized unterminated serial line")
            self.pending = None
            self.buffer.clear()
            self.discard_line = True

    def process(self, text):
        if not text:
            return
        if text.startswith("OUTPUT_TEST "):
            item = load_json(text[len("OUTPUT_TEST "):])
            if (type(item.get("schema")) is not int or item["schema"] != 1
                    or not isinstance(item.get("boot"), str) or not re.fullmatch(r"[0-9a-f]{16}", item["boot"])
                    or type(item.get("test")) is not int or not 1 <= item["test"] <= 0xffffffff
                    or item.get("event") not in ("started", "completed", "cancelled")
                    or item.get("source") != "manual_command"):
                raise ValueError("invalid independent output-test event")
            key = item["boot"], item["test"]
            if item["event"] == "started":
                if key in self.output_tests:
                    raise ValueError("duplicate output-test start")
                self.output_tests[key] = {"start": item, "end": None}
            else:
                if key not in self.output_tests or self.output_tests[key]["end"] is not None:
                    raise ValueError("orphan/duplicate output-test ending")
                self.output_tests[key]["end"] = item
            self.emit(output_test=item)
            return
        if text.startswith("INJECTION "):
            item = load_json(text[len("INJECTION "):])
            if (type(item.get("schema")) is not int or item["schema"] != 1
                    or not isinstance(item.get("boot"), str) or not re.fullmatch(r"[0-9a-f]{16}", item["boot"])
                    or type(item.get("injection")) is not int or not 1 <= item["injection"] <= 0xffffffff
                    or item.get("event") not in ("started", "completed", "cancelled")
                    or item.get("detection") != "confirmed_deer" or item.get("source") != "usb_command"
                    or type(item.get("alarm_start_us")) is not int or item["alarm_start_us"] < 0
                    or (item["event"] != "started" and item.get("ended_us") is not None and type(item["ended_us"]) is not int)
                    or (item["event"] == "completed" and type(item.get("ended_us")) is not int)):
                raise ValueError("invalid injected-detection event")
            key = item["boot"], item["injection"]
            if item["event"] == "started":
                if key in self.injections:
                    raise ValueError("duplicate injected-detection start")
                self.injections[key] = {"start": item, "end": None}
            else:
                if key not in self.injections or self.injections[key]["end"] is not None:
                    raise ValueError("orphan/duplicate injected-detection ending")
                if item["alarm_start_us"] != self.injections[key]["start"]["alarm_start_us"]:
                    raise ValueError("injected-detection ending names another alarm start")
                self.injections[key]["end"] = item
            self.emit(injection=item)
            return
        if text.startswith("TRIAL_DATA "):
            parts = text.split()
            if self.pending is None:
                key = (int(parts[1]), parts[2]) if len(parts) == 5 and parts[1].isdigit() else None
                if key is not None and key == self.skip_blob:
                    return
                self.skip_blob = key
                raise ValueError("data chunks without their blob header (recording joined during a transfer?)")
            if len(parts) != 5:
                raise ValueError("malformed data chunk")
            header, data = self.pending
            if int(parts[1]) != header["frame"] or parts[2] != header["kind"] or int(parts[3]) != len(data):
                raise ValueError("data chunk identity/offset mismatch")
            chunk = base64.b64decode(parts[4], validate=True)
            if not chunk or len(data) + len(chunk) > header["bytes"]:
                raise ValueError("data exceeds declared length")
            data.extend(chunk)
            return
        if text.startswith("TRIAL_BLOB_END "):
            parts = text.split()
            if self.pending is None and len(parts) == 3 and parts[1].isdigit() and (int(parts[1]), parts[2]) == self.skip_blob:
                self.skip_blob = None
                return
            if len(parts) != 3 or self.pending is None:
                raise ValueError("orphan blob ending")
            header, data = self.pending
            if int(parts[1]) != header["frame"] or parts[2] != header["kind"]:
                raise ValueError("blob ending identity mismatch")
            if len(data) != header["bytes"] or fnv1a(data) != header["fnv1a"]:
                raise ValueError("blob length/checksum mismatch")
            self.save_blob(header, bytes(data))
            self.pending = None
            return
        if text.startswith("TRIAL_BLOB_CANCELLED "):
            parts = text.split()
            if self.pending is None and len(parts) == 4 and parts[1].isdigit() and (int(parts[1]), parts[2]) == self.skip_blob:
                self.skip_blob = None
                return
            if len(parts) != 4 or self.pending is None:
                raise ValueError("orphan transfer cancellation")
            header, data = self.pending
            if int(parts[1]) != header["frame"] or parts[2] != header["kind"] or int(parts[3]) != len(data):
                raise ValueError("transfer cancellation identity/offset mismatch")
            self.frames[identity(header)]["cancelled_blob"] = header["kind"]
            self.pending = None
            self.emit(transfer_cancelled=dict(frame=header["frame"], kind=header["kind"], bytes_received=len(data)))
            return
        if text.startswith("TRIAL_BLOB "):
            self.skip_blob = None
            if self.pending is not None:
                self.error("previous blob interrupted")
            self.pending = None
            header = load_json(text[len("TRIAL_BLOB "):])
            key = identity(header)
            if key not in self.frames or header.get("kind") not in ("jpeg", "input"):
                raise ValueError("blob without matching result or unknown kind")
            record = self.frames[key]
            kind = header["kind"]
            if record["ended"] or kind in record["artifacts"] or header["trial"] != record["result"]["trial"]:
                raise ValueError("duplicate/late blob or trial mismatch")
            if kind not in expected_artifacts(record["result"]):
                raise ValueError("blob the frame record does not announce")
            if type(header.get("bytes")) is not int or not 0 < header["bytes"] == record["result"][kind + "_bytes"]:
                raise ValueError("blob size disagrees with inference record")
            if not isinstance(header.get("fnv1a"), str) or not re.fullmatch(r"[0-9a-f]{8}", header["fnv1a"]):
                raise ValueError("invalid blob checksum")
            self.pending = header, bytearray()
            return
        if text.startswith("TRIAL_FRAME "):
            self.skip_blob = None
            if self.pending is not None:
                self.error("new result interrupted blob")
                self.pending = None
            item = load_json(text[len("TRIAL_FRAME "):])
            validate_result(item)
            key = identity(item)
            if key in self.frames:
                raise ValueError("duplicate result identity")
            stem = f"{key[0]}-trial-{item['trial']:06d}-frame-{key[1]:06d}"
            self.frames[key] = {"stem": stem, "result": item, "artifacts": {}, "ended": False, "complete": False}
            self.timeline.append(("frame", key))
            problem = box_problem(item)
            if problem:
                self.frames[key]["warning"] = problem
                self.warn(f"{stem}: {problem}; its picture and model input are kept")
            (self.root / (stem + ".json")).write_text(json.dumps(item, indent=2, allow_nan=False) + "\n")
            self.emit(result=item)
            return
        if text.startswith("TRIAL_END "):
            self.skip_blob = None
            ending = load_json(text[len("TRIAL_END "):])
            key = identity(ending)
            if self.pending is not None:
                self.error("frame ended with incomplete blob")
                self.pending = None
            if key not in self.frames or self.frames[key]["ended"]:
                raise ValueError("orphan/duplicate frame ending")
            row = self.frames[key]
            if ending["trial"] != row["result"]["trial"] or type(ending.get("transfer_complete")) is not bool:
                raise ValueError("invalid frame ending")
            cancelled = ending.get("transfer_cancelled", False)
            if type(cancelled) is not bool or (cancelled and ending["transfer_complete"]):
                raise ValueError("invalid transfer cancellation flag")
            row["cancelled"] = cancelled
            row["complete"] = ending["transfer_complete"] and set(row["artifacts"]) == expected_artifacts(row["result"])
            row["ended"] = True
            row["ending"] = ending
            self.emit(frame_end=ending, received_complete=row["complete"])
            return
        text = ANSI.sub("", text)
        if text.startswith("BOOT ") and self.pending is not None:
            self.error("device reboot interrupted blob")
            self.pending = None
        if text.startswith("BOOT "):
            boot = re.search(r"\bboot=([0-9a-f]{16})\b", text)
            firmware = text.split(" firmware=", 1)[1] if " firmware=" in text else None
            if boot:
                try:
                    self.boots[boot.group(1)] = load_json(firmware) if firmware else None
                except ValueError:
                    self.boots[boot.group(1)] = None
        if text.startswith("TRIAL_STATUS "):
            try:
                self.status_replies.append(load_json(text[len("TRIAL_STATUS "):]))
            except ValueError:
                pass
        if SDK_DIAGNOSTIC.match(text):
            self.diagnostics.append(text)
            self.emit(device_message=text, diagnostic=text[0])
            return
        if text.startswith(("ERROR", "TRIAL_ERROR")) or "Guru Meditation" in text:
            self.error("device: " + text)
        self.emit(device_message=text)

    def save_blob(self, header, data):
        from PIL import Image
        row = self.frames[identity(header)]
        kind = header["kind"]
        suffix = ".jpg" if kind == "jpeg" else ".input.i8"
        name = row["stem"] + suffix
        if kind == "jpeg":
            try:
                with Image.open(io.BytesIO(data)) as picture:
                    if picture.format != "JPEG" or list(picture.size) != [row["result"]["camera_width"], row["result"]["camera_height"]]:
                        raise ValueError("JPEG dimensions/type disagree with result")
                    picture.load()
            except OSError as exc:
                raise ValueError("JPEG decode/verification failed") from exc
        else:
            scale = math.ldexp(255.0, row["result"]["input_exponent"])
            pixels = bytes(max(0, min(255, round((value if value < 128 else value - 256) * scale))) for value in data)
            Image.frombytes("RGB", (224, 224), pixels).save(self.root / (row["stem"] + ".input-preview.png"))
        with (self.root / name).open("xb") as output:
            output.write(data)
        row["artifacts"][kind] = {"file": name, "bytes": len(data), "fnv1a": fnv1a(data), "sha256": hashlib.sha256(data).hexdigest()}
        self.emit(saved=row["artifacts"][kind], kind=kind)

    def finish(self):
        if self.closed:
            return
        if self.pending is not None:
            self.error("recording stopped during a blob")
        if self.buffer or self.discard_line:
            self.error("recording ended with partial serial line")
        for row in self.frames.values():
            if not row["complete"] and not row.get("cancelled"):
                self.error("incomplete frame: " + row["stem"])
            (self.root / (row["stem"] + ".received.json")).write_text(json.dumps(row, indent=2, allow_nan=False) + "\n")
        for (boot, test), row in self.output_tests.items():
            if row["end"] is None:
                self.error(f"output test lacks terminal event: {boot}/{test}")
        for (boot, number), row in self.injections.items():
            if row["end"] is None:
                self.error(f"injected detection lacks terminal event: {boot}/{number}")
        summary = {
            "schema": 1, "frames": len(self.frames),
            "complete_valid_pairs": sum(row["complete"] and row["result"]["valid"] and "input" in row["artifacts"] for row in self.frames.values()),
            "complete_valid_frames": sum(row["complete"] and row["result"]["valid"] for row in self.frames.values()),
            "invalid_inferences": sum(not row["result"]["valid"] for row in self.frames.values()),
            "incomplete_frames": sum(not row["complete"] for row in self.frames.values()),
            "cancelled_transfers": sum(bool(row.get("cancelled")) for row in self.frames.values()),
            "frames_without_input": sum(bool(row["ended"] and "input" not in expected_artifacts(row["result"])) for row in self.frames.values()),
            "output_tests_started": len(self.output_tests),
            "output_tests_completed": sum(bool(row["end"] and row["end"]["event"] == "completed") for row in self.output_tests.values()),
            "output_tests_cancelled": sum(bool(row["end"] and row["end"]["event"] == "cancelled") for row in self.output_tests.values()),
            "output_tests_incomplete": sum(row["end"] is None for row in self.output_tests.values()),
            "injections_started": len(self.injections),
            "injections_completed": sum(bool(row["end"] and row["end"]["event"] == "completed") for row in self.injections.values()),
            "injections_cancelled": sum(bool(row["end"] and row["end"]["event"] == "cancelled") for row in self.injections.values()),
            "injections_incomplete": sum(row["end"] is None for row in self.injections.values()),
            "trials": self.trials(),
            "errors": self.errors,
            "warnings": self.warnings,
            "device_diagnostics": self.diagnostics,
            "scope": "Image/input transfer and reported inference/commands; not physical-output verification or recognition accuracy."
        }
        (self.root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        with (self.root / "trials.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=self.TRIAL_COLUMNS)
            writer.writeheader()
            writer.writerows(summary["trials"])
        self.gallery()
        self.raw.close();self.events.close();self.closed = True

    TRIAL_COLUMNS = ["boot", "trial", "trigger", "armed", "lighting", "recording", "frames", "valid_frames", "invalid_frames",
                     "max_score", "frames_at_threshold", "confirmed", "alarm_started", "first_started_us", "last_inferred_us"]

    def trials(self):
        """One row per trial, from the frame records in arrival order."""
        rows = {}
        for row in self.frames.values():
            r = row["result"]
            t = rows.setdefault((r["boot"], r["trial"]), dict(
                boot=r["boot"], trial=r["trial"], trigger=r["trigger"], armed=r["armed"],
                lighting=r.get("lighting", "not recorded"), recording=r.get("recording", "full"),
                frames=0, valid_frames=0, invalid_frames=0, max_score=None, frames_at_threshold=0,
                confirmed=False, alarm_started=False, first_started_us=r.get("started_us"), last_inferred_us=None))
            t["frames"] += 1
            if r["valid"]:
                t["valid_frames"] += 1
                t["max_score"] = r["score"] if t["max_score"] is None else max(t["max_score"], r["score"])
                t["frames_at_threshold"] += r["score"] >= r.get("threshold", 2)
            else:
                t["invalid_frames"] += 1
            t["confirmed"] |= r["present"]
            t["alarm_started"] |= r["alarm_started"]
            t["last_inferred_us"] = r.get("inferred_us")
        return list(rows.values())

    def frame_card(self, row):
        r, stem = row["result"], row["stem"]
        width, height = r.get("camera_width") or 1, r.get("camera_height") or 1
        figures = []
        if "jpeg" in row["artifacts"]:
            boxes = []
            for box in r.get("boxes", []) if isinstance(r.get("boxes"), list) else []:
                try:
                    x1, y1, x2, y2 = box["box"]
                    style = (f"left:{100 * x1 / width:.2f}%;top:{100 * y1 / height:.2f}%;"
                             f"width:{100 * (x2 - x1 + 1) / width:.2f}%;height:{100 * (y2 - y1 + 1) / height:.2f}%")
                    boxes.append(f'<div class="box" style="{style}"><span>{float(box["score"]):.3f}</span></div>')
                except (KeyError, TypeError, ValueError):
                    continue
            figures.append(f'<figure><div class="image"><img src="{stem}.jpg" alt="Camera JPEG of frame {r["frame"]}">{"".join(boxes)}</div>'
                           f'<figcaption>Camera JPEG, unchanged; outlines show the {len(boxes)} detection box(es) of the record with their scores</figcaption></figure>')
        if "input" in row["artifacts"]:
            figures.append(f'<figure><img src="{stem}.input-preview.png" alt="Model input preview"><figcaption>Dequantized 224×224 model input preview</figcaption></figure>')
        parts = [f"{label} {r[key] / 1000:.0f} ms" for label, key in (("capture", "capture_us"), ("decode", "decode_us"), ("model", "model_us")) if isinstance(r.get(key), (int, float))]
        if row.get("ending") and isinstance(row["ending"].get("transfer_us"), (int, float)):
            parts.append(f"transfer {row['ending']['transfer_us'] / 1000:.0f} ms")
        timing = ("Timing: " + ", ".join(parts) + ".") if parts else ""
        score = "none (invalid frame)" if r["score"] is None else f"{r['score']:.4f}"
        title = f"Trial {r['trial']}, frame {r['frame']} ({r['within']} of the trial)"
        facts = (f"Trigger {html.escape(r['trigger'])}; armed {r['armed']}; lighting {html.escape(r.get('lighting', 'not recorded'))}; "
                 f"recording {html.escape(r.get('recording', 'full'))}. Score {score}; confirmed {r['present']}; committed {r['committed']}; "
                 f"alarm commanded {r['alarm_started']}. {html.escape(timing)}")
        problems = [p for p in (r.get("error"), row.get("warning")) if p]
        if row.get("cancelled"):
            problems.append("transfer cancelled")
        elif not row["complete"]:
            problems.append("transfer incomplete")
        problem_text = f'<p class="problem">{html.escape("; ".join(problems))}</p>' if problems else ""
        return (f'<article><h2>{html.escape(title)}</h2><p>{facts}</p>{problem_text}<div class="figures">{"".join(figures)}</div>'
                f'<a href="{stem}.json">Frame record</a> · <a href="{stem}.received.json">Transfer verification</a></article>')

    def gallery(self):
        trials = self.trials()
        head = "".join(f"<th>{html.escape(c.replace('_', ' '))}</th>" for c in self.TRIAL_COLUMNS)
        body = "".join("<tr>" + "".join(f"<td>{html.escape(str(t[c]))}</td>" for c in self.TRIAL_COLUMNS) + "</tr>" for t in trials)
        trial_section = (f'<h2>Trials</h2><p>Also in <a href="trials.csv">trials.csv</a>. Frames within a trial are consecutive looks at one scene, '
                         f'not independent trials.</p><table><tr>{head}</tr>{body}</table>') if trials else ""
        output_cards = []
        for (boot, test), row in self.output_tests.items():
            outcome = row["end"]["event"] if row["end"] else "terminal event not received"
            output_cards.append(f'<li>Boot {boot}, output test {test}: {outcome}</li>')
        output_section = '<h2>Independent output tests</h2><p>These commands exercise the light/buzzer without a recognition verdict. Completion is the reported controller state; record physical observations separately.</p><ul>' + ''.join(output_cards) + '</ul>' if output_cards else ''
        injection_cards = []
        for (boot, number), row in self.injections.items():
            end = row["end"]
            outcome = end["event"] if end else "terminal event not received"
            if end and end.get("ended_us") is not None:
                outcome += f" after {(end['ended_us'] - end['alarm_start_us']) / 1e6:.3f} s"
            injection_cards.append(f'<li>Boot {boot}, injected detection {number}: {html.escape(outcome)}</li>')
        injection_section = '<h2>Injected detections</h2><p>INJECT DEER puts a confirmed deer detection into the armed controller, which starts the light/buzzer through the decision path with no picture taken. Completion is the reported controller state; record physical observations separately.</p><ul>' + ''.join(injection_cards) + '</ul>' if injection_cards else ''
        timeline = []
        for entry in self.timeline:
            if entry[0] == "frame":
                timeline.append(self.frame_card(self.frames[entry[1]]))
            else:
                timeline.append(f'<p class="marker">Marker: {html.escape(entry[1])}</p>')
        context = "".join(f"<p><b>{html.escape(k.capitalize())}:</b> {html.escape(str(v))}</p>" for k, v in self.context.items() if v)
        problems = ""
        if self.errors or self.warnings:
            items = "".join(f"<li>{html.escape(m)}</li>" for m in self.errors + self.warnings)
            problems = f'<h2>Recording errors and warnings</h2><ul>{items}</ul>'
        style = ('body{font:16px/1.5 system-ui;max-width:1100px;margin:24px auto;padding:0 20px;color:#18252c}'
                 'article{border-top:1px solid #ccc;padding:20px 0}.figures{display:flex;flex-wrap:wrap;gap:20px}'
                 'figure{margin:0;max-width:480px}img{max-width:100%;display:block}h2{font-size:19px}'
                 '.image{position:relative;line-height:0}.box{position:absolute;border:2px solid #e8590c;box-sizing:border-box}'
                 '.box span{position:absolute;left:0;top:0;background:#e8590c;color:#fff;font:12px/1.3 system-ui;padding:0 3px}'
                 'table{border-collapse:collapse;font-size:14px}td,th{border:1px solid #ccc;padding:2px 6px;text-align:left}'
                 '.marker{background:#f1f3f5;padding:6px 10px}.problem{color:#b00020}')
        page = (f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
                f'<title>Recognition trials</title><style>{style}</style><h1>Recorded recognition trials</h1>{context}'
                '<p>Each frame card pairs the camera JPEG the model scored with its device record and, when it was sent, the exact signed INT8 model input. '
                'The input preview dequantizes the retained tensor for display; the .input.i8 file is authoritative. Box outlines are drawn by this page over the unchanged JPEG. '
                'Transfer time is separate from inference time. Alarm commands do not measure actual sound/light.</p>'
                '<p><a href="summary.json">Recording summary</a> · <a href="events.jsonl">Events and markers</a> · <a href="run_manifest.json">Manifest</a></p>')
        (self.root / "index.html").write_text(page + problems + trial_section + output_section + injection_section + "".join(timeline) + "</html>\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--port", help="USB serial port, such as COM5 or /dev/ttyACM0")
    source.add_argument("--replay", type=Path, help="Reconstruct a recording from an existing serial.raw")
    parser.add_argument("--output", type=Path, required=True, help="New session directory; must not already exist")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--label", default="", help="short name of the session, such as the print and distance")
    parser.add_argument("--notes", default="", help="setup notes kept in the manifest and gallery")
    parser.add_argument("--keep-state", action="store_true",
                        help="record the device's state at the start without sending STOP")
    parser.add_argument("--drain-seconds", type=float, default=30.0,
                        help="longest wait after QUIT for an outstanding frame transfer to end")
    parser.add_argument("--quiet-seconds", type=float, default=2.0,
                        help="quiet time after QUIT, with no transfer outstanding, before closing")
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("output directory already exists; choose a new trial directory")
    serial = None
    if not args.replay:
        try:
            import serial
        except ImportError:
            parser.error("pyserial is required for a live recording: python -m pip install -r tools/requirements-classifier.txt")
    args.output.mkdir(parents=True)
    def report(row):
        if "result" in row:
            r=row["result"]
            print(f"Trial {r['trial']} frame {r['within']}: score={r['score']} confirmed={r['present']} committed={r['committed']} alarm={r['alarm_started']}")
        elif "device_message" in row:
            print(row["device_message"])
        elif "output_test" in row:
            print("Independent output test:", row["output_test"])
        elif "injection" in row:
            print("Injected detection:", row["injection"])
        elif "recorder_error" in row:
            print("RECORDING ERROR:",row["recorder_error"])
        elif "recorder_warning" in row:
            print("Recording warning:",row["recorder_warning"])
    receiver = Receiver(args.output, on_event=None if args.replay else report, context=dict(label=args.label, notes=args.notes))
    manifest = {"schema": 2, "started_utc": utc(), "source": str(args.replay) if args.replay else args.port,
                "recorder_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "state": "open",
                "label": args.label, "notes": args.notes}
    manifest_path = args.output / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    close = "normal"
    try:
        if args.replay:
            with args.replay.open("rb") as stream:
                while chunk := stream.read(4096):
                    receiver.feed(chunk)
        else:
            device = serial.Serial(port=None, baudrate=args.baud, timeout=0.1)
            device.dtr = False; device.rts = False; device.port = args.port
            device.open()
            def send(command):
                # The event row follows a completed write, so no command is logged unsent.
                device.write((command + "\n").encode("ascii"))
                receiver.emit(host_command=command)
            def read_once():
                chunk = device.read(4096)
                if chunk:
                    receiver.feed(chunk)
                return chunk
            def wait_for_reply(command, seconds):
                seen = len(receiver.status_replies)
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    read_once()
                    for reply in receiver.status_replies[seen:]:
                        if reply.get("command") == command:
                            return reply
                return None
            pending = queue.Queue()
            def console():
                try:
                    while True:
                        pending.put(input())
                except EOFError:
                    pending.put("QUIT")
            print("Commands: CAPTURE 1..600, MODE MANUAL, MODE PIR, LIGHT AMBIENT, LIGHT WHITE, RECORD FULL, RECORD JPEG, TEST OUTPUT, INJECT DEER, ARM, DISARM, STOP, STATUS")
            print("MARK description writes a local marker; QUIT sends STOP and closes recording.")
            try:
                # Opening the port can reset the board; the reply then follows READY.
                send("STATUS")
                manifest["initial_status"] = wait_for_reply("STATUS", 30)
                if manifest["initial_status"] is None:
                    receiver.error("no reply to the opening STATUS within 30 s")
                elif not args.keep_state:
                    send("STOP")
                    manifest["start_status"] = wait_for_reply("STOP", 10)
                threading.Thread(target=console, daemon=True).start()
                while True:
                    read_once()
                    try:
                        command = pending.get_nowait().strip()
                    except queue.Empty:
                        continue
                    if command == "QUIT":
                        break
                    if command.startswith("MARK "):
                        receiver.mark(command[5:]);continue
                    if not valid_command(command):
                        print("Invalid command; nothing sent.");continue
                    send(command)
                    print("Sent:", command)
            except KeyboardInterrupt:
                close = "interrupted"
            except (serial.SerialException, OSError) as exc:
                close = "serial_lost"
                receiver.error(f"serial connection lost: {exc}")
            if close != "serial_lost":
                try:
                    send("STOP")
                    # Wait for an outstanding frame to finish, then for the quiet time.
                    deadline = time.monotonic() + args.drain_seconds
                    quiet_since = time.monotonic()
                    while time.monotonic() < deadline:
                        if read_once():
                            quiet_since = time.monotonic()
                        elif not receiver.transfer_open() and time.monotonic() - quiet_since >= args.quiet_seconds:
                            break
                except (serial.SerialException, OSError) as exc:
                    close = "serial_lost"
                    receiver.error(f"serial connection lost while stopping: {exc}")
            try:
                device.close()
            except (serial.SerialException, OSError):
                pass
    finally:
        receiver.finish()
        # Close the manifest with the firmware seen and a hash of every saved file.
        artifacts = {path.name: {"bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                     for path in sorted(args.output.iterdir()) if path.is_file() and path.name != "run_manifest.json"}
        models = sorted({row["result"]["model_sha256"] for row in receiver.frames.values()})
        manifest.update(state="closed", close=close, ended_utc=utc(), firmware=receiver.boots,
                        model_sha256=models, artifacts=artifacts)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(args.output / "index.html")
    return 1 if receiver.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
