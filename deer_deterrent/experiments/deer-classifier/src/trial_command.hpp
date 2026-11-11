#pragma once
#include <cstring>
#include <cstdint>

namespace recognition_trial {
enum class CommandKind { Invalid, Capture, Manual, Pir, Arm, Disarm, Stop, Status, Ambient, White, OutputTest, InjectDeer, RecordFull, RecordJpeg };
struct Command { CommandKind kind = CommandKind::Invalid; unsigned frames = 0; };
inline Command parseCommand(const char* text) {
    if (!std::strcmp(text, "MODE MANUAL")) return {CommandKind::Manual, 0};
    if (!std::strcmp(text, "MODE PIR")) return {CommandKind::Pir, 0};
    if (!std::strcmp(text, "ARM")) return {CommandKind::Arm, 0};
    if (!std::strcmp(text, "DISARM")) return {CommandKind::Disarm, 0};
    if (!std::strcmp(text, "STOP")) return {CommandKind::Stop, 0};
    if (!std::strcmp(text, "STATUS")) return {CommandKind::Status, 0};
    if (!std::strcmp(text, "LIGHT AMBIENT")) return {CommandKind::Ambient, 0};
    if (!std::strcmp(text, "LIGHT WHITE")) return {CommandKind::White, 0};
    if (!std::strcmp(text, "TEST OUTPUT")) return {CommandKind::OutputTest, 0};
    if (!std::strcmp(text, "INJECT DEER")) return {CommandKind::InjectDeer, 0};
    if (!std::strcmp(text, "RECORD FULL")) return {CommandKind::RecordFull, 0};
    if (!std::strcmp(text, "RECORD JPEG")) return {CommandKind::RecordJpeg, 0};
    if (std::strncmp(text, "CAPTURE ", 8)) return {};
    unsigned value = 0;
    const char* p = text + 8;
    if (!*p) return {};
    for (; *p; ++p) {
        if (*p < '0' || *p > '9' || value > 600) return {};
        value = value * 10 + unsigned(*p - '0');
    }
    return value >= 1 && value <= 600 ? Command{CommandKind::Capture, value} : Command{};
}
}
