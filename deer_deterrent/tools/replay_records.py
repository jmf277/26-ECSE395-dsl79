"""Verify recordings and replay compatible, observed windows through C++.

Exit 0: analysis completed without integrity errors or replay mismatches.
Exit 1: integrity/application errors or replay mismatches were found.
Exit 2: invalid command, missing compiler, or a native-adapter failure.
Warnings and partial windows remain explicit; exit 0 is not an accuracy result.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

from recording import analyze_session, sha256_file, strict_json

TOOLS = Path(__file__).resolve().parent
REPOSITORY = TOOLS.parent
GATE_HEADER = REPOSITORY / "firmware/motion-size/src/approach_gate.hpp"


class NativeGate:
    def __init__(self, directory, compiler=None):
        requested = compiler or os.environ.get("CXX") or "c++"
        self.compiler = shutil.which(requested)
        if not self.compiler:
            raise RuntimeError(f"C++17 compiler not found: {requested}. Set --compiler to its executable path.")
        self.binary = Path(directory) / ("gate_replay.exe" if os.name == "nt" else "gate_replay")
        self.source_hash = sha256_file(TOOLS / "gate_replay.cpp")
        self.gate_hash = sha256_file(GATE_HEADER)
        command = [self.compiler, "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                   str(TOOLS / "gate_replay.cpp"), "-o", str(self.binary)]
        try:
            built = subprocess.run(command, capture_output=True, text=True, timeout=60)
            if built.returncode:
                raise RuntimeError("Native gate build failed:\n" + built.stderr)
            if self.gate_hash != sha256_file(GATE_HEADER) or self.source_hash != sha256_file(TOOLS / "gate_replay.cpp"):
                raise RuntimeError("Replay sources changed during compilation; repeat the command on a stable source tree.")
            description = subprocess.run([str(self.binary), "--describe"], capture_output=True, text=True, check=True, timeout=10)
            self.contract = strict_json(description.stdout)
            version = subprocess.run([self.compiler, "--version"], capture_output=True, text=True, timeout=10)
            self.compiler_version = (version.stdout or version.stderr).splitlines()[0]
        except (OSError, subprocess.SubprocessError, ValueError, IndexError) as exc:
            raise RuntimeError(f"Native gate setup failed: {exc}") from exc

    def run(self, commands):
        try:
            result = subprocess.run([str(self.binary)], input="\n".join(commands) + "\n",
                                    capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"Native gate replay failed: {exc}") from exc
        if result.returncode:
            raise RuntimeError(f"Native gate rejected replay input (exit {result.returncode}): {result.stderr}")
        try:
            return [strict_json(line) for line in result.stdout.splitlines()]
        except ValueError as exc:
            raise RuntimeError("Native gate produced invalid output") from exc

    def provenance(self):
        return {"engine": "shared firmware C++ approach::Gate", "gate_header": "firmware/motion-size/src/approach_gate.hpp",
                "gate_sha256": self.gate_hash, "adapter_sha256": self.source_hash,
                "compiler": self.compiler, "compiler_version": self.compiler_version,
                "recording_contract": self.contract,
                "scope": "Region-metadata replay; does not reconstruct the unsaved baseline or rerun camera image processing."}


def replay_sessions(sessions, native):
    commands, expected, skipped = [], [], []
    window_counts = Counter()
    for session in sessions:
        for window in session["windows"]:
            identity = {"session": session["session"], "window": window["number"]}
            reason = None
            if not window["start_observed"]:
                reason = "missing trigger; initial gate history is unknown"
            elif window["issues"]:
                reason = "incomplete or inconsistent sequence: " + ", ".join(sorted(set(window["issues"])))
            elif not window["frames"]:
                reason = "no retained detector measurements"
            elif any("growth_frames" not in frame["metadata"] for frame in window["frames"]):
                reason = "legacy size-only or unknown gate revision; not replayed with the current growth gate"
            elif any(any(frame["metadata"].get(k) != v for k, v in native.contract.items()) for frame in window["frames"]):
                reason = "recorded configuration does not match the shared gate's recording contract"
            if reason:
                window["replay"] = {"status": "skipped", "reason": reason}
                skipped.append({**identity, "reason": reason})
                continue
            # Reset for each explicitly observed PIR window, including when its
            # preceding ARM/calibration/abort event is outside the recording.
            commands.append("RESET")
            window["replay"] = {"status": "pending", "checked_frames": len(window["frames"]), "mismatches": []}
            window_counts[window["completeness"]] += 1
            for frame in window["frames"]:
                meta = frame["metadata"]
                number = len(expected)
                commands.append(" ".join(map(str, ["FRAME", number, meta["changed_pixels"], *meta["bbox"]])))
                expected.append((session, window, frame))
    received = native.run(commands) if expected else []
    if len(received) != len(expected):
        raise RuntimeError("Native output count differs from the input frame count")
    mismatches = []
    for number, (actual, (session, window, frame)) in enumerate(zip(received, expected)):
        if actual.get("id") != number:
            raise RuntimeError("Native output sequence differs from its input")
        meta = frame["metadata"]
        differences = {key: {"recorded": meta[key], "replayed": actual.get(key)}
                       for key in ("state", "growth_frames", "growth_pixels", "growth_percent")
                       if meta[key] != actual.get(key)}
        if differences:
            mismatch = {"session": session["session"], "window": window["number"], "event_line": frame["line"],
                        "frame": meta["frame"], "differences": differences}
            mismatches.append(mismatch)
            window["replay"]["mismatches"].append(mismatch)
    for session in sessions:
        for window in session["windows"]:
            if window.get("replay", {}).get("status") != "pending":
                continue
            # New records distinguish gate qualification from the alarm-start
            # result: cancellation can deny an otherwise qualifying frame.
            # Legacy records lack that field and use their terminal gate state.
            # Neither representation measures physical sound or light output.
            if window["completeness"] == "complete":
                terminal = window["frames"][-1]["metadata"]
                basis = "deterrent_started" if "deterrent_started" in terminal else "legacy_terminal_gate_state"
                terminal_confirmed = (terminal["deterrent_started"] if "deterrent_started" in terminal
                                      else terminal["state"] == "LARGE_CHANGE")
                if bool(window["firmware_confirmed"]) != terminal_confirmed:
                    mismatch = {"session": session["session"], "window": window["number"], "event_line": window["end_line"],
                                "differences": {"burst_confirmation": {"recorded": window["firmware_confirmed"],
                                                                       "terminal_frame": terminal_confirmed, "basis": basis}}}
                    mismatches.append(mismatch)
                    window["replay"]["mismatches"].append(mismatch)
            window["replay"]["status"] = ("mismatch" if window["replay"]["mismatches"] else
                                           "matched_complete_window" if window["completeness"] == "complete" else "matched_observed_prefix")
    return {**native.provenance(), "checked_frames": len(expected), "checked_windows": sum(window_counts.values()),
            "checked_window_completeness": dict(window_counts), "mismatches": mismatches, "skipped_windows": skipped,
            "unbounded_detector_records_not_replayed": sum(s["counts"].get("detector_records_without_window", 0) for s in sessions)}


def discover_sessions(paths):
    found = set()
    for supplied in paths:
        path = Path(supplied).resolve()
        if path.is_file():
            if path.name != "events.jsonl":
                raise ValueError(f"Expected a session folder or events.jsonl: {path}")
            found.add(path.parent)
        elif path.is_dir():
            if (path / "events.jsonl").is_file():
                found.add(path)
            else:
                found.update(event.parent for event in path.rglob("events.jsonl"))
        else:
            raise ValueError(f"Input does not exist: {path}")
    if not found:
        raise ValueError("No events.jsonl recordings found")
    return sorted(found)


def report_path(path):
    """A session folder as reported: relative to the working directory when it lies
    inside it, so reports made from the `deer_deterrent` folder carry no machine-specific paths."""
    try:
        return Path(path).relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(path)


def render_markdown(report):
    replay = report["gate_replay"]
    totals = report["totals"]
    lines = ["# Detector recording analysis", "", "Analysis of retained records; no new hardware experiment was performed.", "",
             f"{totals['sessions']} sessions; {totals['verified_photos']} verified saved photos; "
             f"{replay['checked_frames']} region records replayed through the shared C++ gate in {replay['checked_windows']} windows; "
             f"{len(replay['mismatches'])} replay mismatches.", "",
             "Frames are correlated observations, not independent accuracy trials. Alarm start/stop messages are software events and do not establish physical sound or light output. Legacy size-only windows and records with unknown initial history are not evaluated against the current gate.", "",
             "| Session | Detector records | Calibration / diagnostic | Verified photos | Window status | Alarm start / stop messages | SDK E diagnostics |", "|---|---:|---:|---:|---|---:|---:|"]
    for s in report["sessions"]:
        c = s["counts"]
        windows = Counter(w["completeness"] for w in s["windows"])
        window_text = ", ".join(f"{count} {state}" for state, count in sorted(windows.items())) or "no observed PIR window"
        lines.append(f"| {s['session']} | {c.get('detector_frame_records', 0)} | {c.get('calibration_frame_records', 0)} / {c.get('diagnostic_frame_records', 0)} | {c['verified_photos']} | {window_text} | {c['alarm_start_messages']} / {c['alarm_stop_messages']} | {c['sdk_error_diagnostics']} |")
    lines += ["", "## Scope and integrity", "", replay["scope"], "",
              f"Gate SHA-256: `{replay['gate_sha256']}`. Adapter SHA-256: `{replay['adapter_sha256']}`.", "",
              "Local source hashes identify this replay engine. They do not establish which binary was flashed for a historical session.", ""]
    for s in report["sessions"]:
        if not s["errors"] and not s["warnings"] and not s["application_errors"]:
            continue
        lines += [f"### {s['session']}", ""]
        for level in ("errors", "warnings"):
            for item in s[level]:
                location = f"events.jsonl:{item['line']}" if item["line"] is not None else "saved files"
                detail = item["detail"].replace("\n", " ")
                lines.append(f"- {level[:-1].capitalize()} — {location}: {item['code']}: {detail}")
        for item in s["application_errors"]:
            lines.append(f"- Device application error — events.jsonl:{item['line']}: `{item['message']}`")
        lines.append("")
    if replay["mismatches"]:
        lines += ["## Replay mismatches", "", "```json", json.dumps(replay["mismatches"], indent=2), "```", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", help="Session directories, events.jsonl files or a parent containing sessions")
    parser.add_argument("--output", type=Path, help="Write the complete JSON report outside the input session folders")
    parser.add_argument("--markdown", type=Path, help="Write a readable report outside the input session folders")
    parser.add_argument("--compiler", help="C++17 compiler executable; defaults to CXX or c++ (GCC/Clang flags)")
    args = parser.parse_args(argv)
    try:
        roots = discover_sessions(args.paths)
        for destination in (args.output, args.markdown):
            if destination and any(destination.resolve().is_relative_to(root) for root in roots):
                raise ValueError("Reports must be written outside input session directories")
        if args.output and args.markdown and args.output.resolve() == args.markdown.resolve():
            raise ValueError("JSON and Markdown outputs must have different paths")
        sessions = [analyze_session(root) for root in roots]
        for session in sessions:
            session["source_directory"] = report_path(session["source_directory"])
        with tempfile.TemporaryDirectory(prefix="deer-gate-replay-") as temporary:
            native = NativeGate(temporary, args.compiler)
            replay = replay_sessions(sessions, native)
        totals = Counter({"sessions": len(sessions)})
        for session in sessions:
            totals.update(session["counts"])
        report = {"schema_version": 2, "totals": dict(totals), "gate_replay": replay, "sessions": sessions}
        for path, content in ((args.output, json.dumps(report, indent=2, allow_nan=False) + "\n"),
                              (args.markdown, render_markdown(report))):
            if path:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
        print(f"{len(sessions)} sessions; {totals['verified_photos']} photos verified; {replay['checked_frames']} frames replayed; {len(replay['mismatches'])} mismatches.")
        print(f"SDK E diagnostics: {totals['sdk_error_diagnostics']}; application error messages: {totals['application_error_messages']}; "
              f"integrity errors: {sum(len(s['errors']) for s in sessions)}; warnings: {sum(len(s['warnings']) for s in sessions)}.")
        print("These are recording/replay checks, not independent accuracy trials or physical output measurements.")
        return 1 if replay["mismatches"] or any(s["errors"] or s["application_errors"] for s in sessions) else 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"replay_records: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
