#pragma once
#include "control_policy.hpp"

namespace control_service {
// Initializes output gate, persistent settings, WPA2 setup AP, STA, NTP, and HTTP.
// Any initialization/storage error latches the output gate off for this boot.
bool initialize();
night_control::Snapshot snapshot();
night_control::Ticket ticket(night_control::Capture);
bool valid(night_control::Ticket);
bool captureLight(night_control::Ticket,bool on);
bool completeCalibration(night_control::Ticket);
bool startAlarm(night_control::Ticket);
bool alarmEnded();
void fatal(const char* reason);
// These are shared by exact USB commands and authenticated POST handlers.
bool pause();
bool resume();
bool calibrate();
void rearm();
bool takeRearm();
void printAccess(); // Explicit physical-USB request only; never automatic logs.
void printStatus();
}
