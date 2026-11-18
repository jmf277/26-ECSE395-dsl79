"""Fetch ESP32 JPEGs, run a laptop deer checkpoint and return image-bound verdicts.

Uses the project's ESP-Detection compatibility loader. No checkpoint is bundled
or downloaded automatically. Each retained JPEG, prediction and response receipt
is saved. Run --help for the explicit model/source paths.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import io
import json
import logging
import math
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

MAX_JPEG = 1000000
PROTOCOL = 3
GATE_SOURCE = Path(__file__).resolve().parents[1] / "experiments" / "deer-classifier" / "temporal_gate.py"


def load_gate_class(path=GATE_SOURCE):
    """The team's Python two-of-three gate, loaded from the repository so both paths use one rule."""
    spec = importlib.util.spec_from_file_location("deer_temporal_gate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.TemporalDeerGate

def fnv1a(data):
    value = 2166136261
    for byte in data:
        value = ((value ^ byte) * 16777619) & 0xffffffff
    return f"{value:08x}"

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def strict_json(data):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("duplicate JSON key")
            out[key] = value
        return out
    def reject(value):
        raise ValueError("nonfinite JSON value")
    return json.loads(data, object_pairs_hook=pairs, parse_constant=reject)

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("device HTTP redirects are not accepted")

class Device:
    def __init__(self, base, timeout=15):
        parsed = urlsplit(base)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("device URL must be an HTTP(S) origin without credentials/path/query")
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.opener = build_opener(NoRedirect)

    def request(self, path, fields=None, image=False):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("device path must be local")
        data = None if fields is None else urlencode(fields).encode("ascii")
        request = Request(self.base + path, data=data,
                          headers={} if fields is None else {"Content-Type": "application/x-www-form-urlencoded"})
        limit = MAX_JPEG if image else 65536
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                body = response.read(limit + 1)
        except HTTPError as refused:
            refused.close()  # release the device's reply; callers use only its status and message
            raise
        if len(body) > limit:
            raise ValueError("device response exceeds limit")
        return body if image else strict_json(body)

    def status(self):
        result = self.request("/status")
        if result.get("protocol") != PROTOCOL:
            raise ValueError(f"device does not implement image-bound alarm protocol {PROTOCOL}")
        return result

class NmsWarnings(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []
    def emit(self, record):
        if "nms time limit" in record.getMessage().lower():
            self.messages.append(record.getMessage())

class EspDetPredictor:
    def __init__(self, checkpoint, model_project, esp_detection, device="cpu", size=224):
        checkpoint, model_project, esp_detection = map(lambda p: Path(p).resolve(), (checkpoint, model_project, esp_detection))
        helper = model_project / "espdet_compat.py"
        if not checkpoint.is_file() or checkpoint.suffix != ".pt":
            raise ValueError("provide the existing trained float .pt checkpoint")
        if not helper.is_file() or not (esp_detection / "train.py").is_file():
            raise ValueError("model project/ESP-Detection checkout is incomplete")
        spec = importlib.util.spec_from_file_location("project_espdet_compat", helper)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.load_esp_detection(esp_detection)
        import torch
        import ultralytics
        from ultralytics import YOLO
        self.model = YOLO(str(checkpoint))
        names = self.model.names
        if len(names) != 1 or str(names[0]).lower() != "deer":
            raise ValueError(f"expected one-class deer checkpoint, got {names}")
        self.device, self.size = device, size
        self.identity = dict(checkpoint=str(checkpoint), checkpoint_sha256=digest(checkpoint),
                             compatibility_sha256=digest(helper), torch=torch.__version__,
                             ultralytics=ultralytics.__version__, input_size=size, device=device,
                             prediction_floor=.001, nms_iou=.7, rect=False, max_detections=300,
                             score_contract="maximum class-0 deer box confidence; zero if no box")

    def predict(self, jpeg):
        from PIL import Image
        from ultralytics.utils import LOGGER
        with Image.open(io.BytesIO(jpeg)) as decoded:
            image = decoded.convert("RGB")
        watcher = NmsWarnings()
        LOGGER.addHandler(watcher)
        try:
            rows = self.model.predict(source=image, imgsz=self.size, conf=.001, iou=.7,
                                      max_det=300, rect=False, device=self.device, verbose=False)
        finally:
            LOGGER.removeHandler(watcher)
        if watcher.messages:
            raise RuntimeError("NMS truncated the result; no verdict sent")
        if len(rows) != 1 or rows[0].boxes is None:
            raise RuntimeError("model did not return exactly one box-result object")
        boxes = rows[0].boxes
        result = []
        for xyxy, score, category in zip(boxes.xyxy.cpu().tolist(), boxes.conf.cpu().tolist(), boxes.cls.cpu().tolist(), strict=True):
            if category != 0 or not math.isfinite(score) or not 0 <= score <= 1 or len(xyxy) != 4 or any(not math.isfinite(x) for x in xyxy):
                raise ValueError("invalid model box/score/class")
            result.append(dict(box=xyxy, score=score, category=0))
        return dict(score=max((b["score"] for b in result), default=0.0), boxes=result)

def validate_manifest(manifest):
    if manifest.get("protocol") != PROTOCOL or not isinstance(manifest.get("boot"), str) or not re.fullmatch(r"[0-9a-f]{16}", manifest["boot"]):
        raise ValueError("invalid manifest protocol/boot")
    if type(manifest.get("burst")) is not int or not 0 <= manifest["burst"] <= 0xffffffff:
        raise ValueError("invalid burst")
    if type(manifest.get("accepting_results")) is not bool:
        raise ValueError("manifest lacks reply-window state")
    images = manifest.get("images")
    if not isinstance(images, list) or len(images) > 10:
        raise ValueError("invalid image manifest")
    seen = set()
    for image in images:
        if (type(image.get("id")) is not int or not 1 <= image["id"] <= 0xffffffff or image["id"] in seen
                or type(image.get("bytes")) is not int or not 4 <= image["bytes"] <= MAX_JPEG
                or not isinstance(image.get("fnv1a"), str) or not re.fullmatch(r"[0-9a-f]{8}", image["fnv1a"])):
            raise ValueError("invalid or duplicate image identity/size/checksum")
        seen.add(image["id"])
    if images and manifest["burst"] == 0:
        raise ValueError("retained images require a burst identity")

class BurstGates:
    """One two-of-three gate per burst, mirroring the board's, so each record carries the decision."""
    def __init__(self, threshold, gate_class=None):
        self.threshold = threshold
        self.gate_class = gate_class or load_gate_class()
        self.gates = {}

    def for_burst(self, boot, burst):
        return self.gates.setdefault((boot, burst), self.gate_class(window=3, required=2, threshold=self.threshold))


def process_manifest(device, predictor, manifest, root, seen, threshold, gates=None):
    """One synchronous inference per original JPEG, in capture order.

    A photo that fails (download, check, inference or reply) is recorded with its
    error and passed over; it sends no verdict and clears the two-of-three
    history, as the board does for a photo left without a verdict.
    """
    from PIL import Image
    validate_manifest(manifest)
    if not manifest["accepting_results"]:
        return []
    gates = gates or BurstGates(threshold)
    gate = gates.for_burst(manifest["boot"], manifest["burst"])
    results = []
    for item in manifest["images"]:
        key = (manifest["boot"], manifest["burst"], item["id"])
        if key in seen:
            continue
        seen.add(key)
        stem = f"{key[0]}-burst-{key[1]:06d}-photo-{key[2]:06d}"
        photo_path, result_path = root/(stem+".jpg"), root/(stem+".json")
        fields = dict(boot=key[0], burst=key[1], id=key[2])
        record = dict(schema=2, **fields, threshold=threshold, rule="two of the last three photos", model=predictor.identity,
                      source="ESP32 camera JPEG; laptop float inference", alarm_receipt=None)
        try:
            # Construct the URL from validated identity, never a server-supplied destination.
            jpeg = device.request("/photo.jpg?"+urlencode(fields), image=True)
            if len(jpeg) != item["bytes"] or fnv1a(jpeg) != item["fnv1a"]:
                raise ValueError("downloaded image differs from the retained image manifest")
            with Image.open(io.BytesIO(jpeg)) as image:
                if image.format != "JPEG" or image.size != (320,240):
                    raise ValueError("expected the firmware's QVGA JPEG")
                image.load()
            with photo_path.open("xb") as saved:
                saved.write(jpeg)
            record.update(jpeg_file=photo_path.name, jpeg_sha256=hashlib.sha256(jpeg).hexdigest(), fnv1a=item["fnv1a"])
            started = time.perf_counter()
            prediction = predictor.predict(jpeg)
            score = prediction.get("score")
            if type(score) not in (int,float) or not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError("invalid model score; no verdict sent")
            record.update(prediction=prediction, inference_seconds=time.perf_counter()-started, deer=score >= threshold)
            verdict = dict(**fields, fnv1a=item["fnv1a"], deer="true" if record["deer"] else "false")
            receipt = device.request("/classification", verdict)
            if (receipt.get("accepted") is not True or type(receipt.get("duplicate")) is not bool
                    or type(receipt.get("present")) is not bool or type(receipt.get("alarm_started")) is not bool
                    or (receipt["alarm_started"] and (not record["deer"] or receipt["duplicate"] or not receipt["present"]))):
                raise ValueError("invalid classification acknowledgment")
            record["alarm_receipt"] = receipt
            record["confirmed"] = gate.update(score)
            if record["confirmed"] != receipt["present"]:
                record["error"] = "device and laptop disagree on the two-of-three decision"
        except (OSError, ValueError, RuntimeError) as exc:
            gate.update(None)
            record["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            with result_path.open("x", encoding="utf-8") as stream:
                json.dump(record,stream,indent=2,allow_nan=False)
                stream.write("\n")
        results.append(record)
    return results

def main(argv=None, predictor_factory=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--model-project", type=Path, required=True, help="deer_classifier directory containing espdet_compat.py")
    parser.add_argument("--esp-detection", type=Path, required=True, help="compatible Espressif ESP-Detection checkout")
    parser.add_argument("--url", default="http://192.168.4.1")
    parser.add_argument("--output", type=Path, required=True, help="new recording directory")
    parser.add_argument("--device", default="cpu", help="PyTorch execution device")
    parser.add_argument("--size", type=int, default=224)
    parser.add_argument("--threshold", type=float, default=.85)
    parser.add_argument("--seconds", type=float, default=180)
    parser.add_argument("--poll", type=float, default=.25, help="seconds between status polls")
    parser.add_argument("--arm", action="store_true", help="permit model-driven physical outputs")
    parser.add_argument("--capture", action="store_true", help="start one manual burst once PIR warm-up and any pause have finished")
    args = parser.parse_args(argv)
    if (not math.isfinite(args.threshold) or not .001 < args.threshold <= 1 or args.size <= 0 or args.size % 32
            or not math.isfinite(args.seconds) or args.seconds <= 0 or not math.isfinite(args.poll) or args.poll <= 0):
        parser.error("threshold must be in (.001,1], size a positive multiple of 32, seconds and poll positive")
    if args.output.exists():
        parser.error("output directory exists; choose a new session directory")
    predictor = (predictor_factory or EspDetPredictor)(args.checkpoint,args.model_project,args.esp_detection,args.device,args.size)
    device = Device(args.url)
    initial = device.status()
    args.output.mkdir(parents=True)
    (args.output/"run.json").write_text(json.dumps(dict(model=predictor.identity, threshold=args.threshold, rule="two of the last three photos",
        armed=args.arm, client_sha256=digest(__file__), gate_sha256=digest(GATE_SOURCE), initial_status=initial,
        result_scope="laptop inference and device acknowledgments; physical actuation requires observation"),indent=2)+"\n")
    seen, capture_pending, gates = set(), args.capture, BurstGates(args.threshold)
    events = (args.output/"events.jsonl").open("x",encoding="utf-8")
    def event(**row):
        events.write(json.dumps(dict(host_monotonic=time.monotonic(),**row),allow_nan=False)+"\n");events.flush()
    last_good = time.monotonic()
    try:
        event(stop=device.request("/stop",{}))
        if args.arm:
            event(arm=device.request("/arm",{}))
        deadline = time.monotonic()+args.seconds
        while time.monotonic()<deadline:
            try:
                state = device.status();event(status=state)
                if state["boot"] != initial["boot"]:
                    raise RuntimeError("device rebooted; restart the supervised trial")
                if (capture_pending and state.get("pir_ready") and not state.get("burst_active")
                        and not state.get("alarm_active") and not state.get("cooldown_seconds")):
                    try:
                        # /capture is a text acknowledgment, unlike the JSON control routes.
                        device.request("/capture",{},image=True);capture_pending=False;event(capture="requested")
                    except HTTPError as exc:
                        if exc.code != 409:
                            raise
                        event(capture_deferred=f"{exc.code}: device busy, cooling down or warming up")
                for row in process_manifest(device,predictor,device.request("/burst"),args.output,seen,args.threshold,gates):
                    details = dict(photo=row["id"],score=row.get("prediction",{}).get("score"),confirmed=row.get("confirmed"),
                                   receipt=row["alarm_receipt"])
                    if row.get("error"):
                        details["photo_error"] = row["error"]
                    event(**details)
                    score = row.get("prediction",{}).get("score")
                    print(f"Photo {row['id']}: score={score}, deer={row.get('deer')}, confirmed={row.get('confirmed')}, "
                          f"receipt={row['alarm_receipt']}" + (f", error={row['error']}" if row.get("error") else ""))
                last_good = time.monotonic()
            except (URLError, TimeoutError, ConnectionError) as exc:
                event(poll_error=f"{type(exc).__name__}: {exc}")
                if time.monotonic() - last_good >= 10:
                    raise RuntimeError("device unreachable for 10 s") from exc
            time.sleep(args.poll)
    except KeyboardInterrupt:
        event(interrupted=True)
    except Exception as exc:
        event(error=f"{type(exc).__name__}: {exc}");raise
    finally:
        try:
            event(stop=device.request("/stop",{}))
        except Exception as exc:
            event(stop_error=str(exc))
        events.close()

if __name__ == "__main__":
    main()
