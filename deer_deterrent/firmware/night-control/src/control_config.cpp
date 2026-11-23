#include "control_config.hpp"
#include "cJSON.h"
#include <cmath>
#include <cstring>

namespace night_control {
namespace {
bool printable(const char* s,size_t capacity,size_t minimum) {
    const auto n=strnlen(s,capacity);
    if(n<minimum||n==capacity) return false;
    for(size_t i=0;i<n;++i) if(static_cast<unsigned char>(s[i])<32||static_cast<unsigned char>(s[i])>126) return false;
    return true;
}
cJSON* object(const char* json,size_t length) {
    if(!json||length<2||length>1024||std::memchr(json,0,length)) return nullptr;
    // cJSON represents strings as C strings: explicitly forbid escaped NUL.
    for(size_t i=0;i+6<=length;++i) if(std::memcmp(json+i,"\\u0000",6)==0) return nullptr;
    // The API is a flat object of scalar fields. Reject nesting before invoking
    // a recursive parser, bounding stack use even for hostile authenticated input.
    bool quoted=false,escaped=false;int depth=0;
    for(size_t i=0;i<length;++i) {
        const char ch=json[i];
        if(quoted) {
            if(escaped) escaped=false;
            else if(ch=='\\') escaped=true;
            else if(ch=='"') quoted=false;
        } else if(ch=='"') quoted=true;
        else if(ch=='{') {if(depth!=0)return nullptr;depth=1;}
        else if(ch=='}') {if(depth!=1)return nullptr;depth=0;}
        else if(ch=='['||ch==']') return nullptr;
    }
    char bounded[1025];std::memcpy(bounded,json,length);bounded[length]=0;
    const char* end=nullptr;
    auto root=cJSON_ParseWithLengthOpts(bounded,length+1,&end,true);
    if(!root||!cJSON_IsObject(root)||end!=bounded+length) {cJSON_Delete(root);return nullptr;}
    return root;
}
}
Configuration policyConfiguration(const StoredConfiguration& c) {
    return {c.configured==1,c.latitude,c.longitude,c.afterSunsetMinutes,c.beforeSunriseMinutes};
}
bool validStoredConfiguration(const StoredConfiguration& c) {
    if(c.magic!=0x44454552||c.version!=1||c.paused>1||c.configured>1||
       !printable(c.accessPassword,sizeof(c.accessPassword),16)) return false;
    if(!c.configured) return c.paused==1&&c.latitude==0&&c.longitude==0&&
        c.afterSunsetMinutes==0&&c.beforeSunriseMinutes==0&&c.ssid[0]==0&&c.wifiPassword[0]==0;
    return validConfiguration(policyConfiguration(c))&&printable(c.ssid,sizeof(c.ssid),1)&&
        printable(c.wifiPassword,sizeof(c.wifiPassword),8);
}
bool parseConfiguration(const char* json,size_t length,const StoredConfiguration& previous,
                        StoredConfiguration& result,const char*& error) {
    error="Invalid configuration JSON";
    auto root=object(json,length);
    if(!root) return false;
    const char* keys[]={"latitude","longitude","after_sunset_minutes","before_sunrise_minutes","wifi_ssid","wifi_password"};
    unsigned seen=0;bool valid=true;
    for(auto item=root->child;item;item=item->next) {
        int index=-1;
        for(int i=0;i<6;++i) if(item->string&&std::strcmp(item->string,keys[i])==0) index=i;
        if(index<0||(seen&(1U<<index))) {valid=false;break;}
        seen|=1U<<index;
    }
    if(!valid||(seen&31)!=31) {cJSON_Delete(root);error="Missing, duplicate, or unknown field";return false;}
    auto lat=cJSON_GetObjectItemCaseSensitive(root,keys[0]);
    auto lon=cJSON_GetObjectItemCaseSensitive(root,keys[1]);
    auto after=cJSON_GetObjectItemCaseSensitive(root,keys[2]);
    auto before=cJSON_GetObjectItemCaseSensitive(root,keys[3]);
    auto ssid=cJSON_GetObjectItemCaseSensitive(root,keys[4]);
    auto pass=cJSON_GetObjectItemCaseSensitive(root,keys[5]);
    valid=cJSON_IsNumber(lat)&&cJSON_IsNumber(lon)&&cJSON_IsNumber(after)&&cJSON_IsNumber(before)&&
        cJSON_IsString(ssid)&&(!pass||cJSON_IsString(pass));
    if(valid) valid=std::isfinite(after->valuedouble)&&std::isfinite(before->valuedouble)&&
        after->valuedouble>=0&&after->valuedouble<=120&&std::floor(after->valuedouble)==after->valuedouble&&
        before->valuedouble>=0&&before->valuedouble<=120&&std::floor(before->valuedouble)==before->valuedouble;
    if(valid) valid=printable(ssid->valuestring,33,1)&&(pass?printable(pass->valuestring,64,8):
        previous.configured&&std::strcmp(previous.ssid,ssid->valuestring)==0);
    if(valid) {
        result=previous;result.configured=1;result.paused=1;
        result.latitude=lat->valuedouble;result.longitude=lon->valuedouble;
        result.afterSunsetMinutes=static_cast<uint16_t>(after->valuedouble);
        result.beforeSunriseMinutes=static_cast<uint16_t>(before->valuedouble);
        std::memset(result.ssid,0,sizeof(result.ssid));std::strcpy(result.ssid,ssid->valuestring);
        if(pass) {std::memset(result.wifiPassword,0,sizeof(result.wifiPassword));std::strcpy(result.wifiPassword,pass->valuestring);}
        valid=validStoredConfiguration(result);
    }
    cJSON_Delete(root);
    if(!valid) {error="Check coordinates, integer margins 0–120, SSID 1–32, and WPA2 password 8–63 printable ASCII characters";return false;}
    error=nullptr;return true;
}
bool emptyObject(const char* json,size_t length) {
    auto root=object(json,length);const bool valid=root&&!root->child;cJSON_Delete(root);return valid;
}
}
