#!/usr/bin/env python3
"""Run the independent host review suite without installing dependencies."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

HERE=Path(__file__).resolve().parent
PROJECT=HERE.parents[1]
SRC=PROJECT/'src'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--idf',type=Path,default=os.environ.get('IDF_PATH'),
                        help='ESP-IDF checkout for the exact cJSON/service transaction checks')
    parser.add_argument('--output',type=Path,default=PROJECT/'build/independent-review')
    args=parser.parse_args()
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    records=[]

    def run(command):
        result=subprocess.run([str(x) for x in command],check=True,text=True,capture_output=True)
        if result.stdout:print(result.stdout,end='')
        if result.stderr:print(result.stderr,end='',file=sys.stderr)
        records.append({'command':[str(x) for x in command],'stdout':result.stdout.strip()})

    common=['g++','-std=c++17','-Wall','-Wextra','-Werror','-g',
            '-fsanitize=undefined','-fno-sanitize-recover=all','-I',SRC]
    run(common+[HERE/'review_core_test.cpp',SRC/'control_policy.cpp',SRC/'solar.cpp',
                '-o',output/'review_core_test'])
    run([output/'review_core_test'])
    run(common+[HERE/'solar_probe.cpp',SRC/'solar.cpp','-o',output/'solar_probe'])
    run([sys.executable,HERE/'review_solar.py','--probe',output/'solar_probe','--output',output])
    run([sys.executable,HERE/'review_generation_handoff.py','--output',output])
    if args.idf:
        run([sys.executable,HERE/'review_service_transactions.py','--idf',args.idf,'--output',output])
    else:
        print('SKIP service transaction tests: supply --idf or IDF_PATH for the ESP-IDF cJSON source.')
    sources={name:hashlib.sha256((SRC/name).read_bytes()).hexdigest() for name in [
        'solar.cpp','solar.hpp','control_policy.cpp','control_policy.hpp',
        'control_config.cpp','control_config.hpp','control_service.cpp','app_main.cpp']}
    report={'checks':'strict-warning host builds and UBSan; no ASan or board-operation claim',
            'service_transactions_run':bool(args.idf),'source_sha256':sources,'commands':records}
    (output/'independent-review-run.json').write_text(json.dumps(report,indent=2)+'\n')
    print('PASS independent review suite; results:',output)


if __name__=='__main__':main()
