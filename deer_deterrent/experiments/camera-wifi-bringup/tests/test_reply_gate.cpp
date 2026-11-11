#include "../include/reply_gate.hpp"
#include <cassert>
#include <cstdio>
using namespace laptop_trial;
int main() {
  ReplyGate gate;
  assert(!gate.armed&&!gate.active());
  assert(gate.begin(1)&&gate.retain(4,100));
  auto r=gate.submit(1,4,true,200);assert(r.reply==Reply::Accepted&&!r.startAlarm);
  assert(gate.arm()&&gate.submit(1,4,true,201).reply==Reply::Stale);
  assert(gate.begin(2)&&gate.retain(5,300)&&gate.retain(6,400));
  assert(gate.submit(1,5,true,401).reply==Reply::Stale);
  assert(gate.submit(2,999,true,401).reply==Reply::Unknown);
  assert(gate.submit(2,5,false,402).reply==Reply::Accepted);
  assert(gate.submit(2,5,true,403).reply==Reply::Conflict);
  r=gate.submit(2,6,true,404);assert(r.startAlarm&&gate.active());
  assert(gate.tick(404).tone&&gate.tick(404).white&&!gate.begin(3)&&!gate.arm());
  assert(gate.submit(2,6,true,405).reply==Reply::Duplicate);
  assert(!gate.tick(1904).tone&&gate.tick(2404).tone);
  assert(!gate.tick(5904).tone&&!gate.active());
  assert(!gate.submit(2,6,true,6000).startAlarm); // Retry cannot restart a finished event.
  assert(gate.begin(3)&&gate.retain(7,7000));
  assert(gate.submit(3,7,true,67001).reply==Reply::Expired);
  assert(gate.begin(4)&&gate.retain(8,70000));
  assert(gate.submit(4,8,true,70001).startAlarm);gate.cancel();
  assert(!gate.active()&&!gate.tick(70002).white&&gate.submit(4,8,true,70002).reply==Reply::Stale);
  assert(gate.arm()&&gate.begin(5)&&gate.retain(9,0xfffffff0u));
  assert(gate.submit(5,9,true,20).startAlarm); // millis() wraparound.
  assert(gate.tick(21).tone&&!gate.tick(5520).tone);
  gate.cancel();assert(gate.begin(6));
  for(unsigned i=1;i<=10;++i)assert(gate.retain(i,100));
  assert(!gate.retain(11,100)&&!gate.retain(1,100));
  std::puts("PASS: armed/disarmed decisions, image/burst identity, expiry, idempotency, conflicts, finite waveform, cancellation, wraparound and retention limits");
}
