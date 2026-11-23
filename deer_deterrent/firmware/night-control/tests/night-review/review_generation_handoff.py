"""Exercise the exact app generation-guard expression with the real core/gate.

This is a deliberately narrow snapshot-to-ticket handoff test; it is not a
simulation of camera or ESP32 task operation.
"""
from pathlib import Path
import argparse
import hashlib
import json
import re
import subprocess

HERE=Path(__file__).resolve().parent
SRC=HERE.parents[1]/'src'
# The growth gate is the MVP program's header, shared by include path.
GATE=HERE.parents[2]/'motion-size'/'src'
parser=argparse.ArgumentParser()
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
OUTPUT=args.output.resolve()
OUTPUT.mkdir(parents=True,exist_ok=True)
source=(SRC/'app_main.cpp').read_text()
match=re.search(r'if\((!ticket\.generation[^\n]*)\) continue;',source)
assert match, 'App must retain a generation consistency guard before camera work'
guard=match.group(1)

code=r'''
#include "control_policy.hpp"
#include "approach_gate.hpp"
#include <cstdio>
#include <cstdlib>
using namespace night_control;
unsigned checks=0;
void check(bool x,const char* text){++checks;if(!x){std::fprintf(stderr,"FAIL %s\n",text);std::exit(1);}}
#define CHECK(x) check((x),#x)
int main(){
 constexpr int64_t now=1000000,night=1793750400LL*1000000;
 for(int change=0;change<4;++change){
  Core core;core.configure({true,41.5,-81.69,0,0},false,now);
  CHECK(core.synchronize(night,now));core.calibrationComplete(now);
  approach::Gate history;for(int area:{400,450,500,550})CHECK(!history.update({area,10,10,40,40}));
  const auto policy=core.tick(now);const auto seenGeneration=policy.generation;
  // The asynchronous change lands after the main-loop snapshot, before ticket.
  if(change==0){core.setPaused(true,now);core.setPaused(false,now);}
  else if(change==1)core.cancel(now);
  else if(change==2)CHECK(core.synchronize(night+3000000,now));
  else {core.invalidateClock(now);CHECK(core.synchronize(night,now));}
  const auto ticket=core.ticket(Capture::Monitoring,now);
  CHECK(ticket.generation!=policy.generation&&ticket.generation!=0);
  CHECK(history.update({650,10,10,40,40})); // stale four-frame evidence would pass
  const bool skip=GUARD;
  CHECK(skip); // no capture/decision may adopt the newer ticket with old history
  CHECK(!core.tick(now).alarmActive);
 }
 // A fresh explicit calibration must also reject the old partial-reference run.
 Core core;core.configure({true,41.5,-81.69,0,0},false,now);
 CHECK(core.synchronize(night,now));
 const auto policy=core.tick(now);const auto seenGeneration=policy.generation;
 CHECK(core.requestCalibration(now));const auto ticket=core.ticket(Capture::Calibration,now);
 CHECK(ticket.generation!=policy.generation&&ticket.generation!=0);
 CHECK(GUARD);
 std::printf("PASS app generation handoff checks=%u\n",checks);
}
'''.replace('GUARD','('+guard+')')
generated=OUTPUT/'generation_handoff_generated.cpp'
generated.write_text(code)
binary=OUTPUT/'generation_handoff_test'
subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined',
                '-fno-sanitize-recover=all','-I',str(SRC),'-I',str(GATE),str(generated),
                str(SRC/'control_policy.cpp'),str(SRC/'solar.cpp'),'-o',str(binary)],check=True)
result=subprocess.check_output([str(binary)],text=True)
(OUTPUT/'generation-handoff-results.json').write_text(json.dumps({
    'app_source_sha256':hashlib.sha256(source.encode()).hexdigest(),
    'guard_under_test':guard,'result':result.strip(),
    'scope':'Exact app guard expression; real core/growth gate; snapshot-to-ticket handoff only'},indent=2)+'\n')
print(result,end='')
