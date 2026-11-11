#include "../src/trial_control.hpp"
#include "../src/trial_command.hpp"
#include <cassert>
#include <cstdio>
#include <limits>
#include <initializer_list>

using namespace recognition_trial;
void captureLightingChecks() {
  Controller c(.85f);
  assert(c.lighting==Lighting::Ambient && c.setLighting(Lighting::White));
  assert(c.startManual(3));auto t=c.take();assert(t.illuminated);
  assert(c.beginCapture(t,1000));assert(c.tick(1001).white&&!c.tick(1001).tone);
  assert(!c.setLighting(Lighting::Ambient)&&!c.startOutputTest(1002));
  assert(c.capturePermitted(t,121000)&&c.endCapture(t,121000));
  assert(!c.tick(121001).white); // Illumination ends before model/transfer work.
  assert(c.commit(t,true,1,122000).accepted);c.release(t,123000);
  t=c.take();assert(c.beginCapture(t,124000));
  assert(!c.tick(124000+Controller::captureLightLimitUs).white);
  assert(!c.capturePermitted(t,124000+Controller::captureLightLimitUs));
  assert(!c.endCapture(t,124000+Controller::captureLightLimitUs));
  assert(!c.commit(t,false,0,6000000).present);c.release(t,6000001);
  t=c.take();assert(c.beginCapture(t,6000002));c.stop();
  assert(!c.tick(6000003).white&&!c.endCapture(t,6000004));
  assert(!c.commit(t,true,1,6000005).accepted);c.release(t,6000006);
  assert(c.setLighting(Lighting::Ambient)&&c.startManual(1));t=c.take();
  assert(!t.illuminated&&c.beginCapture(t,7000000));
  assert(!c.tick(7000001).white&&c.endCapture(t,7000002));
  assert(!c.commit(t,true,1,7000003).present);c.release(t,7000004);
  // Light selection also follows a PIR ticket. A rise starts a trial only once armed.
  assert(c.setLighting(Lighting::White));c.setMode(Mode::Pir);assert(c.arm(true));
  assert(!c.pollPir(false,Controller::warmupUs));assert(c.pollPir(true,Controller::warmupUs+1));
  t=c.take();assert(t.pir&&t.illuminated&&c.beginCapture(t,Controller::warmupUs+2));
  c.arm(false);assert(!c.tick(Controller::warmupUs+3).white);c.release(t,Controller::warmupUs+4);
}
void independentOutputChecks() {
  Controller c(.85f);
  assert(c.startOutputTest(100));const auto id=c.outputTestId;
  assert(!c.armed&&!c.busy&&c.trial==0&&c.frame==0);
  assert(c.alarmSource==AlarmSource::OutputTest&&c.tick(100).tone&&c.tick(100).white);
  assert(!c.startOutputTest(101)&&c.alarmStart==100); // No extension/restart.
  assert(!c.arm(true)&&!c.startManual(1)&&!c.take().generation&&!c.setLighting(Lighting::White));
  assert(!c.tick(5500100).tone&&c.alarmSource==AlarmSource::None);
  assert(!c.startOutputTest(5500101)); // Drain the prior terminal event first.
  assert(c.takeOutputTestStart()==id);auto end=c.takeOutputTestEnd();
  assert(end.id==id&&end.completed&&!c.takeOutputTestEnd().id);
  assert(c.startOutputTest(6000000));assert(c.takeOutputTestStart()==id+1);
  c.stop();assert(!c.tick(6000001).tone);end=c.takeOutputTestEnd();assert(end.id==id+1&&!end.completed);
  assert(c.arm(true)&&!c.startOutputTest(6000002));c.arm(false);
  c.setMode(Mode::Pir);assert(!c.startOutputTest(6000003));c.stop();
  assert(c.startManual(1));auto t=c.take();assert(!c.startOutputTest(6000004));
  c.stop();assert(!c.startOutputTest(6000005));c.release(t,6000006);
  assert(c.startOutputTest(6000007));c.setMode(Mode::Pir);
  assert(!c.tick(6000008).tone);assert(c.takeOutputTestStart()==id+2);
  end=c.takeOutputTestEnd();assert(end.id==id+2&&!end.completed);
}
void sensorPauseAndRecordingChecks() {
  Controller c(.85f);
  const int64_t w=Controller::warmupUs;
  // An unconfirmed sensor-started trial sets the 120 s pause.
  c.setMode(Mode::Pir);assert(c.arm(true));
  assert(!c.pollPir(false,w)&&c.pollPir(true,w+1));
  for(int i=0;i<10;++i){auto t=c.take();assert(c.commit(t,true,0,w+10+i).accepted);c.release(t,w+10+i);}
  const int64_t until=c.cooldownUntil;assert(until==w+19+Controller::cooldownUs);
  // STOP, DISARM and mode changes keep it; it ends only when it runs out.
  c.stop();assert(c.cooldownUntil==until);c.setMode(Mode::Pir);assert(c.cooldownUntil==until);
  c.arm(false);assert(c.cooldownUntil==until);assert(c.arm(true));
  assert(!c.pollPir(false,until-3)&&!c.pollPir(true,until-2)&&!c.busy);
  assert(c.pollPir(true,until)&&c.busy&&!c.cooldownUntil); // a still-HIGH sensor at expiry starts
  c.stop();
  // In manual mode the pause still runs out on time.
  Controller m(.85f);m.setMode(Mode::Pir);assert(m.arm(true));
  assert(!m.pollPir(false,w)&&m.pollPir(true,w+1));
  for(int i=0;i<10;++i){auto t=m.take();m.commit(t,true,0,w+10+i);m.release(t,w+10+i);}
  const int64_t mu=m.cooldownUntil;m.setMode(Mode::Manual);
  assert(!m.pollPir(true,mu-1)&&m.cooldownUntil==mu);assert(!m.pollPir(true,mu)&&!m.cooldownUntil);
  // The recording level follows the ticket and changes only while idle.
  Controller r(.85f);
  assert(r.recordInput&&r.setRecording(false)&&r.startManual(2));
  auto t=r.take();assert(!t.recordInput&&!r.setRecording(true));
  r.commit(t,true,.1f,1);r.release(t,2);t=r.take();assert(!t.recordInput);r.commit(t,true,.1f,3);r.release(t,4);
  assert(r.setRecording(true)&&r.startManual(1));t=r.take();assert(t.recordInput);r.commit(t,true,.1f,5);r.release(t,6);
  assert(parseCommand("RECORD FULL").kind==CommandKind::RecordFull&&parseCommand("RECORD JPEG").kind==CommandKind::RecordJpeg);
  for(const char* bad:{"RECORD","RECORD jpeg","RECORD MIXED","RECORD FULL extra"})assert(parseCommand(bad).kind==CommandKind::Invalid);
}
int main() {
  captureLightingChecks();independentOutputChecks();sensorPauseAndRecordingChecks();
  Controller c(.85f);
  assert(!c.take().generation && !c.armed && c.mode==Mode::Manual);
  assert(!c.startManual(0) && !c.startManual(601));
  assert(c.startManual(1));auto a=c.take();
  assert(c.commit(a,true,.99f,100).accepted);c.release(a,200);
  assert(!c.busy && c.alarmStart<0);
  // Independent one-frame trials must not combine into a two-hit confirmation.
  assert(c.startManual(1));a=c.take();assert(!c.commit(a,true,.99f,300).present);c.release(a,400);
  assert(c.arm(true) && c.startManual(8));
  a=c.take();assert(!c.commit(a,true,.99f,1000).present);c.release(a,1000);
  auto b=c.take();auto d=c.commit(b,true,.99f,2000);
  assert(d.present && d.alarmStarted && c.tick(2000).tone && c.tick(2000).white);
  assert(!c.take().generation && !c.startManual(1));
  assert(!c.tick(1502000).tone && c.tick(2002000).tone);
  assert(!c.tick(5502000).tone && c.alarmStart<0);
  // A slow USB transfer can outlast the alarm; it must still end the trial.
  c.release(b,6000000);assert(!c.busy && !c.take().generation);
  // STOP/DISARM invalidate an inference already in flight.
  assert(c.startManual(3));a=c.take();c.stop();assert(!c.commit(a,true,1,7000000).accepted);
  assert(!c.startManual(1));c.release(a,7000000);assert(c.startManual(1));
  a=c.take();c.arm(false);assert(!c.commit(a,true,1,7000001).accepted);c.release(a,7000002);
  c.setMode(Mode::Pir);assert(!c.armed);
  assert(!c.pollPir(false,10) && !c.pollPir(true,20));
  assert(!c.pollPir(true,Controller::warmupUs));
  assert(!c.pollPir(false,Controller::warmupUs+1));
  assert(!c.pollPir(true,Controller::warmupUs+2)&&c.pirHigh&&!c.busy); // disarmed: a rise starts nothing
  assert(c.arm(true)&&!c.pollPir(true,Controller::warmupUs+3));      // held HIGH: needs LOW, then HIGH
  assert(!c.pollPir(false,Controller::warmupUs+4)&&!c.pirHigh);
  assert(c.pollPir(true,Controller::warmupUs+5));
  for(int i=0;i<10;++i){a=c.take();assert(a.pir);assert(c.commit(a,true,0,70000000+i).accepted);c.release(a,70000000+i);}
  assert(!c.busy && c.cooldownUntil>70000000);
  assert(!c.pollPir(true,c.cooldownUntil-1));assert(c.pollPir(true,c.cooldownUntil));
  c.stop();assert(!c.pollPir(true,999999999));
  // Invalid images/scores reset the temporal history within a trial.
  assert(c.startManual(5));a=c.take();assert(!c.commit(a,true,1,1).present);c.release(a,1);
  a=c.take();assert(!c.commit(a,false,1,2).present);c.release(a,2);
  a=c.take();assert(!c.commit(a,true,1,3).present);c.release(a,3);
  a=c.take();assert(!c.commit(a,true,std::numeric_limits<float>::quiet_NaN(),4).present);c.release(a,4);
  a=c.take();assert(!c.commit(a,true,1,5).present);c.release(a,5);
  for(const char* bad:{"CAPTURE 0","CAPTURE -1","CAPTURE 601","CAPTURE 1 extra","CAPTURE 9999999999999999999999","ARM extra","ALARM","MODE PIR\n","LIGHT","LIGHT WHITE extra","TEST","TEST OUTPUT extra","TEST  OUTPUT"})assert(parseCommand(bad).kind==CommandKind::Invalid);
  assert(parseCommand("CAPTURE 600").frames==600 && parseCommand("MODE PIR").kind==CommandKind::Pir);
  assert(parseCommand("LIGHT AMBIENT").kind==CommandKind::Ambient&&parseCommand("LIGHT WHITE").kind==CommandKind::White);
  assert(parseCommand("TEST OUTPUT").kind==CommandKind::OutputTest);
  for(int64_t i=0;i<=5500000;i+=10000){auto out=deterrent::pattern(i);assert(out.tone==(i<5500000&&i%2000000<1500000));assert(out.white==(i<5500000&&(i/100000)%2==0));}
  std::puts("PASS: trial scheduling, isolated confirmation, cancellation, PIR warm-up/arming/rearming/cooldown, pause kept across STOP and mode changes, recording level, long-transfer alarm completion, capture-light isolation/watchdog, independent output-test lifecycle, command boundaries and waveform");
}
