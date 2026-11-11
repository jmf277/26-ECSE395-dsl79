// Score-level test of the trial program's decision-to-alarm path, run on a
// computer against the controller the firmware compiles (src/trial_control.hpp).
// Scores are injected where the model's frame score goes, and detections are
// injected at the decision point with INJECT DEER's controller entry.
//
// The settings and the expected result of every case are fixed in this file
// before it runs. Each case runs kRuns times with a different start time and
// frame spacing; a case passes only if every run matches its expected result.
// The checks do not use assert(), so they also run in a build with NDEBUG.
#include "../src/trial_control.hpp"
#include "../src/trial_command.hpp"
#include <cstdio>
#include <cstdint>
#include <vector>

using namespace recognition_trial;

namespace {
constexpr float kThreshold = 0.85f;          // CONFIG_DEER_THRESHOLD_PERCENT=85
constexpr int kRuns = 10;                    // runs per case
constexpr int64_t kTickUs = 20000;           // the firmware's output timer period
constexpr int64_t kFramePeriodUs = 2117000;  // frame period of the recorded trials
constexpr float kInvalid = -1.0f;            // marks a frame whose capture or inference failed

// What one run observed.
struct Observed {
    bool alarm = false;              // an alarm ran
    int alarmFrame = 0;              // frame whose decision started it (0: none or injected)
    AlarmSource source = AlarmSource::None;
    int framesRun = 0;               // frames taken before the trial ended
    int whiteEdges = 0, toneEdges = 0;  // rising edges while the alarm ran, sampled every tick
    int64_t durationUs = 0;          // alarm start to the tick that ended it
    bool commandAccepted = true;     // for cases that issue a refused or accepted command
    bool endedCompleted = false;     // injection end event reported completed
    bool endedCancelled = false;     // injection or output end reported cancelled
};

// Expected result, fixed before the run.
struct Expected {
    bool alarm;
    int alarmFrame;
    AlarmSource source;
    int framesRun;
    int whiteEdges, toneEdges;
    bool fullDuration;               // the alarm ran its whole 5.5 s
    bool commandAccepted;
    bool endedCompleted, endedCancelled;
};

int64_t runStart(int run) { return 70000000 + int64_t(run) * 1234567; }  // after the 60 s warm-up
int64_t period(int run) { return kFramePeriodUs + int64_t(run) * 1000; }

// Samples the outputs every tick from start until the alarm ends or `until`.
void watchAlarm(Controller& c, int64_t start, int64_t until, Observed& o) {
    bool white = false, tone = false;
    for (int64_t t = start; t <= until; t += kTickUs) {
        const bool running = c.alarmStart >= 0;
        const auto out = c.tick(t);
        if (out.white && !white) ++o.whiteEdges;
        if (out.tone && !tone) ++o.toneEdges;
        white = out.white; tone = out.tone;
        if (running && c.alarmStart < 0) { o.durationUs = t - start; return; }
    }
}

// Runs one manual trial of `scores.size()` frames at the given spacing.
// A frame stops being taken once the controller ends the trial.
Observed manualTrial(bool armed, const std::vector<float>& scores, int run) {
    Controller c(kThreshold);
    Observed o;
    if (armed) c.arm(true);
    c.startManual(unsigned(scores.size()));
    int64_t now = runStart(run);
    for (size_t i = 0; i < scores.size(); ++i) {
        const auto t = c.take();
        if (!t.generation) break;
        ++o.framesRun;
        c.beginCapture(t, now); c.endCapture(t, now + 280000);
        const bool valid = scores[i] != kInvalid;
        const int64_t decided = now + 700000;
        const auto d = c.commit(t, valid, valid ? scores[i] : 0.0f, decided);
        if (d.alarmStarted) { o.alarm = true; o.alarmFrame = int(i) + 1; o.source = c.alarmSource; }
        if (d.alarmStarted) watchAlarm(c, decided, decided + 6000000, o);
        c.release(t, decided + 900000);
        now += period(run);
    }
    return o;
}

bool same(const Observed& o, const Expected& e) {
    if (o.alarm != e.alarm || o.alarmFrame != e.alarmFrame || o.source != e.source) return false;
    if (o.framesRun != e.framesRun || o.commandAccepted != e.commandAccepted) return false;
    if (o.endedCompleted != e.endedCompleted || o.endedCancelled != e.endedCancelled) return false;
    if (e.alarm) {
        if (o.whiteEdges != e.whiteEdges || o.toneEdges != e.toneEdges) return false;
        const bool full = o.durationUs >= deterrent::durationUs && o.durationUs < deterrent::durationUs + kTickUs;
        if (full != e.fullDuration) return false;
    }
    return true;
}

// ---- the cases ------------------------------------------------------------

// Two qualifying scores in a row: present on the second frame, the alarm
// starts at that frame's decision and the trial ends after it.
Observed qualifying(int run) { return manualTrial(true, {0.90f, 0.90f, 0.90f}, run); }
// A score exactly at the threshold counts.
Observed atThreshold(int run) { return manualTrial(true, {0.85f, 0.85f, 0.85f}, run); }
// Just below the threshold never counts.
Observed belowThreshold(int run) { return manualTrial(true, {0.8499f, 0.8499f, 0.8499f}, run); }
// Two of the last three: hit, miss, hit confirms on the third frame.
Observed twoOfThree(int run) { return manualTrial(true, {0.90f, 0.10f, 0.90f}, run); }
// Disarmed: the decision is present but no alarm starts; all frames run.
Observed disarmed(int run) { return manualTrial(false, {0.90f, 0.90f, 0.90f}, run); }
// An invalid frame clears the history: hit, invalid, hit is not enough; the
// fourth frame's hit confirms.
Observed invalidFrame(int run) { return manualTrial(true, {0.90f, kInvalid, 0.90f, 0.90f}, run); }

// STOP one second into a recognition alarm: outputs off at once, disarmed.
Observed stopDuringAlarm(int run) {
    Controller c(kThreshold);
    Observed o;
    c.arm(true); c.startManual(2);
    int64_t now = runStart(run);
    for (int i = 0; i < 2; ++i) {
        const auto t = c.take(); ++o.framesRun;
        c.beginCapture(t, now); c.endCapture(t, now + 280000);
        const auto d = c.commit(t, true, 0.95f, now + 700000);
        if (d.alarmStarted) { o.alarm = true; o.alarmFrame = i + 1; o.source = c.alarmSource; }
        c.release(t, now + 900000);
        if (d.alarmStarted) {
            const int64_t start = now + 700000;
            bool white = false, tone = false;
            for (int64_t s = start; s < start + 1000000; s += kTickUs) {
                const auto out = c.tick(s);
                if (out.white && !white) ++o.whiteEdges;
                if (out.tone && !tone) ++o.toneEdges;
                white = out.white; tone = out.tone;
            }
            c.stop();
            const auto out = c.tick(start + 1000000);
            o.durationUs = 1000000;
            o.endedCancelled = !out.white && !out.tone && c.alarmSource == AlarmSource::None && !c.armed && c.mode == Mode::Manual;
        }
        now += period(run);
    }
    return o;
}

// A sensor rise during a recognition alarm is refused; after the alarm the
// sensor must go LOW and then HIGH again before another trial starts. The
// sensor toggles for the first 4 s of the alarm and is HIGH from then on.
Observed pirDuringAlarm(int run) {
    Controller c(kThreshold);
    Observed o;
    c.setMode(Mode::Pir); c.arm(true);
    const int64_t base = runStart(run);
    c.pollPir(false, base);
    o.commandAccepted = c.pollPir(true, base + kTickUs);   // the rise that starts the trial
    int64_t now = base + 2 * kTickUs;
    for (int i = 0; i < 2; ++i) {
        const auto t = c.take(); if (!t.generation) break; ++o.framesRun;
        c.beginCapture(t, now); c.endCapture(t, now + 280000);
        const auto d = c.commit(t, true, 0.95f, now + 700000);
        if (d.alarmStarted) { o.alarm = true; o.alarmFrame = i + 1; o.source = c.alarmSource; }
        c.release(t, now + 900000);
        if (!d.alarmStarted) now += period(run);
    }
    const int64_t start = c.alarmStart;
    bool refused = true, white = false, tone = false;
    for (int64_t s = start; c.alarmStart >= 0 && s < start + 6000000; s += kTickUs) {
        const bool level = s - start >= 4000000 || ((s - start) / 300000) % 2 == 1;
        const bool running = c.alarmStart >= 0;
        if (c.pollPir(level, s)) refused = false;
        const auto out = c.tick(s);
        if (out.white && !white) ++o.whiteEdges;
        if (out.tone && !tone) ++o.toneEdges;
        white = out.white; tone = out.tone;
        if (running && c.alarmStart < 0) o.durationUs = s - start;
    }
    const int64_t after = start + 6000000;
    const bool heldHighRefused = !c.pollPir(true, after) && !c.pollPir(true, after + kTickUs);
    const bool lowThenHigh = !c.pollPir(false, after + 2 * kTickUs) && c.pollPir(true, after + 3 * kTickUs);
    o.endedCompleted = refused && heldHighRefused && lowThenHigh;
    return o;
}

// A sensor rise while disarmed starts no trial (the program starts a sensor
// trial only once armed); after ARM, the next LOW then HIGH starts one.
Observed disarmedRise(int run) {
    Controller c(kThreshold);
    Observed o;
    c.setMode(Mode::Pir);
    const int64_t base = runStart(run);
    c.pollPir(false, base);
    o.commandAccepted = c.pollPir(true, base + kTickUs);
    o.framesRun = c.take().generation ? 1 : 0;
    c.arm(true);
    o.endedCompleted = !c.pollPir(true, base + 2 * kTickUs) && !c.pollPir(false, base + 3 * kTickUs) &&
                       c.pollPir(true, base + 4 * kTickUs);
    return o;
}

// CAPTURE during a recognition alarm is refused; after it ends it is accepted.
Observed captureDuringAlarm(int run) {
    Observed o = manualTrial(true, {0.90f, 0.90f}, run);
    Controller c(kThreshold);
    c.arm(true); c.startManual(2);
    int64_t now = runStart(run);
    for (int i = 0; i < 2; ++i) {
        const auto t = c.take();
        c.beginCapture(t, now); c.endCapture(t, now + 280000);
        c.commit(t, true, 0.95f, now + 700000); c.release(t, now + 900000);
        now += period(run);
    }
    const int64_t start = c.alarmStart;
    const bool refused = !c.startManual(1) && !c.startManual(600);
    c.tick(start + deterrent::durationUs);
    o.commandAccepted = refused && c.startManual(1);
    return o;
}

// INJECT DEER while armed: the alarm starts at once through the decision's
// branch, no frame is taken, and the end is reported completed.
Observed injectedArmed(int run) {
    Controller c(kThreshold);
    Observed o;
    c.arm(true);
    const int64_t start = runStart(run);
    o.commandAccepted = c.injectDetection(start);
    o.alarm = c.alarmStart == start; o.source = c.alarmSource;
    const auto taken = c.take();
    o.framesRun = taken.generation ? 1 : 0;
    const auto begun = c.takeInjectionStart();
    watchAlarm(c, start, start + 6000000, o);
    const auto end = c.takeInjectionEnd();
    o.endedCompleted = begun.id == 1 && begun.alarmStartUs == start && end.id == 1 && end.completed &&
                       end.alarmStartUs == start && end.endedUs == start + o.durationUs && c.armed;
    return o;
}
// INJECT DEER while disarmed is refused and starts nothing.
Observed injectedDisarmed(int run) {
    Controller c(kThreshold);
    Observed o;
    o.commandAccepted = c.injectDetection(runStart(run));
    o.alarm = c.alarmStart >= 0; o.source = c.alarmSource;
    return o;
}
// INJECT DEER while a trial is running, or in sensor mode, is refused.
Observed injectedBusyOrPir(int run) {
    Controller c(kThreshold);
    Observed o;
    c.arm(true); c.startManual(3);
    const bool busyRefused = !c.injectDetection(runStart(run));
    c.stop(); c.setMode(Mode::Pir); c.arm(true);
    const bool pirRefused = !c.injectDetection(runStart(run) + kTickUs);
    o.commandAccepted = !(busyRefused && pirRefused);
    o.alarm = c.alarmStart >= 0; o.source = c.alarmSource;
    return o;
}
// STOP one second into an injected alarm: outputs off, end reported cancelled.
Observed stopDuringInjection(int run) {
    Controller c(kThreshold);
    Observed o;
    c.arm(true);
    const int64_t start = runStart(run);
    o.commandAccepted = c.injectDetection(start);
    o.alarm = c.alarmStart == start; o.source = c.alarmSource;
    bool white = false, tone = false;
    for (int64_t s = start; s < start + 1000000; s += kTickUs) {
        const auto out = c.tick(s);
        if (out.white && !white) ++o.whiteEdges;
        if (out.tone && !tone) ++o.toneEdges;
        white = out.white; tone = out.tone;
    }
    c.stop();
    const auto out = c.tick(start + 1000000);
    o.durationUs = 1000000;
    const auto end = c.takeInjectionEnd();
    o.endedCancelled = end.id == 1 && !end.completed && end.endedUs < 0 && !out.white && !out.tone;
    return o;
}
// A second INJECT DEER during the injected alarm is refused and does not restart it.
Observed secondInjection(int run) {
    Controller c(kThreshold);
    Observed o;
    c.arm(true);
    const int64_t start = runStart(run);
    c.injectDetection(start);
    o.commandAccepted = c.injectDetection(start + 2000000);
    o.alarm = c.alarmStart == start; o.source = c.alarmSource;
    watchAlarm(c, start, start + 6000000, o);
    // After the end event is taken, a new injection is accepted again.
    c.takeInjectionStart(); c.takeInjectionEnd();
    o.endedCompleted = c.injectDetection(start + 7000000);
    return o;
}

struct Case { const char* name; const char* what; Observed (*run)(int); Expected expected; };
constexpr auto R = AlarmSource::Recognition;
constexpr auto I = AlarmSource::InjectedDetection;
constexpr auto N = AlarmSource::None;
// name, description, run, {alarm, alarmFrame, source, framesRun, whiteEdges, toneEdges,
//                          fullDuration, commandAccepted, endedCompleted, endedCancelled}
const Case kCases[] = {
    {"qualifying", "armed, scores 0.90 0.90 0.90: alarm at frame 2, trial ends", qualifying,
     {true, 2, R, 2, 28, 3, true, true, false, false}},
    {"at_threshold", "armed, scores 0.85 0.85 0.85: 0.85 counts, alarm at frame 2", atThreshold,
     {true, 2, R, 2, 28, 3, true, true, false, false}},
    {"below_threshold", "armed, scores 0.8499 x3: no alarm, 3 frames", belowThreshold,
     {false, 0, N, 3, 0, 0, false, true, false, false}},
    {"two_of_three", "armed, scores 0.90 0.10 0.90: alarm at frame 3", twoOfThree,
     {true, 3, R, 3, 28, 3, true, true, false, false}},
    {"disarmed", "disarmed, scores 0.90 x3: present, no alarm, 3 frames", disarmed,
     {false, 0, N, 3, 0, 0, false, true, false, false}},
    {"invalid_frame", "armed, 0.90 invalid 0.90 0.90: history cleared, alarm at frame 4", invalidFrame,
     {true, 4, R, 4, 28, 3, true, true, false, false}},
    {"stop_during_alarm", "STOP 1 s into the alarm: outputs off, disarmed", stopDuringAlarm,
     {true, 2, R, 2, 5, 1, false, true, false, true}},
    {"pir_during_alarm", "sensor rises during the alarm refused; LOW then HIGH needed after", pirDuringAlarm,
     {true, 2, R, 2, 28, 3, true, true, true, false}},
    {"disarmed_sensor_rise", "sensor rise while disarmed: no trial; after ARM, LOW then HIGH starts one", disarmedRise,
     {false, 0, N, 0, 0, 0, false, false, true, false}},
    {"capture_during_alarm", "CAPTURE refused during the alarm, accepted after", captureDuringAlarm,
     {true, 2, R, 2, 28, 3, true, true, false, false}},
    {"inject_armed", "INJECT DEER armed: alarm at once, no frame, completed", injectedArmed,
     {true, 0, I, 0, 28, 3, true, true, true, false}},
    {"inject_disarmed", "INJECT DEER disarmed: refused", injectedDisarmed,
     {false, 0, N, 0, 0, 0, false, false, false, false}},
    {"inject_busy_or_pir", "INJECT DEER during a trial or in sensor mode: refused", injectedBusyOrPir,
     {false, 0, N, 0, 0, 0, false, false, false, false}},
    {"stop_during_injection", "STOP 1 s into an injected alarm: off, cancelled", stopDuringInjection,
     {true, 0, I, 0, 5, 1, false, true, false, true}},
    {"second_injection", "INJECT DEER during the injected alarm: refused, no restart", secondInjection,
     {true, 0, I, 0, 28, 3, true, false, true, false}},
};
}  // namespace

int main() {
    std::printf("Decision-to-alarm test: threshold %.2f, two of the last three frames, "
                "%d runs per case, output tick %lld us, frame period from %lld us\n",
                double(kThreshold), kRuns, static_cast<long long>(kTickUs),
                static_cast<long long>(kFramePeriodUs));
    // The command the documents name parses as the injection command.
    bool ok = parseCommand("INJECT DEER").kind == CommandKind::InjectDeer &&
              parseCommand("INJECT").kind == CommandKind::Invalid &&
              parseCommand("INJECT DEER now").kind == CommandKind::Invalid;
    if (!ok) std::printf("FAIL command parsing of INJECT DEER\n");
    const int total = int(sizeof(kCases) / sizeof(kCases[0]));
    int matched = 0;
    for (const auto& item : kCases) {
        int passed = 0;
        for (int run = 0; run < kRuns; ++run) passed += same(item.run(run), item.expected) ? 1 : 0;
        std::printf("%s %-22s %2d/%d  %s\n", passed == kRuns ? "PASS" : "FAIL", item.name, passed, kRuns, item.what);
        if (passed == kRuns) ++matched;
    }
    ok = ok && matched == total;
    std::printf("%s: %d of %d cases matched their expected results in all %d runs\n",
                ok ? "PASS" : "FAIL", matched, total, kRuns);
    return ok ? 0 : 1;
}
