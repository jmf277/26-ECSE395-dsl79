#pragma once
#include "../../../shared/alarm_pattern.hpp"
#include "solar.hpp"
#include <cstdint>

namespace night_control {
constexpr int64_t maxClockAgeUs=26LL*60*60*1000000;
constexpr int64_t alarmDurationUs=deterrent::durationUs;
struct Configuration {
    bool configured=false;
    double latitude=0,longitude=0;
    unsigned afterSunsetMinutes=0,beforeSunriseMinutes=0;
};
bool validConfiguration(const Configuration&);
enum class State { Fault, Unconfigured, Paused, Calibrating, InvalidTime, SolarUnavailable, Daylight, NeedsBaseline, Armed, Alarm };
const char* stateName(State);
enum class Capture { Monitoring, Calibration };
struct Ticket { uint64_t generation=0; Capture kind=Capture::Monitoring; };
struct Outputs { bool white=false,tone=false; };
struct Snapshot {
    State state=State::Unconfigured;
    bool configured=false,paused=true,clockValid=false,night=false,baselineReady=false;
    bool captureAllowed=false,alarmActive=false,calibrationRequested=false;
    int64_t utcUs=0,clockAgeUs=-1,windowStart=0,windowEnd=0,nextStart=0;
    uint64_t generation=0;
    Outputs outputs;
};
// The firmware and host tests use this same permission/output state machine.
// Its caller serializes access; no RTOS, GPIO, network, or wall-clock dependencies.
class Core {
public:
    void configure(const Configuration&,bool persistedPaused,int64_t nowUs);
    void setPaused(bool,int64_t nowUs);
    void fault(int64_t nowUs);
    bool synchronize(int64_t utcUs,int64_t nowUs);
    void invalidateClock(int64_t nowUs);
    bool requestCalibration(int64_t nowUs);
    void calibrationComplete(int64_t nowUs);
    void calibrationAborted(int64_t nowUs);
    void invalidateBaseline(int64_t nowUs);
    void cancel(int64_t nowUs);
    Ticket ticket(Capture,int64_t nowUs);
    bool ticketValid(Ticket,int64_t nowUs);
    bool captureLight(Ticket,bool on,int64_t nowUs);
    bool startAlarm(Ticket,int64_t nowUs);
    Snapshot tick(int64_t nowUs);
    const Configuration& configuration() const {return config_;}
private:
    Configuration config_{};
    bool paused_=true,fault_=false,synced_=false,baseline_=false,manualCalibration_=false;
    int64_t syncUtc_=0,syncMono_=0,alarmStart_=-1,lastDay_=INT64_MIN;
    uint64_t generation_=1;
    bool previousPermission_=false;
    solar::Schedule windows_{};
    Outputs outputs_{};
    bool captureLit_=false;
    Ticket captureTicket_{};
    void invalidate();
    Snapshot refresh(int64_t nowUs);
    bool permits(Capture,const Snapshot&) const;
};
}
