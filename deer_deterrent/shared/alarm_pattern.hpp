#pragma once
#include <cstdint>

// Electrical waveform shared by the practical controller and recognition trials.
// Permission, triggering and rearming belong to each application's controller.
namespace deterrent {
constexpr int64_t durationUs = 5500000;
struct Outputs { bool white = false; bool tone = false; };
inline Outputs pattern(int64_t elapsedUs) {
    if (elapsedUs < 0 || elapsedUs >= durationUs) return {};
    return {(elapsedUs / 100000) % 2 == 0,
            elapsedUs % 2000000 < 1500000};
}
}
