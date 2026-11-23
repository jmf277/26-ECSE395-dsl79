#!/usr/bin/env python3
"""Compile and execute the production policy/config code with a fake clock.

Requires C/C++ compilers and an ESP-IDF checkout for its exact cJSON dependency.
No board, network, credentials, or installed Python packages are used.
"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--idf", type=Path, default=os.environ.get("IDF_PATH"))
    parser.add_argument("--sanitize", action="store_true", help="enable undefined-behavior checks")
    parser.add_argument("--address-sanitize", action="store_true", help="also enable address/leak checks (requires a supported host runtime)")
    args = parser.parse_args()
    if args.idf is None:
        parser.error("set IDF_PATH or pass --idf /path/to/esp-idf")
    project = Path(__file__).resolve().parents[1]
    cjson = Path(args.idf) / "components/json/cJSON"
    if not (cjson / "cJSON.c").is_file():
        parser.error("ESP-IDF cJSON source is unavailable")
    flags = ["-Wall", "-Wextra", "-Werror", "-O1", "-g"]
    if args.sanitize or args.address_sanitize:
        flags += ["-fsanitize=address,undefined" if args.address_sanitize else "-fsanitize=undefined", "-fno-omit-frame-pointer"]
    with tempfile.TemporaryDirectory(prefix="deer-control-tests-") as directory:
        build = Path(directory)
        subprocess.run([os.environ.get("CC", "cc"), *flags, "-I", str(cjson), "-c", str(cjson / "cJSON.c"), "-o", str(build / "cjson.o")], check=True)
        sources = [project / "src" / name for name in ("solar.cpp", "control_policy.cpp", "control_config.cpp")]
        subprocess.run([os.environ.get("CXX", "c++"), "-std=c++17", *flags, "-I", str(project / "src"), "-I", str(cjson), *map(str, sources), str(project / "tests/test_control.cpp"), str(build / "cjson.o"), "-o", str(build / "test_control")], check=True)
        subprocess.run([str(build / "test_control")], check=True)

if __name__ == "__main__":
    main()
