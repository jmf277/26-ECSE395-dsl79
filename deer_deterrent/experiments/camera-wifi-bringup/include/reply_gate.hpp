#pragma once
#include <cstdint>
#include "../../../shared/alarm_pattern.hpp"

namespace laptop_trial {
enum class Reply { Accepted, Duplicate, Stale, Unknown, Expired, Conflict };
struct Decision { Reply reply; bool startAlarm = false; };
struct Photo { uint32_t id=0, capturedMs=0; int verdict=-1; };
// Called under the application's mutex. Timing uses unsigned elapsed arithmetic.
class ReplyGate {
public:
    static constexpr uint32_t replyLimitMs=60000;
    bool armed=false, open=false, hasDeer=false;
    uint32_t burst=0;
    void cancel() { armed=false;open=false;alarm_=false; }
    bool arm() { if(alarm_)return false;open=false;armed=true;return true; }
    bool begin(uint32_t id) {
        if(alarm_||!id)return false;
        burst=id;open=true;hasDeer=false;count_=0;
        for(auto& p:photos_)p={};
        return true;
    }
    bool retain(uint32_t id,uint32_t now) {
        if(!open||!id||count_>=10)return false;
        for(unsigned i=0;i<count_;++i)if(photos_[i].id==id)return false;
        photos_[count_++]={id,now,-1};return true;
    }
    Decision submit(uint32_t burstId,uint32_t photoId,bool deer,uint32_t now) {
        if(!open||burstId!=burst)return {Reply::Stale};
        for(unsigned i=0;i<count_;++i) {
            auto& p=photos_[i];if(p.id!=photoId)continue;
            if(p.verdict>=0)return {p.verdict==int(deer)?Reply::Duplicate:Reply::Conflict};
            if(uint32_t(now-p.capturedMs)>replyLimitMs)return {Reply::Expired};
            p.verdict=int(deer);
            const bool start=deer&&!hasDeer&&armed;
            if(deer)hasDeer=true;
            if(start){alarm_=true;alarmAt_=now;}
            return {Reply::Accepted,start};
        }
        return {Reply::Unknown};
    }
    deterrent::Outputs tick(uint32_t now) {
        if(alarm_&&uint32_t(now-alarmAt_)>=5500)alarm_=false;
        return alarm_?deterrent::pattern(int64_t(uint32_t(now-alarmAt_))*1000):deterrent::Outputs{};
    }
    bool active() const { return alarm_; }
private:
    Photo photos_[10];unsigned count_=0;
    bool alarm_=false;uint32_t alarmAt_=0;
};
}
