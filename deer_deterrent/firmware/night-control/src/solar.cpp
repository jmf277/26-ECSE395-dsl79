#include "solar.hpp"
#include <cmath>

namespace solar {
namespace {
constexpr double pi=3.14159265358979323846;
double rad(double d) { return d*pi/180.0; }
double deg(double r) { return r*180.0/pi; }
double normalize(double d) { return d-360.0*std::floor(d/360.0); }
struct Sun { double equationMinutes, declination; };
// NOAA/Meeus Julian-century equations. Apparent upper-limb horizon: 90.833 deg.
// Reference: NOAA Solar Calculation Details and its published calculator equations.
// No terrain/refraction correction beyond the conventional horizon definition.
Sun position(double julianDay) {
    const double t=(julianDay-2451545.0)/36525.0;
    const double l=normalize(280.46646+t*(36000.76983+0.0003032*t));
    const double m=357.52911+t*(35999.05029-0.0001537*t);
    const double e=0.016708634-t*(0.000042037+0.0000001267*t);
    const double c=std::sin(rad(m))*(1.914602-t*(0.004817+0.000014*t))
                  +std::sin(rad(2*m))*(0.019993-0.000101*t)+0.000289*std::sin(rad(3*m));
    const double omega=125.04-1934.136*t;
    const double apparent=l+c-0.00569-0.00478*std::sin(rad(omega));
    const double obliq=23.0+(26.0+(21.448-t*(46.815+t*(0.00059-0.001813*t)))/60.0)/60.0
                       +0.00256*std::cos(rad(omega));
    const double decl=deg(std::asin(std::sin(rad(obliq))*std::sin(rad(apparent))));
    const double y=std::pow(std::tan(rad(obliq/2)),2);
    const double eq=y*std::sin(rad(2*l))-2*e*std::sin(rad(m))
                   +4*e*y*std::sin(rad(m))*std::cos(rad(2*l))
                   -0.5*y*y*std::sin(rad(4*l))-1.25*e*e*std::sin(rad(2*m));
    return {4*deg(eq),decl};
}
bool event(double jd,double lat,double lon,bool rise,double& minutes) {
    minutes=720-4*lon;
    for(int iteration=0;iteration<4;++iteration) {
        const auto sun=position(jd+minutes/1440.0);
        const double cosine=std::cos(rad(90.833))/(std::cos(rad(lat))*std::cos(rad(sun.declination)))
                            -std::tan(rad(lat))*std::tan(rad(sun.declination));
        if(!std::isfinite(cosine)||cosine < -1.0 || cosine > 1.0) return false;
        const double hourAngle=deg(std::acos(cosine));
        minutes=720-4*(lon+(rise?hourAngle:-hourAngle))-sun.equationMinutes;
    }
    return std::isfinite(minutes);
}
}
int64_t dayStart(int64_t utcSeconds) {
    const int64_t remainder=utcSeconds%86400;
    return utcSeconds-remainder-(remainder<0?86400:0);
}
Events events(int64_t day,double latitude,double longitude) {
    if(!std::isfinite(latitude)||!std::isfinite(longitude)||latitude<=-90||latitude>=90||
       longitude < -180||longitude > 180) return {};
    day=dayStart(day);
    const double jd=2440587.5+double(day)/86400.0;
    double rise=0,set=0;
    if(!event(jd,latitude,longitude,true,rise)||!event(jd,latitude,longitude,false,set)) return {};
    return {true,day+int64_t(std::llround(rise*60)),day+int64_t(std::llround(set*60))};
}
Schedule schedule(int64_t utc,double lat,double lon,unsigned after,unsigned before) {
    Schedule result{};
    if(after>120||before>120) return result;
    const auto day=dayStart(utc);
    for(int i=0;i<5;++i) {
        auto sunset=events(day+(i-2)*86400,lat,lon);
        auto sunrise=events(day+(i-1)*86400,lat,lon);
        if(sunset.valid&&sunrise.valid) {
            const int64_t start=sunset.sunset+int64_t(after)*60;
            const int64_t end=sunrise.sunrise-int64_t(before)*60;
            if(start<end) result[i]={start,end};
        }
    }
    return result;
}
}
