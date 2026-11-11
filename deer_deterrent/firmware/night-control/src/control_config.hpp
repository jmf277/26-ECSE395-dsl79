#pragma once
#include "control_policy.hpp"
#include <cstddef>
#include <cstdint>

namespace night_control {
// One versioned NVS blob is committed atomically. Never store trusted time.
struct StoredConfiguration {
    uint32_t magic=0x44454552, version=1;
    double latitude=0, longitude=0;
    uint16_t afterSunsetMinutes=0, beforeSunriseMinutes=0;
    uint8_t configured=0, paused=1;
    char ssid[33]={}, wifiPassword[64]={}, accessPassword[64]={};
};
Configuration policyConfiguration(const StoredConfiguration&);
bool validStoredConfiguration(const StoredConfiguration&);
// Strict bounded JSON; unknown/duplicate keys and embedded NULs are rejected.
// An omitted Wi-Fi password retains it only when the SSID is unchanged.
bool parseConfiguration(const char* json,size_t length,const StoredConfiguration& previous,
                        StoredConfiguration& result,const char*& error);
bool emptyObject(const char* json,size_t length);
}
