#include "control_policy.hpp"
#include "control_config.hpp"
#include <cassert>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <limits>
#include <string>
using namespace night_control;
namespace {
unsigned checks=0;
void check(bool result){++checks;if(!result){std::fprintf(stderr,"FAILED check %u\n",checks);std::abort();}}
constexpr int64_t second=1000000;
// Synthetic equatorial fixture, explicitly not a deployment location.
constexpr int64_t day=1710892800; // 2024-03-20 UTC
const Configuration site{true,0,0,0,0};
solar::Window window(){return solar::schedule(day,0,0,0,0)[2];}
void ready(Core& core,int64_t utc,int64_t mono=1000){
    core.configure(site,false,mono);
    check(core.synchronize(utc*second,mono));
    core.calibrationComplete(mono);
    check(core.tick(mono).state==State::Armed);
}
void policyTests(){
    Core fresh;check(fresh.tick(0).state==State::Unconfigured);
    check(!fresh.ticket(Capture::Monitoring,0).generation);
    check(!fresh.requestCalibration(0));
    fresh.configure(site,true,0);check(fresh.tick(0).state==State::Paused);
    check(!fresh.requestCalibration(0));
    const auto w=window();check(w.start<w.end);
    check(fresh.synchronize((w.start+60)*second,1000));
    fresh.setPaused(false,1000);check(fresh.tick(1000).state==State::NeedsBaseline);
    auto calibration=fresh.ticket(Capture::Calibration,1000);check(calibration.generation!=0);
    check(!fresh.startAlarm(calibration,1000));check(fresh.captureLight(calibration,true,1000));
    check(fresh.tick(1000).outputs.white&&!fresh.tick(1000).outputs.tone);
    fresh.setPaused(true,1010);check(!fresh.tick(1010).outputs.white);
    check(!fresh.ticketValid(calibration,1010));check(!fresh.captureLight(calibration,true,1010));
    fresh.cancel(1020);check(fresh.tick(1020).state==State::Paused);
    fresh.setPaused(false,1030);check(!fresh.ticketValid(calibration,1030));
    fresh.fault(1040);fresh.setPaused(false,1050);check(fresh.tick(1050).state==State::Fault);
    check(!fresh.requestCalibration(1050));check(!fresh.ticket(Capture::Calibration,1050).generation);

    Core setup;setup.configure(site,false,0);
    check(setup.tick(0).state==State::InvalidTime);check(setup.requestCalibration(0));
    auto t=setup.ticket(Capture::Calibration,0);check(setup.captureLight(t,true,0));
    check(!setup.startAlarm(t,0));check(!setup.ticket(Capture::Monitoring,0).generation);
    setup.calibrationComplete(0);check(setup.tick(0).state==State::InvalidTime);
    check(!setup.tick(0).outputs.white);check(setup.tick(0).baselineReady);
    setup.configure(site,false,10);check(!setup.tick(10).baselineReady&&!setup.tick(10).clockValid);

    Core edge;ready(edge,w.start-1+86400); // following night
    edge.configure(site,false,0);check(edge.synchronize((w.start-1)*second,0));edge.calibrationComplete(0);
    check(edge.tick(0).state==State::Daylight);check(!edge.ticket(Capture::Monitoring,0).generation);
    check(edge.tick(second).state==State::Armed);auto atStart=edge.ticket(Capture::Monitoring,second);
    const auto untilEnd=(w.end-(w.start-1))*second;
    check(edge.tick(untilEnd-1).night);check(!edge.tick(untilEnd).night);
    check(!edge.ticketValid(atStart,untilEnd));check(!edge.tick(untilEnd).outputs.white);
}
void alarmTests(){
    const auto w=window();Core c;ready(c,w.start+60);
    auto t=c.ticket(Capture::Monitoring,1000);check(c.startAlarm(t,1000));
    check(!c.startAlarm(t,1001));
    for(int64_t elapsed=0;elapsed<alarmDurationUs;elapsed+=10000){
        auto s=c.tick(1000+elapsed);
        check(s.outputs.tone==(elapsed%2000000<1500000));
        check(s.outputs.white==((elapsed/100000)%2==0));
    }
    check(!c.tick(1000+alarmDurationUs).alarmActive);
    check(!c.tick(1000+alarmDurationUs).outputs.white&&!c.tick(1000+alarmDurationUs).outputs.tone);
    check(!c.startAlarm(t,1001+alarmDurationUs));
    t=c.ticket(Capture::Monitoring,6000000);check(c.startAlarm(t,6000000));
    c.setPaused(true,6010000);check(!c.tick(6010000).outputs.tone&&!c.tick(6010000).outputs.white);
    c.setPaused(false,6020000);check(!c.startAlarm(t,6020000));
    t=c.ticket(Capture::Monitoring,6020000);check(c.captureLight(t,true,6020000));
    c.invalidateClock(6020100);check(!c.tick(6020100).outputs.white&&!c.ticketValid(t,6020100));
    Core sunrise;ready(sunrise,w.end-1);t=sunrise.ticket(Capture::Monitoring,1000);
    check(sunrise.startAlarm(t,1000));check(!sunrise.tick(1000+second).alarmActive);
}
void clockTests(){
    auto w=window();Core c;ready(c,w.start+60);
    auto t=c.ticket(Capture::Monitoring,1000);
    check(c.synchronize((w.start+61)*second,1000+second));check(c.ticketValid(t,1000+second));
    check(c.synchronize((w.start+120)*second,1000+2*second));check(!c.ticketValid(t,1000+2*second));
    check(c.tick(1000+2*second+maxClockAgeUs-1).clockValid);
    check(!c.tick(1000+2*second+maxClockAgeUs).clockValid);
    check(!c.synchronize(0,0));check(!c.synchronize(4102444800LL*second,0));
    check(!c.synchronize((w.start+60)*second,-1));
    check(c.synchronize((w.start+60)*second,1000));check(!c.tick(999).clockValid);
    Core reboot;reboot.configure(site,false,0);check(!reboot.tick(0).clockValid);
    Configuration polar{true,89,0,0,0};reboot.configure(polar,false,0);
    check(reboot.synchronize(1718928000LL*second,0));check(reboot.tick(0).state==State::SolarUnavailable);
    check(!reboot.tick(0).captureAllowed);
    auto invalid=site;invalid.latitude=std::numeric_limits<double>::quiet_NaN();check(!validConfiguration(invalid));
    invalid=site;invalid.longitude=181;check(!validConfiguration(invalid));
    invalid=site;invalid.afterSunsetMinutes=121;check(!validConfiguration(invalid));
    Core margins;auto cfg=site;cfg.afterSunsetMinutes=10;cfg.beforeSunriseMinutes=20;
    margins.configure(cfg,false,0);margins.synchronize((w.start+599)*second,0);margins.calibrationComplete(0);
    check(!margins.tick(0).night);check(margins.tick(second).night);
    check(!margins.tick((w.end-w.start-599-1200)*second).night);
}
void parserTests(){
    StoredConfiguration stored;std::strcpy(stored.accessPassword,"synthetic-test-secret-only");
    check(validStoredConfiguration(stored));
    const char* error=nullptr;StoredConfiguration result;
    const std::string json=R"({"latitude":0,"longitude":0,"after_sunset_minutes":10,"before_sunrise_minutes":20,"wifi_ssid":"Synthetic test AP","wifi_password":"synthetic test password"})";
    check(parseConfiguration(json.data(),json.size(),stored,result,error));check(result.paused==1&&result.configured==1);
    check(result.latitude==0&&result.longitude==0);check(result.afterSunsetMinutes==10);
    check(std::strcmp(result.accessPassword,stored.accessPassword)==0);
    stored=result;stored.paused=0;
    auto noPassword=json.substr(0,json.find(",\"wifi_password\""))+"}";
    check(parseConfiguration(noPassword.data(),noPassword.size(),stored,result,error));
    check(result.paused==1&&std::strcmp(result.wifiPassword,stored.wifiPassword)==0);
    auto bad=[&](std::string value){check(!parseConfiguration(value.data(),value.size(),stored,result,error));};
    bad("[]");bad("{}");bad(json+"garbage");bad(json.substr(0,json.size()-1)+",\"latitude\":1}");
    bad("{\"latitude\":"+std::string(400,'[')+"0"+std::string(400,']')+"}");
    bad("{\"latitude\":{\"nested\":0}}");
    bad(json.substr(0,json.size()-1)+",\"unknown\":1}");
    for(auto replacement:{"NaN","1e999","90","-90","\"0\"","null"}){
        auto value=json;value.replace(value.find(":0")+1,1,replacement);bad(value);
    }
    for(auto replacement:{"-1","121","1.5","true"}){
        auto value=json;value.replace(value.find(":10")+1,2,replacement);bad(value);
    }
    auto different=noPassword;different.replace(different.find("Synthetic test AP"),17,"Another AP");bad(different);
    auto nul=json;nul.replace(nul.find("Synthetic"),9,"\\u0000");bad(nul);
    auto raw=json;raw[10]=0;bad(raw);bad(std::string(1025,' '));
    check(emptyObject("{}",2));check(emptyObject("{ } \n",5));check(!emptyObject("{\"a\":1}",7));
    char exact[2]={'{','}'};check(emptyObject(exact,sizeof(exact))); // no caller terminator
    stored.version=2;check(!validStoredConfiguration(stored));stored.version=1;
    stored.paused=2;check(!validStoredConfiguration(stored));stored.paused=1;
    std::memset(stored.accessPassword,'x',sizeof(stored.accessPassword));check(!validStoredConfiguration(stored));
}
}
int main(){policyTests();alarmTests();clockTests();parserTests();std::printf("PASS %u policy/output/config checks\n",checks);}
