#pragma once
#include <cstdint>

// The light-and-buzzer response shared by the recognition trial program, the
// laptop-interface program and the night-controlled detector: the waveform and
// the outputs it drives. Permission, triggering and rearming belong to each
// application's controller. The MVP program in firmware/motion-size keeps its
// own copy of the same waveform, pins and tone.
namespace deterrent {
constexpr int64_t durationUs = 5500000;
struct Outputs { bool white = false; bool tone = false; };
inline Outputs pattern(int64_t elapsedUs) {
    if (elapsedUs < 0 || elapsedUs >= durationUs) return {};
    return {(elapsedUs / 100000) % 2 == 0,
            elapsedUs % 2000000 < 1500000};
}
// Outputs (WIRING.md): the RGB module's R, G and B pins, all on for white,
// and the passive buzzer driven by PWM at a 2 kHz tone, half duty.
constexpr int whitePins[3] = {13, 36, 35};
constexpr int buzzerPin = 10;
constexpr uint32_t toneHz = 2000;
constexpr uint32_t toneResolutionBits = 10;
constexpr uint32_t toneDuty = 1u << (toneResolutionBits - 1);
}
