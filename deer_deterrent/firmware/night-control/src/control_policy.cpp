#include "control_policy.hpp"
#include <cmath>
#include <cstdlib>
#include "../../../shared/alarm_pattern.hpp"

namespace night_control {
bool validConfiguration(const Configuration& c) {
    return c.configured&&std::isfinite(c.latitude)&&std::isfinite(c.longitude)&&
        c.latitude>-90&&c.latitude<90&&c.longitude>=-180&&c.longitude<=180&&
        c.afterSunsetMinutes<=120&&c.beforeSunriseMinutes<=120;
}
const char* stateName(State s) {
    switch(s) {
        case State::Fault:return "FAULT";case State::Unconfigured:return "UNCONFIGURED";
        case State::Paused:return "PAUSED";case State::Calibrating:return "CALIBRATING";
        case State::InvalidTime:return "WAIT_TIME";case State::SolarUnavailable:return "SOLAR_UNAVAILABLE";
        case State::Daylight:return "DAYTIME";case State::NeedsBaseline:return "NEEDS_BASELINE";
        case State::Armed:return "ARMED";case State::Alarm:return "ALARM_ACTIVE";
    }
    return "FAULT";
}
void Core::invalidate() {
    ++generation_;alarmStart_=-1;outputs_={};captureLit_=false;
}
void Core::configure(const Configuration& c,bool paused,int64_t now) {
    config_=c;paused_=paused;manualCalibration_=false;baseline_=false;
    synced_=false;lastDay_=INT64_MIN;invalidate();refresh(now);
}
void Core::setPaused(bool paused,int64_t now) {
    if(paused_!=paused) {paused_=paused;manualCalibration_=false;invalidate();}
    refresh(now);
}
void Core::fault(int64_t now) {fault_=true;paused_=true;manualCalibration_=false;invalidate();refresh(now);}
bool Core::synchronize(int64_t utc,int64_t now) {
    // Reject unset/implausible server timestamps. This is not authentication of NTP.
    constexpr int64_t earliest=1577836800LL*1000000; // 2020-01-01
    constexpr int64_t latest=4102444800LL*1000000;   // 2100-01-01
    if(utc<earliest||utc>=latest||now<0) {invalidateClock(now);return false;}
    const bool jump=!synced_||now<syncMono_||now-syncMono_>=maxClockAgeUs||
        std::llabs(utc-(syncUtc_+now-syncMono_))>2000000;
    syncUtc_=utc;syncMono_=now;synced_=true;lastDay_=INT64_MIN;
    if(jump) invalidate();
    refresh(now);return true;
}
void Core::invalidateClock(int64_t now) {synced_=false;invalidate();refresh(now);}
bool Core::requestCalibration(int64_t now) {
    refresh(now);
    if(fault_||paused_||!validConfiguration(config_)) return false;
    invalidate();baseline_=false;manualCalibration_=true;return true;
}
void Core::calibrationComplete(int64_t now) {baseline_=true;manualCalibration_=false;invalidate();refresh(now);}
void Core::calibrationAborted(int64_t now) {baseline_=false;manualCalibration_=false;invalidate();refresh(now);}
void Core::invalidateBaseline(int64_t now) {baseline_=false;invalidate();refresh(now);}
void Core::cancel(int64_t now) {manualCalibration_=false;invalidate();refresh(now);}
bool Core::permits(Capture purpose,const Snapshot& s) const {
    if(fault_||paused_||!s.configured||alarmStart_>=0) return false;
    if(purpose==Capture::Calibration) return manualCalibration_||(s.clockValid&&s.night&&!baseline_);
    return s.clockValid&&s.night&&baseline_&&!manualCalibration_;
}
Snapshot Core::refresh(int64_t now) {
    Snapshot s;
    s.configured=validConfiguration(config_);s.paused=paused_;s.baselineReady=baseline_;
    s.clockAgeUs=synced_&&now>=syncMono_?now-syncMono_:-1;
    s.clockValid=s.clockAgeUs>=0&&s.clockAgeUs<maxClockAgeUs;
    if(s.clockValid) s.utcUs=syncUtc_+s.clockAgeUs;
    bool solarAvailable=false;
    if(s.clockValid&&s.configured) {
        const int64_t utc=s.utcUs/1000000,day=solar::dayStart(utc);
        if(day!=lastDay_) {windows_=solar::schedule(utc,config_.latitude,config_.longitude,
            config_.afterSunsetMinutes,config_.beforeSunriseMinutes);lastDay_=day;}
        for(const auto& w:windows_) if(w.start<w.end) {
            solarAvailable=true;
            if(utc>=w.start&&utc<w.end) {s.night=true;s.windowStart=w.start;s.windowEnd=w.end;}
            if(w.start>utc&&(s.nextStart==0||w.start<s.nextStart)) s.nextStart=w.start;
        }
    }
    const bool permission=s.configured&&!paused_&&!fault_&&s.clockValid&&s.night;
    if(permission!=previousPermission_) {previousPermission_=permission;invalidate();}
    if(alarmStart_>=0&&(!permission||!baseline_||manualCalibration_||now<alarmStart_||now-alarmStart_>=alarmDurationUs))
        invalidate(); // Consume all pre-alarm tickets; a finished event cannot replay.
    if(alarmStart_>=0) {
        const auto elapsed=now-alarmStart_;
        const auto waveform=deterrent::pattern(elapsed);
        outputs_.tone=waveform.tone;
        outputs_.white=waveform.white;
    } else if(captureLit_) {
        if(captureTicket_.generation!=generation_||!permits(captureTicket_.kind,s)) {captureLit_=false;outputs_={};}
    } else outputs_={};
    s.alarmActive=alarmStart_>=0;s.calibrationRequested=manualCalibration_;
    if(fault_) s.state=State::Fault;
    else if(!s.configured) s.state=State::Unconfigured;
    else if(paused_) s.state=State::Paused;
    else if(manualCalibration_) s.state=State::Calibrating;
    else if(!s.clockValid) s.state=State::InvalidTime;
    else if(!solarAvailable) s.state=State::SolarUnavailable;
    else if(!s.night) s.state=State::Daylight;
    else if(!baseline_) s.state=State::NeedsBaseline;
    else if(s.alarmActive) s.state=State::Alarm;
    else s.state=State::Armed;
    s.captureAllowed=permits(baseline_?Capture::Monitoring:Capture::Calibration,s);
    s.generation=generation_;s.outputs=outputs_;return s;
}
Ticket Core::ticket(Capture kind,int64_t now) {auto s=refresh(now);return permits(kind,s)?Ticket{generation_,kind}:Ticket{};}
bool Core::ticketValid(Ticket t,int64_t now) {auto s=refresh(now);return t.generation&&t.generation==generation_&&permits(t.kind,s);}
bool Core::captureLight(Ticket t,bool on,int64_t now) {
    auto s=refresh(now);
    if(!on) {if(captureLit_&&captureTicket_.generation==t.generation){captureLit_=false;outputs_={};}return true;}
    if(t.generation==0||t.generation!=generation_||!permits(t.kind,s)) return false;
    captureTicket_=t;captureLit_=true;outputs_.white=true;outputs_.tone=false;return true;
}
bool Core::startAlarm(Ticket t,int64_t now) {
    auto s=refresh(now);
    if(t.kind!=Capture::Monitoring||t.generation==0||t.generation!=generation_||!permits(t.kind,s)) return false;
    alarmStart_=now;captureLit_=false;outputs_={true,true};return true;
}
Snapshot Core::tick(int64_t now) {return refresh(now);}
}
