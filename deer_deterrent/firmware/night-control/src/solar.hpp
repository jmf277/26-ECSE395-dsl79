#pragma once
#include <array>
#include <cstdint>

namespace solar {
// Longitude is east-positive. Times are Unix UTC seconds, not local civil time.
struct Events { bool valid=false; int64_t sunrise=0, sunset=0; };
struct Window { int64_t start=0, end=0; };
using Schedule=std::array<Window,5>;
int64_t dayStart(int64_t utcSeconds);
Events events(int64_t utcDayStart, double latitude, double longitude);
Schedule schedule(int64_t utcSeconds, double latitude, double longitude,
                  unsigned afterSunsetMinutes, unsigned beforeSunriseMinutes);
}
