#include "control_policy.hpp"
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <limits>
#include <random>

using namespace night_control;
static unsigned checks=0;
static void check(bool value,const char* what,int line) {
    ++checks;
    if(!value) {std::fprintf(stderr,"FAIL line %d: %s\n",line,what);std::exit(1);}
}
#define CHECK(v) check((v),#v,__LINE__)
static bool off(const Snapshot& s) {return !s.outputs.white&&!s.outputs.tone&&!s.alarmActive;}
static Configuration config() {return {true,41.5,-81.69,0,0};}
static Core armed(int64_t utc=1793750400,int64_t now=1000000) {
    Core c;c.configure(config(),false,now);
    CHECK(c.synchronize(utc*1000000,now));c.calibrationComplete(now);
    CHECK(c.tick(now).state==State::Armed);return c;
}

int main() {
    constexpr int64_t now=1000000,night=1793750400;
    Core c;
    auto s=c.tick(now);CHECK(s.paused&&!s.configured&&!s.clockValid&&off(s));
    c.configure(config(),true,now);CHECK(c.tick(now).state==State::Paused);
    CHECK(c.synchronize(night*1000000,now));CHECK(!c.requestCalibration(now));
    CHECK(!c.ticketValid(c.ticket(Capture::Monitoring,now),now));
    c.cancel(now);CHECK(c.tick(now).paused&&off(c.tick(now)));
    c.setPaused(false,now);CHECK(c.tick(now).state==State::NeedsBaseline);
    c.calibrationComplete(now);CHECK(c.tick(now).state==State::Armed);

    // Persistent intention does not manufacture fresh boot time or a baseline.
    Core reboot;reboot.configure(config(),false,now);
    CHECK(reboot.tick(now).state==State::InvalidTime);CHECK(off(reboot.tick(now)));
    CHECK(reboot.synchronize(night*1000000,now));CHECK(!reboot.tick(now).baselineReady);

    // Manual enabled calibration is an illumination-only exception.
    Core calibration;calibration.configure(config(),false,now);
    CHECK(calibration.requestCalibration(now));
    auto ct=calibration.ticket(Capture::Calibration,now);
    CHECK(calibration.captureLight(ct,true,now));CHECK(calibration.tick(now).outputs.white);
    CHECK(!calibration.startAlarm(ct,now));
    calibration.setPaused(true,now+1);CHECK(off(calibration.tick(now+1)));
    CHECK(!calibration.ticketValid(ct,now+1));CHECK(!calibration.requestCalibration(now+1));
    calibration.cancel(now+2);CHECK(calibration.tick(now+2).paused);
    calibration.setPaused(false,now+3);CHECK(!calibration.ticketValid(ct,now+3));
    CHECK(!calibration.captureLight(ct,true,now+3));

    c=armed();auto t=c.ticket(Capture::Monitoring,now);
    CHECK(c.startAlarm(t,now));CHECK(!c.startAlarm(t,now));
    const int64_t phases[]={0,1499999,1500000,1999999,2000000,3499999,3500000,3999999,4000000,5499999,5500000};
    const bool tones[]={true,true,false,false,true,true,false,false,true,true,false};
    for(unsigned i=0;i<sizeof(phases)/sizeof(phases[0]);++i) {
        s=c.tick(now+phases[i]);CHECK(s.outputs.tone==tones[i]);
        CHECK(s.alarmActive==(phases[i]<alarmDurationUs));
        CHECK(s.outputs.white==(phases[i]<alarmDurationUs&&((phases[i]/100000)%2==0)));
    }
    CHECK(!c.ticketValid(t,now+alarmDurationUs));
    CHECK(!c.startAlarm(t,now+alarmDurationUs));

    for(bool illumination:{false,true}) {
        c=armed();t=c.ticket(Capture::Monitoring,now);
        CHECK(illumination?c.captureLight(t,true,now):c.startAlarm(t,now));
        c.setPaused(true,now+1234);CHECK(off(c.tick(now+1234)));
        c.cancel(now+1235);CHECK(c.tick(now+1235).paused);
        CHECK(c.synchronize(night*1000000+1236,now+1236));
        CHECK(off(c.tick(now+1236))&&c.tick(now+1236).paused);
        CHECK(!c.requestCalibration(now+1236));
        c.setPaused(false,now+1237);CHECK(!c.ticketValid(t,now+1237));
        CHECK(!c.startAlarm(t,now+1237));CHECK(!c.captureLight(t,true,now+1237));
    }

    // Exact freshness boundary and invalid timestamps are fail-closed.
    c=armed();CHECK(c.tick(now+maxClockAgeUs-1).clockValid);
    CHECK(!c.tick(now+maxClockAgeUs).clockValid);CHECK(off(c.tick(now+maxClockAgeUs)));
    CHECK(!c.synchronize(1577836800LL*1000000-1,now));CHECK(!c.tick(now).clockValid);
    CHECK(!c.synchronize(4102444800LL*1000000,now));CHECK(!c.tick(now).clockValid);
    CHECK(!c.synchronize(night*1000000,-1));
    c=armed();CHECK(!c.tick(now-1).clockValid);CHECK(off(c.tick(now-1)));
    c=armed();t=c.ticket(Capture::Monitoring,now);CHECK(c.startAlarm(t,now));
    c.invalidateClock(now+1);CHECK(off(c.tick(now+1)));
    CHECK(c.synchronize(night*1000000+2,now+2));CHECK(!c.ticketValid(t,now+2));
    c=armed();t=c.ticket(Capture::Monitoring,now);CHECK(c.captureLight(t,true,now));
    CHECK(c.synchronize(night*1000000+3000000,now+1));CHECK(!c.ticketValid(t,now+1));
    CHECK(off(c.tick(now+1)));

    // Actual event boundaries and inward margins; the end is exclusive.
    auto windows=solar::schedule(night,41.5,-81.69,17,23);
    solar::Window w{};for(auto v:windows) if(v.start<=night&&night<v.end) w=v;
    CHECK(w.start<w.end);
    auto boundary=config();boundary.afterSunsetMinutes=17;boundary.beforeSunriseMinutes=23;
    Core edge;edge.configure(boundary,false,now);
    CHECK(edge.synchronize((w.start-1)*1000000,now));edge.calibrationComplete(now);
    CHECK(!edge.tick(now).night);CHECK(edge.tick(now+999999).state==State::Daylight);
    CHECK(edge.tick(now+1000000).state==State::Armed);
    CHECK(edge.synchronize((w.end-1)*1000000,now+2000000));
    t=edge.ticket(Capture::Monitoring,now+2000000);CHECK(edge.startAlarm(t,now+2000000));
    CHECK(edge.tick(now+2999999).alarmActive);
    CHECK(off(edge.tick(now+3000000)));CHECK(!edge.tick(now+3000000).night);
    CHECK(!edge.ticketValid(t,now+3000000));

    CHECK(solar::dayStart(-1)==-86400);CHECK(solar::dayStart(0)==0);
    CHECK(solar::dayStart(86399)==0);CHECK(solar::dayStart(86400)==86400);
    auto invalid=config();invalid.latitude=std::numeric_limits<double>::quiet_NaN();
    CHECK(!validConfiguration(invalid));invalid=config();invalid.longitude=std::numeric_limits<double>::infinity();
    CHECK(!validConfiguration(invalid));
    for(double lat:{-90.0,90.0}) {invalid=config();invalid.latitude=lat;CHECK(!validConfiguration(invalid));}
    for(double lon:{-180.0,180.0}) {invalid=config();invalid.longitude=lon;CHECK(validConfiguration(invalid));}
    invalid=config();invalid.afterSunsetMinutes=121;CHECK(!validConfiguration(invalid));
    invalid=config();invalid.beforeSunriseMinutes=121;CHECK(!validConfiguration(invalid));
    CHECK(!solar::events(1782000000,89,0).valid);CHECK(!solar::events(1782000000,-89,0).valid);
    for(auto q:solar::schedule(1782000000,65,0,120,120)) CHECK(q.start==q.end);

    // Pause priority under randomized command order and clocks. All operations
    // here intentionally exclude explicit resume, which is the only release.
    std::mt19937 generator(0x4e494748);
    c=armed();c.setPaused(true,now);
    int64_t mono=now;
    for(unsigned i=0;i<2000;++i) {
        mono+=1+generator()%100000;
        switch(generator()%6) {
            case 0:c.cancel(mono);break;
            case 1:CHECK(!c.requestCalibration(mono));break;
            case 2:c.synchronize((night+generator()%172800)*1000000,mono);break;
            case 3:c.invalidateClock(mono);break;
            case 4:c.invalidateBaseline(mono);break;
            case 5:c.setPaused(true,mono);break;
        }
        s=c.tick(mono);CHECK(s.paused);CHECK(off(s));
        CHECK(!c.startAlarm(t,mono));CHECK(!c.captureLight(t,true,mono));
        CHECK(!c.ticketValid(c.ticket(Capture::Calibration,mono),mono));
    }
    std::printf("PASS independent core review checks=%u\n",checks);
}
