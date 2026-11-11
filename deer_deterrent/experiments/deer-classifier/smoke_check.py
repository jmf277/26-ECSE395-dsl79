"""Recheck retained score-only blank/deer/blank JSONL recordings.

For live paired images and inference, use tools/record_classifier.py.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re

STAGES = ("baseline", "deer", "clear")
FRAME = re.compile(r"^FRAME n=(\d+) score=(\S+) present=([01])(?:\s|$)")


def parse_frame(line: str) -> dict | None:
    if not line.startswith("FRAME"):
        return None
    match = FRAME.match(line)
    if not match:
        raise ValueError(f"malformed firmware frame: {line}")
    score = float(match[2])
    if not math.isfinite(score) or not 0 <= score <= 1:
        raise ValueError(f"invalid detection score: {score}")
    return {"n": int(match[1]), "score": score, "present": int(match[3])}


def summarize(records: list[dict], frames: int = 10, settle: int = 3) -> dict:
    """Require complete stages, no device errors/reboots and expected gate states."""
    reasons: list[str] = []
    if type(frames) is not int or type(settle) is not int or frames < 3 or settle < 3:
        return {"pass": False, "stages": {}, "failures": ["require at least 3 measured and 3 settling frames"],
                "scope": "controlled printed-image smoke test only"}
    stage_frames: dict[str, list[dict]] = {s: [] for s in STAGES}
    stages_seen: list[str] = []
    previous_n = 0
    active = None
    ready = False
    history: list[bool | None] = []
    threshold = 0.85
    for row in records:
        if row.get("type") == "stage":
            active = row["stage"]
            stages_seen.append(active)
        if row.get("type") != "serial":
            continue
        line = row["line"]
        if line.startswith(("ERROR", "FATAL", "MODEL_SELFTEST FAIL", "GATE_SELFTEST FAIL", "INFERENCE_SELFTEST FAIL")):
            reasons.append(line)
        if ready and (line.startswith(("BOOT ", "ESP-ROM:", "rst:")) or "Guru Meditation" in line or "panic" in line.lower()):
            reasons.append("device reset or panicked during the test: " + line)
        if line.startswith("READY"):
            if ready:
                reasons.append("device restarted during the test")
            ready = True
            threshold_match = re.search(r"\bthreshold=(\S+)", line)
            if threshold_match:
                try:
                    threshold = float(threshold_match[1])
                except ValueError:
                    reasons.append("invalid threshold in READY line")
            if not math.isfinite(threshold) or not 0.10 <= threshold <= 1:
                reasons.append("gate threshold is below the reported score floor or invalid")
            history.clear()
        try:
            frame = parse_frame(line)
        except ValueError as error:
            reasons.append(str(error))
            continue
        if frame is not None:
            if not ready:
                continue
            if previous_n and frame["n"] != previous_n + 1:
                reasons.append("frame counter restarted, repeated or skipped")
            previous_n = frame["n"]
            # Scores have four decimals. Only check gate consistency when rounding
            # cannot change which side of the threshold the actual score was on.
            history.append(None if abs(frame["score"] - threshold) < 0.000051 else frame["score"] >= threshold)
            history = history[-3:]
            if None not in history and frame["present"] != int(sum(history) >= 2):
                reasons.append(f"frame {frame['n']}: gate state disagrees with reported scores")
            if active in stage_frames:
                stage_frames[active].append(frame)
    if stages_seen != list(STAGES):
        reasons.append("expected exactly baseline, deer, clear in that order")
    results = {}
    for stage in STAGES:
        rows = stage_frames[stage]
        measured = rows[settle:settle + frames]
        complete = len(rows) >= settle + frames
        positives = sum(row["present"] for row in measured)
        passed = complete and (positives >= 2 if stage == "deer" else positives == 0)
        if not passed:
            reasons.append(f"{stage}: incomplete capture or unexpected deer signal")
        results[stage] = {"pass": passed, "frames": len(measured),
                          "confirmed_frames": positives,
                          "max_score": max((r["score"] for r in measured), default=None)}
    if not any(r.get("type") == "serial" and r["line"].startswith("READY") for r in records):
        reasons.append("firmware READY line missing")
    return {"pass": not reasons, "stages": results, "failures": reasons,
            "scope": "controlled printed-image smoke test only"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", type=Path, required=True)
    args = parser.parse_args()
    records = [json.loads(line) for line in args.replay.read_text().splitlines() if line.strip()]
    settings = next((r for r in records if r.get("type") == "settings"), {})
    result = summarize(records, settings.get("frames", 10), settings.get("settle", 3))
    print(json.dumps(result, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
