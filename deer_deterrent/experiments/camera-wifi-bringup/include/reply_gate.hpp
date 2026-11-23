#pragma once
#include <cstdint>
#include "../../../shared/alarm_pattern.hpp"
#include "../../deer-classifier/src/temporal_gate.hpp"

namespace laptop_trial {
enum class Reply { Accepted, Duplicate, Stale, Unknown, Expired, Conflict };
struct Decision { Reply reply; bool startAlarm = false; bool present = false; };
struct Photo { uint32_t id=0, capturedMs=0; int verdict=-1; };
// Called under the application's mutex. Timing uses unsigned elapsed arithmetic.
// Verdicts confirm with the board path's rule: two deer verdicts among the last
// three photos, in capture order. The gate is the trial program's TemporalDeerGate,
// fed 1 for a deer verdict and 0 otherwise at a threshold of 1; a photo left
// without a verdict when a later photo is judged counts as an invalid frame and
// clears the history.
class ReplyGate {
public:
    static constexpr uint32_t replyLimitMs=60000;
    bool armed=false, open=false, hasDeer=false;
    uint32_t burst=0;
    void cancel() { armed=false;open=false;alarm_=false; }
    bool arm() { if(alarm_)return false;open=false;armed=true;return true; }
    bool begin(uint32_t id) {
        if(alarm_||!id)return false;
        burst=id;open=true;hasDeer=false;present_=false;count_=0;judged_=0;gate_.reset();
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
            if(p.verdict>=0)return {p.verdict==int(deer)?Reply::Duplicate:Reply::Conflict,false,present_};
            if(i<judged_)return {Reply::Stale};  // a later photo was judged first
            if(uint32_t(now-p.capturedMs)>replyLimitMs)return {Reply::Expired};
            if(i>judged_)gate_.update(false,0.0f);  // skipped photos clear the history
            p.verdict=int(deer);judged_=i+1;
            present_=gate_.update(true,deer?1.0f:0.0f);
            const bool start=present_&&!hasDeer&&armed;
            if(present_)hasDeer=true;
            if(start){alarm_=true;alarmAt_=now;}
            return {Reply::Accepted,start,present_};
        }
        return {Reply::Unknown};
    }
    deterrent::Outputs tick(uint32_t now) {
        if(alarm_&&uint32_t(now-alarmAt_)>=5500)alarm_=false;
        return alarm_?deterrent::pattern(int64_t(uint32_t(now-alarmAt_))*1000):deterrent::Outputs{};
    }
    bool active() const { return alarm_; }
private:
    Photo photos_[10];unsigned count_=0,judged_=0;
    TemporalDeerGate gate_{1.0f};
    bool present_=false;
    bool alarm_=false;uint32_t alarmAt_=0;
};
}
