"""Record paired ESP32 recognition images, exact INT8 inputs and decisions.

The computer sends trial commands and saves records; it does not run the model.
Run --help for live USB recording or reconstruction from retained serial.raw.
"""
from __future__ import annotations

import argparse
import base64
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


def valid_command(command):
    return bool(re.fullmatch(r"CAPTURE [1-9][0-9]{0,2}|MODE (MANUAL|PIR)|LIGHT (AMBIENT|WHITE)|TEST OUTPUT|ARM|DISARM|STOP|STATUS", command)) and not (
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


def validate_result(item):
    identity(item)
    if type(item.get("schema")) is not int or item["schema"] != 1:
        raise ValueError("unsupported record schema")
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
    boxes = item.get("boxes")
    if not isinstance(boxes, list) or len(boxes) > 10:
        raise ValueError("invalid box list")
    for box in boxes:
        coords = box.get("box")
        if (not isinstance(coords, list) or len(coords) != 4 or any(type(x) is not int for x in coords)
                or not 0 <= coords[0] <= coords[2] < item["camera_width"]
                or not 0 <= coords[1] <= coords[3] < item["camera_height"]
                or type(box.get("category")) is not int or box["category"] != 0
                or type(box.get("score")) not in (int, float) or not math.isfinite(box["score"]) or not 0 <= box["score"] <= 1):
            raise ValueError("invalid detection box")


class Receiver:
    """Strict streaming parser. Partial/invalid transfers never become complete trials."""
    def __init__(self, root, on_event=None):
        self.root = Path(root)
        self.raw = (self.root / "serial.raw").open("xb")
        self.events = (self.root / "events.jsonl").open("x", encoding="utf-8")
        self.buffer = bytearray()
        self.frames = {}
        self.output_tests = {}
        self.pending = None
        self.errors = []
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
        if text.startswith("TRIAL_DATA "):
            parts = text.split()
            if len(parts) != 5 or self.pending is None:
                raise ValueError("orphan or malformed data chunk")
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
        if text.startswith("TRIAL_BLOB "):
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
            if type(header.get("bytes")) is not int or not 0 < header["bytes"] == record["result"][kind + "_bytes"]:
                raise ValueError("blob size disagrees with inference record")
            if not isinstance(header.get("fnv1a"), str) or not re.fullmatch(r"[0-9a-f]{8}", header["fnv1a"]):
                raise ValueError("invalid blob checksum")
            self.pending = header, bytearray()
            return
        if text.startswith("TRIAL_FRAME "):
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
            (self.root / (stem + ".json")).write_text(json.dumps(item, indent=2, allow_nan=False) + "\n")
            self.emit(result=item)
            return
        if text.startswith("TRIAL_END "):
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
            expected = {kind for kind in ("jpeg", "input") if row["result"][kind + "_bytes"]}
            row["complete"] = ending["transfer_complete"] and set(row["artifacts"]) == expected
            row["ended"] = True
            row["ending"] = ending
            self.emit(frame_end=ending, received_complete=row["complete"])
            return
        if text.startswith("BOOT ") and self.pending is not None:
            self.error("device reboot interrupted blob")
            self.pending = None
        if text.startswith(("ERROR", "TRIAL_ERROR", "E (")) or "Guru Meditation" in text:
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
            if not row["complete"]:
                self.error("incomplete frame: " + row["stem"])
            (self.root / (row["stem"] + ".received.json")).write_text(json.dumps(row, indent=2, allow_nan=False) + "\n")
        for (boot, test), row in self.output_tests.items():
            if row["end"] is None:
                self.error(f"output test lacks terminal event: {boot}/{test}")
        summary = {
            "schema": 1, "frames": len(self.frames),
            "complete_valid_pairs": sum(row["complete"] and row["result"]["valid"] for row in self.frames.values()),
            "invalid_inferences": sum(not row["result"]["valid"] for row in self.frames.values()),
            "incomplete_frames": sum(not row["complete"] for row in self.frames.values()),
            "output_tests_started": len(self.output_tests),
            "output_tests_completed": sum(bool(row["end"] and row["end"]["event"] == "completed") for row in self.output_tests.values()),
            "output_tests_cancelled": sum(bool(row["end"] and row["end"]["event"] == "cancelled") for row in self.output_tests.values()),
            "output_tests_incomplete": sum(row["end"] is None for row in self.output_tests.values()),
            "errors": self.errors,
            "scope": "Image/input transfer and reported inference/commands; not physical-output verification or recognition accuracy."
        }
        (self.root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        self.gallery()
        self.raw.close();self.events.close();self.closed = True

    def gallery(self):
        cards = []
        for row in self.frames.values():
            result, stem = row["result"], row["stem"]
            images = []
            if "jpeg" in row["artifacts"]:
                images.append(f'<figure><img src="{stem}.jpg" alt="Original camera JPEG"><figcaption>Original camera JPEG</figcaption></figure>')
            if "input" in row["artifacts"]:
                images.append(f'<figure><img src="{stem}.input-preview.png" alt="Model input preview"><figcaption>Dequantized 224×224 input preview</figcaption></figure>')
            title = f"Trial {result['trial']}, frame {result['within']} — score {result['score']}, confirmed {result['present']}"
            lighting = html.escape(result.get("lighting", "not recorded"))
            cards.append(f'<article><h2>{html.escape(title)}</h2><p>Capture lighting: {lighting}; transfer complete: {row["complete"]}; committed: {result["committed"]}; recognition alarm commanded: {result["alarm_started"]}</p><div>{"".join(images)}</div><a href="{stem}.json">Result and detected boxes</a> · <a href="{stem}.received.json">Transfer verification</a></article>')
        output_cards = []
        for (boot, test), row in self.output_tests.items():
            outcome = row["end"]["event"] if row["end"] else "terminal event not received"
            output_cards.append(f'<li>Boot {boot}, output test {test}: {outcome}</li>')
        output_section = '<h2>Independent output tests</h2><p>These commands exercise the light/buzzer without a recognition verdict. Completion is the reported controller state; record physical observations separately.</p><ul>' + ''.join(output_cards) + '</ul>' if output_cards else ''
        page = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Recognition trials</title><style>body{font:16px/1.5 system-ui;max-width:1100px;margin:24px auto;padding:0 20px;color:#18252c}article{border-top:1px solid #ccc;padding:20px 0}article div{display:flex;flex-wrap:wrap;gap:20px}figure{margin:0;max-width:480px}img{max-width:100%;image-rendering:auto}h2{font-size:19px}</style><h1>Recorded recognition trials</h1><p>The original JPEG and exact signed INT8 input are paired with the device result. The input preview dequantizes the retained tensor for display; the .input.i8 file is authoritative. Transfer time is separate from inference time. Alarm commands do not measure actual sound/light, and correlated frames are not independent animal trials.</p><p><a href="summary.json">Recording summary</a> · <a href="events.jsonl">Events and markers</a></p>'
        (self.root / "index.html").write_text(page + output_section + "".join(cards) + "</html>\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--port", help="USB serial port, such as COM5 or /dev/ttyACM0")
    source.add_argument("--replay", type=Path, help="Reconstruct a recording from an existing serial.raw")
    parser.add_argument("--output", type=Path, required=True, help="New session directory; must not already exist")
    parser.add_argument("--baud", type=int, default=115200)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("output directory already exists; choose a new trial directory")
    args.output.mkdir(parents=True)
    def report(row):
        if "result" in row:
            r=row["result"]
            print(f"Trial {r['trial']} frame {r['within']}: score={r['score']} confirmed={r['present']} committed={r['committed']} alarm={r['alarm_started']}")
        elif "device_message" in row:
            print(row["device_message"])
        elif "output_test" in row:
            print("Independent output test:", row["output_test"])
        elif "recorder_error" in row:
            print("RECORDING ERROR:",row["recorder_error"])
    receiver = Receiver(args.output, on_event=None if args.replay else report)
    manifest = {"schema": 1, "started_utc": utc(), "source": str(args.replay) if args.replay else args.port,
                "recorder_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (args.output / "run_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    try:
        if args.replay:
            with args.replay.open("rb") as stream:
                while chunk := stream.read(4096):
                    receiver.feed(chunk)
        else:
            import serial
            device = serial.Serial(port=None, baudrate=args.baud, timeout=0.1)
            device.dtr = False; device.rts = False; device.port = args.port
            device.open()
            pending = queue.Queue()
            def console():
                try:
                    while True:
                        pending.put(input())
                except EOFError:
                    pending.put("QUIT")
            threading.Thread(target=console, daemon=True).start()
            print("Commands: CAPTURE 1..600, MODE MANUAL, MODE PIR, LIGHT AMBIENT, LIGHT WHITE, TEST OUTPUT, ARM, DISARM, STOP, STATUS")
            print("MARK description writes a local marker; QUIT sends STOP and closes recording. Boot is manual/disarmed.")
            device.write(b"STATUS\n")
            try:
                while True:
                    chunk = device.read(4096)
                    if chunk:
                        receiver.feed(chunk)
                    try:
                        command = pending.get_nowait().strip()
                    except queue.Empty:
                        continue
                    if command == "QUIT":
                        break
                    if command.startswith("MARK "):
                        receiver.emit(marker=command[5:]);continue
                    if not valid_command(command):
                        print("Invalid command; nothing sent.");continue
                    receiver.emit(host_command=command)
                    device.write((command + "\n").encode("ascii"))
                    print("Sent:", command)
            except KeyboardInterrupt:
                pass
            finally:
                receiver.emit(host_command="STOP")
                device.write(b"STOP\n")
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    chunk = device.read(4096)
                    if chunk:
                        receiver.feed(chunk)
                device.close()
    finally:
        receiver.finish()
    print(args.output / "index.html")
    return 1 if receiver.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
