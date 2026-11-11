#include "../include/reply_gate.hpp"
#include <cassert>
#include <cstdio>
using namespace laptop_trial;
int main() {
  ReplyGate gate;
  assert(!gate.armed&&!gate.active());
  // Disarmed: two deer verdicts confirm, but no alarm starts.
  assert(gate.begin(1)&&gate.retain(4,100)&&gate.retain(5,150));
  auto r=gate.submit(1,4,true,200);assert(r.reply==Reply::Accepted&&!r.present&&!r.startAlarm);
  r=gate.submit(1,5,true,210);assert(r.reply==Reply::Accepted&&r.present&&!r.startAlarm&&!gate.active());
  // Arming closes the reply window of the current burst.
  assert(gate.arm()&&gate.submit(1,4,true,211).reply==Reply::Stale);
  // Armed: one deer verdict is not enough; hit, miss, hit confirms on the third.
  assert(gate.begin(2)&&gate.retain(5,300)&&gate.retain(6,350)&&gate.retain(7,390));
  assert(gate.submit(1,5,true,401).reply==Reply::Stale);
  assert(gate.submit(2,999,true,401).reply==Reply::Unknown);
  r=gate.submit(2,5,true,402);assert(r.reply==Reply::Accepted&&!r.present&&!r.startAlarm);
  assert(gate.submit(2,5,false,403).reply==Reply::Conflict);
  r=gate.submit(2,6,false,404);assert(r.reply==Reply::Accepted&&!r.present&&!gate.active());
  r=gate.submit(2,7,true,405);assert(r.present&&r.startAlarm&&gate.active());
  assert(gate.tick(405).tone&&gate.tick(405).white&&!gate.begin(3)&&!gate.arm());
  assert(gate.submit(2,7,true,406).reply==Reply::Duplicate);
  assert(!gate.tick(1905).tone&&gate.tick(2405).tone);
  assert(!gate.tick(5905).tone&&!gate.active());
  assert(!gate.submit(2,7,true,6000).startAlarm); // Retry cannot restart a finished event.
  // Verdicts follow capture order: a skipped photo clears the history, and a
  // photo passed over cannot be judged later.
  assert(gate.begin(3)&&gate.retain(8,7000)&&gate.retain(9,7100)&&gate.retain(10,7200)&&gate.retain(11,7300));
  r=gate.submit(3,8,true,7400);assert(!r.present);
  r=gate.submit(3,10,true,7500);assert(r.reply==Reply::Accepted&&!r.present&&!gate.active()); // 9 skipped
  assert(gate.submit(3,9,true,7600).reply==Reply::Stale);
  r=gate.submit(3,11,true,7700);assert(r.present&&r.startAlarm);gate.cancel();
  // A reply more than 60 s after its photo is refused.
  assert(gate.arm()&&gate.begin(4)&&gate.retain(12,8000));
  assert(gate.submit(4,12,true,68001).reply==Reply::Expired);
  // Cancellation ends the alarm and the reply window.
  assert(gate.begin(5)&&gate.retain(13,70000)&&gate.retain(14,70001));
  gate.submit(5,13,true,70002);assert(gate.submit(5,14,true,70003).startAlarm);gate.cancel();
  assert(!gate.active()&&!gate.tick(70004).white&&gate.submit(5,14,true,70004).reply==Reply::Stale);
  // millis() wraparound.
  assert(gate.arm()&&gate.begin(6)&&gate.retain(15,0xfffffff0u)&&gate.retain(16,0xfffffff8u));
  gate.submit(6,15,true,10);assert(gate.submit(6,16,true,20).startAlarm);
  assert(gate.tick(21).tone&&!gate.tick(5520).tone);
  // A new burst starts with an empty history.
  gate.cancel();assert(gate.arm()&&gate.begin(7)&&gate.retain(17,100)&&gate.retain(18,100));
  assert(!gate.submit(7,17,true,101).present);
  gate.cancel();assert(gate.begin(8));
  for(unsigned i=1;i<=10;++i)assert(gate.retain(i,100));
  assert(!gate.retain(11,100)&&!gate.retain(1,100));
  std::puts("PASS: two of the last three verdicts in capture order, skipped photos, armed/disarmed decisions, image/burst identity, expiry, idempotency, conflicts, finite waveform, cancellation, wraparound and retention limits");
}
