#pragma once
#include <cstdint>
#include <cmath>
#include "temporal_gate.hpp"
#include "../../../shared/alarm_pattern.hpp"

namespace recognition_trial {
enum class Mode { Manual, Pir };
enum class Lighting { Ambient, White };
enum class AlarmSource { None, Recognition, OutputTest };
struct Ticket { uint32_t generation = 0, trial = 0, frame = 0, within = 0; bool pir = false, illuminated = false; };
struct Decision { bool accepted = false, present = false, alarmStarted = false; };
struct OutputTestEnd { uint32_t id = 0; bool completed = false; };

// Hardware-free controller. The application serializes every method with one
// mutex. A generation change makes a completed but cancelled inference inert.
class Controller {
public:
    explicit Controller(float threshold, int64_t bootUs = 0) : gate_(threshold), bootUs_(bootUs) {}
    static constexpr unsigned maxFrames = 600;
    static constexpr int64_t warmupUs = 60000000, cooldownUs = 120000000;
    static constexpr int64_t captureLightLimitUs = 5000000;
    Mode mode = Mode::Manual;
    Lighting lighting = Lighting::Ambient;
    AlarmSource alarmSource = AlarmSource::None;
    bool armed = false, busy = false, inFlight = false;
    uint32_t trial = 0, frame = 0, within = 0, requested = 0;
    int64_t alarmStart = -1, cooldownUntil = 0;
    uint32_t outputTestId = 0;

    void cancel() {
        finishOutputTest(false);
        ++generation_; busy = false; armed = false; alarmStart = -1;
        alarmSource = AlarmSource::None; captureLightUntil_ = 0;
        cooldownUntil = 0; previousPir_ = true; gate_.reset();
        // inFlight remains true until that physical acquisition is released.
    }
    void stop() { cancel(); mode = Mode::Manual; }
    void setMode(Mode value) { cancel(); mode = value; }
    bool setLighting(Lighting value) {
        if (busy || inFlight || alarmStart >= 0) return false;
        lighting = value; return true;
    }
    bool startOutputTest(int64_t now) {
        if (mode != Mode::Manual || armed || busy || inFlight || alarmStart >= 0 || pendingTestEnd_.id) return false;
        ++outputTestId; alarmStart = now; alarmSource = AlarmSource::OutputTest;
        pendingTestStart_ = outputTestId;
        gate_.reset(); return true;
    }
    uint32_t takeOutputTestStart() {
        auto result = pendingTestStart_; pendingTestStart_ = 0; return result;
    }
    OutputTestEnd takeOutputTestEnd() {
        auto result = pendingTestEnd_; pendingTestEnd_ = {}; return result;
    }
    bool arm(bool value) {
        if (!value) { cancel(); return true; }
        if (busy || inFlight || alarmStart >= 0) return false;
        armed = true; return true;
    }
    bool startManual(unsigned count) {
        return mode == Mode::Manual && start(count);
    }
    bool pollPir(bool high, int64_t now) {
        tick(now);
        if (mode != Mode::Pir) return false;
        if (busy || inFlight || alarmStart >= 0) { previousPir_ = true; return false; }
        const bool expired = cooldownUntil && now >= cooldownUntil;
        if (expired) cooldownUntil = 0;
        const bool startNow = now >= bootUs_ + warmupUs && !cooldownUntil && high && (!previousPir_ || expired);
        previousPir_ = high;
        return startNow && start(10);
    }
    Ticket take() {
        if (!busy || inFlight || alarmStart >= 0 || within >= requested) return {};
        inFlight = true; ++within; ++frame;
        return {generation_, trial, frame, within, mode == Mode::Pir, lighting == Lighting::White};
    }
    bool valid(Ticket t) const { return t.generation && t.generation == generation_ && busy && inFlight; }
    bool beginCapture(Ticket t, int64_t now) {
        if (!valid(t) || alarmStart >= 0) return false;
        captureLightUntil_ = t.illuminated ? now + captureLightLimitUs : 0;
        return true;
    }
    bool capturePermitted(Ticket t, int64_t now) const {
        return valid(t) && alarmStart < 0 && (!t.illuminated || now < captureLightUntil_);
    }
    bool endCapture(Ticket t, int64_t now) {
        const bool permitted = capturePermitted(t, now);
        if (t.generation == generation_) captureLightUntil_ = 0;
        return permitted;
    }
    Decision commit(Ticket t, bool validImage, float score, int64_t now) {
        if (!valid(t)) return {};
        const bool present = gate_.update(validImage, score);
        qualified_ |= present;
        bool started = false;
        if (present && armed && alarmStart < 0) {
            alarmStart = now; alarmSource = AlarmSource::Recognition;
            started = true; endAfterFrame_ = true;
        }
        return {true, present, started};
    }
    void release(Ticket t, int64_t now) {
        inFlight = false;
        if (t.generation != generation_ || !busy) return;
        if (within >= requested || endAfterFrame_) {
            busy = false; gate_.reset(); previousPir_ = true;
            if (mode == Mode::Pir && !qualified_) cooldownUntil = now + cooldownUs;
        }
    }
    deterrent::Outputs tick(int64_t now) {
        if (alarmStart >= 0 && (now < alarmStart || now - alarmStart >= deterrent::durationUs)) {
            finishOutputTest(now >= alarmStart);
            alarmStart = -1; alarmSource = AlarmSource::None; previousPir_ = true;
        }
        if (captureLightUntil_ && now >= captureLightUntil_) captureLightUntil_ = 0;
        return alarmStart < 0 ? deterrent::Outputs{captureLightUntil_ != 0, false} : deterrent::pattern(now - alarmStart);
    }
private:
    TemporalDeerGate gate_;
    int64_t bootUs_;
    uint32_t generation_ = 1;
    bool previousPir_ = true, qualified_ = false, endAfterFrame_ = false;
    int64_t captureLightUntil_ = 0;
    OutputTestEnd pendingTestEnd_;
    uint32_t pendingTestStart_ = 0;
    void finishOutputTest(bool completed) {
        if (alarmSource == AlarmSource::OutputTest) pendingTestEnd_ = {outputTestId, completed};
    }
    bool start(unsigned count) {
        if (!count || count > maxFrames || busy || inFlight || alarmStart >= 0) return false;
        ++generation_; ++trial; within = 0; requested = count;
        gate_.reset(); qualified_ = false; endAfterFrame_ = false; busy = true; return true;
    }
};
}
