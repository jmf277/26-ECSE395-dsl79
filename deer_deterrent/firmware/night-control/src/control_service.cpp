#include "control_service.hpp"
#include "control_config.hpp"
#include "control_page.hpp"
#include <atomic>
#include <cstdio>
#include <cstring>
#include <string>
#include "cJSON.h"
#include "bootloader_random.h"
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "esp_event.h"
#include "esp_http_server.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_netif_sntp.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "mbedtls/base64.h"
#include "nvs.h"
#include "nvs_flash.h"

namespace control_service {
namespace {
using namespace night_control;
Core core;
StoredConfiguration stored;
SemaphoreHandle_t coreMutex=nullptr,settingsMutex=nullptr;
esp_timer_handle_t outputTimer=nullptr;
httpd_handle_t server=nullptr;
esp_netif_t* station=nullptr;
std::atomic<bool> networkConnected{false},reconnect{false},networkChange{false},syncPending{false},syncLogged{false};
std::atomic<bool> stationConfigured{false};
std::atomic<bool> ended{false},rearmPending{false};
std::atomic<uint32_t> pauseEpoch{0};
std::atomic<unsigned> pauseRequests{0};
bool outputsReady=false,previousAlarm=false,haveOutputs=false;
Outputs lastOutputs;
char setupSsid[32]={},csrf[33]={};
class Lock {
    SemaphoreHandle_t mutex_;
public:
    explicit Lock(SemaphoreHandle_t m):mutex_(m){xSemaphoreTake(mutex_,portMAX_DELAY);}
    ~Lock(){xSemaphoreGive(mutex_);}
};
int64_t now(){return esp_timer_get_time();}
void apply(const Snapshot& s) {
    if(previousAlarm&&!s.alarmActive) ended=true;
    previousAlarm=s.alarmActive;
    if(!outputsReady) return;
    if(!haveOutputs||lastOutputs.white!=s.outputs.white) {
        for(int pin:deterrent::whitePins) gpio_set_level(static_cast<gpio_num_t>(pin),s.outputs.white);
    }
    if(!haveOutputs||lastOutputs.tone!=s.outputs.tone) {
        ledc_set_duty(LEDC_LOW_SPEED_MODE,LEDC_CHANNEL_2,s.outputs.tone?deterrent::toneDuty:0);
        ledc_update_duty(LEDC_LOW_SPEED_MODE,LEDC_CHANNEL_2);
    }
    lastOutputs=s.outputs;haveOutputs=true;
}
void timer(void*) {Lock lock(coreMutex);apply(core.tick(now()));}
void log(const char* event) {
    const auto s=snapshot();
    std::printf("CONTROL {\"event\":\"%s\",\"state\":\"%s\",\"paused\":%s,\"clock_valid\":%s,\"clock_age_s\":%lld,\"configured\":%s,\"generation\":%llu,\"baseline_ready\":%s,\"night\":%s}\n",
        event,stateName(s.state),s.paused?"true":"false",s.clockValid?"true":"false",
        static_cast<long long>(s.clockAgeUs<0?-1:s.clockAgeUs/1000000),s.configured?"true":"false",
        static_cast<unsigned long long>(s.generation),s.baselineReady?"true":"false",s.night?"true":"false");
}
void randomHex(char* out,size_t bytes) {
    constexpr char hex[]="0123456789abcdef";
    uint8_t random[16];esp_fill_random(random,bytes);
    for(size_t i=0;i<bytes;++i) {out[2*i]=hex[random[i]>>4];out[2*i+1]=hex[random[i]&15];}
    out[2*bytes]=0;
}
bool save(const StoredConfiguration& candidate) {
    if(!validStoredConfiguration(candidate)) return false;
    nvs_handle_t handle=0;
    esp_err_t result=nvs_open("deer_control",NVS_READWRITE,&handle);
    if(result==ESP_OK) result=nvs_set_blob(handle,"settings",&candidate,sizeof(candidate));
    if(result==ESP_OK) result=nvs_commit(handle);
    if(handle) nvs_close(handle);
    return result==ESP_OK;
}
bool load() {
    // Do not erase NVS on failure: that could erase a held pause or credentials.
    if(nvs_flash_init()!=ESP_OK) return false;
    nvs_handle_t handle=0;
    auto result=nvs_open("deer_control",NVS_READWRITE,&handle);
    if(result!=ESP_OK) return false;
    size_t length=sizeof(stored);
    result=nvs_get_blob(handle,"settings",&stored,&length);
    nvs_close(handle);
    if(result==ESP_ERR_NVS_NOT_FOUND) {
        stored=StoredConfiguration{};
        // Called before RF, ADC, or camera initialization; disable this entropy
        // source before initializing those peripherals.
        bootloader_random_enable();
        randomHex(stored.accessPassword,16);
        bootloader_random_disable();
        return save(stored);
    }
    return result==ESP_OK&&length==sizeof(stored)&&validStoredConfiguration(stored);
}
void synchronized(struct timeval* time) {
    {Lock lock(coreMutex);core.synchronize(int64_t(time->tv_sec)*1000000+time->tv_usec,now());apply(core.tick(now()));}
    syncLogged=true;
}
void networkEvent(void*,esp_event_base_t base,int32_t id,void*) {
    if(base==WIFI_EVENT&&id==WIFI_EVENT_STA_DISCONNECTED) {networkConnected=false;reconnect=stationConfigured.load();}
    if(base==IP_EVENT&&id==IP_EVENT_STA_GOT_IP) {networkConnected=true;reconnect=false;syncPending=true;}
}
bool connectSettings() {
    StoredConfiguration copy;
    {Lock lock(settingsMutex);copy=stored;}
    stationConfigured=copy.configured==1;
    esp_wifi_disconnect();networkConnected=false;
    if(!copy.configured) {reconnect=false;return true;}
    wifi_config_t config={};
    std::memcpy(config.sta.ssid,copy.ssid,std::strlen(copy.ssid));
    std::memcpy(config.sta.password,copy.wifiPassword,std::strlen(copy.wifiPassword));
    config.sta.threshold.authmode=WIFI_AUTH_WPA2_PSK;
    config.sta.pmf_cfg.capable=true;
    if(esp_wifi_set_config(WIFI_IF_STA,&config)!=ESP_OK) return false;
    reconnect=true;
    // A missing AP is a recoverable WAIT_TIME condition, not a startup fault.
    esp_wifi_connect();
    return true;
}
void worker(void*) {
    uint64_t lastGeneration=0;State lastState=State::Fault;
    int64_t lastRetry=0,lastEvening=0;
    for(;;) {
        if(networkChange.exchange(false)) {
            esp_netif_sntp_deinit();
            esp_sntp_config_t config=ESP_NETIF_SNTP_DEFAULT_CONFIG_MULTIPLE(2,ESP_SNTP_SERVER_LIST("pool.ntp.org","time.cloudflare.com"));
            config.start=false;config.smooth_sync=false;config.sync_cb=synchronized;
            if(esp_netif_sntp_init(&config)!=ESP_OK||!connectSettings()) fatal("network_reconfigure");
            lastEvening=0;
        }
        if(reconnect&&now()-lastRetry>=5000000) {lastRetry=now();esp_wifi_connect();}
        if(syncPending.exchange(false)&&networkConnected) esp_netif_sntp_start();
        if(syncLogged.exchange(false)) log("TIME_SYNC");
        const auto s=snapshot();
        // Refresh shortly before each computed evening boundary, in addition
        // to startup/reconnect and the configured hourly SNTP update interval.
        if(networkConnected&&s.clockValid&&s.nextStart&&s.nextStart!=lastEvening&&
           s.nextStart-s.utcUs/1000000<=300) {lastEvening=s.nextStart;esp_netif_sntp_start();}
        if(s.generation!=lastGeneration||s.state!=lastState) {log("POLICY_CHANGE");lastGeneration=s.generation;lastState=s.state;}
        vTaskDelay(pdMS_TO_TICKS(200));
    }
}
bool header(httpd_req_t* request,const char* name,const char* expected) {
    char value[160];const auto length=httpd_req_get_hdr_value_len(request,name);
    if(length==0||length>=sizeof(value)||length!=std::strlen(expected)) return false;
    if(httpd_req_get_hdr_value_str(request,name,value,sizeof(value))!=ESP_OK) return false;
    unsigned difference=0;for(size_t i=0;i<length;++i) difference|=static_cast<unsigned char>(value[i]^expected[i]);
    return difference==0;
}
void responseHeaders(httpd_req_t* request) {
    httpd_resp_set_hdr(request,"Cache-Control","no-store");
    httpd_resp_set_hdr(request,"X-Content-Type-Options","nosniff");
    httpd_resp_set_hdr(request,"X-Frame-Options","DENY");
    httpd_resp_set_hdr(request,"Referrer-Policy","no-referrer");
}
bool authenticated(httpd_req_t* request) {
    char clear[72],encoded[112],expected[120];size_t length=0;
    {Lock lock(settingsMutex);std::snprintf(clear,sizeof(clear),"admin:%s",stored.accessPassword);}
    const auto result=mbedtls_base64_encode(reinterpret_cast<unsigned char*>(encoded),sizeof(encoded)-1,&length,
        reinterpret_cast<const unsigned char*>(clear),std::strlen(clear));
    std::memset(clear,0,sizeof(clear));
    if(result!=0) return false;
    encoded[length]=0;std::snprintf(expected,sizeof(expected),"Basic %s",encoded);
    responseHeaders(request);
    if(header(request,"Authorization",expected)) return true;
    httpd_resp_set_hdr(request,"WWW-Authenticate","Basic realm=\"Deer control\"");
    httpd_resp_send_err(request,HTTPD_401_UNAUTHORIZED,"Authentication required");
    return false;
}
esp_err_t message(httpd_req_t* request,bool okay,const char* value) {
    httpd_resp_set_type(request,"application/json");
    if(!okay) httpd_resp_set_status(request,"400 Bad Request");
    auto json=cJSON_CreateObject();
    if(!json) return httpd_resp_send_err(request,HTTPD_500_INTERNAL_SERVER_ERROR,"Memory unavailable");
    cJSON_AddBoolToObject(json,"ok",okay);cJSON_AddStringToObject(json,"message",value);
    char* body=cJSON_PrintUnformatted(json);cJSON_Delete(json);
    if(!body) return httpd_resp_send_err(request,HTTPD_500_INTERNAL_SERVER_ERROR,"Memory unavailable");
    auto result=httpd_resp_send(request,body,HTTPD_RESP_USE_STRLEN);cJSON_free(body);return result;
}
esp_err_t page(httpd_req_t* request) {
    if(!authenticated(request)) return ESP_OK;
    std::string html=controlPage;
    html.replace(html.find("%NONCE%"),7,csrf);
    char csp[220];std::snprintf(csp,sizeof(csp),"default-src 'self'; script-src 'nonce-%s'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",csrf);
    httpd_resp_set_hdr(request,"Content-Security-Policy",csp);
    httpd_resp_set_type(request,"text/html; charset=utf-8");
    return httpd_resp_send(request,html.data(),html.size());
}
esp_err_t status(httpd_req_t* request) {
    if(!authenticated(request)) return ESP_OK;
    const auto s=snapshot();StoredConfiguration copy;
    {Lock lock(settingsMutex);copy=stored;}
    auto j=cJSON_CreateObject();
    if(!j) return httpd_resp_send_err(request,HTTPD_500_INTERNAL_SERVER_ERROR,"Memory unavailable");
    cJSON_AddStringToObject(j,"state",stateName(s.state));cJSON_AddBoolToObject(j,"paused",s.paused);
    cJSON_AddBoolToObject(j,"configured",s.configured);cJSON_AddBoolToObject(j,"clock_valid",s.clockValid);
    cJSON_AddNumberToObject(j,"clock_age_s",s.clockAgeUs<0?-1:s.clockAgeUs/1000000);
    cJSON_AddNumberToObject(j,"utc",s.utcUs/1000000);cJSON_AddNumberToObject(j,"window_start",s.windowStart);
    cJSON_AddNumberToObject(j,"window_end",s.windowEnd);cJSON_AddNumberToObject(j,"next_start",s.nextStart);
    cJSON_AddBoolToObject(j,"baseline_ready",s.baselineReady);cJSON_AddBoolToObject(j,"network_connected",networkConnected);
    cJSON_AddStringToObject(j,"setup_ssid",setupSsid);cJSON_AddStringToObject(j,"wifi_ssid",copy.ssid);
    cJSON_AddNumberToObject(j,"latitude",copy.latitude);cJSON_AddNumberToObject(j,"longitude",copy.longitude);
    cJSON_AddNumberToObject(j,"after_sunset_minutes",copy.afterSunsetMinutes);
    cJSON_AddNumberToObject(j,"before_sunrise_minutes",copy.beforeSunriseMinutes);cJSON_AddStringToObject(j,"csrf",csrf);
    char* body=cJSON_PrintUnformatted(j);cJSON_Delete(j);
    if(!body) return httpd_resp_send_err(request,HTTPD_500_INTERNAL_SERVER_ERROR,"Memory unavailable");
    httpd_resp_set_type(request,"application/json");
    auto result=httpd_resp_send(request,body,HTTPD_RESP_USE_STRLEN);cJSON_free(body);return result;
}
bool configure(const char* body,size_t length,const char*& error) {
    // Parse first; malformed requests never alter the existing configuration.
    Lock settings(settingsMutex);StoredConfiguration next;
    if(!parseConfiguration(body,length,stored,next,error)) return false;
    ++pauseEpoch;
    {Lock lock(coreMutex);core.setPaused(true,now());apply(core.tick(now()));}
    if(!save(next)) {fatal("configuration_save");error="Storage failure; outputs inhibited. Settings were not confirmed saved.";return false;}
    stored=next;
    {Lock lock(coreMutex);core.configure(policyConfiguration(stored),true,now());apply(core.tick(now()));}
    networkChange=true;log("CONFIG_SAVED");return true;
}
esp_err_t mutation(httpd_req_t* request) {
    if(!authenticated(request)) return ESP_OK;
    if(!header(request,"Content-Type","application/json")||!header(request,"X-Deer-Control",csrf)||
       httpd_req_get_url_query_len(request)!=0) return httpd_resp_send_err(request,HTTPD_403_FORBIDDEN,"Use the authenticated control page");
    if(request->content_len<2||request->content_len>1024) return message(request,false,"Invalid request length");
    char body[1025];size_t received=0;
    while(received<request->content_len) {
        int count=httpd_req_recv(request,body+received,request->content_len-received);
        if(count<=0) return httpd_resp_send_err(request,HTTPD_408_REQ_TIMEOUT,"Incomplete request");
        received+=count;
    }
    body[received]=0;
    if(std::strcmp(request->uri,"/api/config")==0) {
        const char* error=nullptr;const bool okay=configure(body,received,error);
        std::memset(body,0,sizeof(body));
        return message(request,okay,okay?"Settings saved. Paused; resume when the view is empty.":error);
    }
    if(!emptyObject(body,received)) return message(request,false,"Expected an empty JSON object");
    if(std::strcmp(request->uri,"/api/pause")==0) {
        const bool okay=pause();return message(request,okay,okay?"Paused and saved.":"Stopped for this boot. Pause could not be saved; check storage before a power cycle.");
    }
    if(std::strcmp(request->uri,"/api/resume")==0) {
        const bool okay=resume();return message(request,okay,okay?"Enabled. Waiting for valid time, night, and reference as needed.":"Cannot resume: configure the site or resolve the displayed fault.");
    }
    if(std::strcmp(request->uri,"/api/calibrate")==0) {
        const bool okay=calibrate();return message(request,okay,okay?"Clear the view. Illuminated reference capture starts after 3 seconds.":"Calibration is blocked while paused, unconfigured, or faulted.");
    }
    return httpd_resp_send_err(request,HTTPD_404_NOT_FOUND,"Unknown control");
}
bool startWeb() {
    httpd_config_t config=HTTPD_DEFAULT_CONFIG();config.stack_size=8192;config.max_uri_handlers=6;
    config.lru_purge_enable=true;config.recv_wait_timeout=5;config.send_wait_timeout=5;
    if(httpd_start(&server,&config)!=ESP_OK) return false;
    for(auto path:{"/","/api/status","/api/config","/api/pause","/api/resume","/api/calibrate"}) {
        httpd_uri_t route={};route.uri=path;route.method=std::strcmp(path,"/")==0||std::strcmp(path,"/api/status")==0?HTTP_GET:HTTP_POST;
        route.handler=route.method==HTTP_POST?mutation:(std::strcmp(path,"/")==0?page:status);
        if(httpd_register_uri_handler(server,&route)!=ESP_OK) return false;
    }
    return true;
}
}
bool initialize() {
    coreMutex=xSemaphoreCreateMutex();settingsMutex=xSemaphoreCreateMutex();
    if(!coreMutex||!settingsMutex) return false;
    ledc_timer_config_t timerConfig={};timerConfig.speed_mode=LEDC_LOW_SPEED_MODE;
    timerConfig.duty_resolution=static_cast<ledc_timer_bit_t>(deterrent::toneResolutionBits);timerConfig.timer_num=LEDC_TIMER_1;
    timerConfig.freq_hz=deterrent::toneHz;timerConfig.clk_cfg=LEDC_AUTO_CLK;
    if(ledc_timer_config(&timerConfig)!=ESP_OK) return false;
    ledc_channel_config_t channel={};channel.gpio_num=deterrent::buzzerPin;channel.speed_mode=LEDC_LOW_SPEED_MODE;
    channel.channel=LEDC_CHANNEL_2;channel.timer_sel=LEDC_TIMER_1;channel.duty=0;
    if(ledc_channel_config(&channel)!=ESP_OK) return false;
    outputsReady=true;
    if(!load()) {fatal("storage_read_or_initial_save");return false;}
    {Lock lock(coreMutex);core.configure(policyConfiguration(stored),stored.paused,now());apply(core.tick(now()));}
    esp_timer_create_args_t args={};args.callback=timer;args.name="output_gate";
    if(esp_timer_create(&args,&outputTimer)!=ESP_OK||esp_timer_start_periodic(outputTimer,20000)!=ESP_OK) {fatal("output_timer");return false;}
    if(esp_netif_init()!=ESP_OK||esp_event_loop_create_default()!=ESP_OK) {fatal("network_init");return false;}
    station=esp_netif_create_default_wifi_sta();auto ap=esp_netif_create_default_wifi_ap();
    if(!station||!ap) {fatal("network_interfaces");return false;}
    esp_netif_set_hostname(station,"deer-deterrent");
    wifi_init_config_t wifi=WIFI_INIT_CONFIG_DEFAULT();
    if(esp_wifi_init(&wifi)!=ESP_OK||esp_wifi_set_storage(WIFI_STORAGE_RAM)!=ESP_OK||
       esp_event_handler_register(WIFI_EVENT,WIFI_EVENT_STA_DISCONNECTED,networkEvent,nullptr)!=ESP_OK||
       esp_event_handler_register(IP_EVENT,IP_EVENT_STA_GOT_IP,networkEvent,nullptr)!=ESP_OK) {fatal("wifi_init");return false;}
    uint8_t mac[6];if(esp_read_mac(mac,ESP_MAC_WIFI_SOFTAP)!=ESP_OK) {fatal("mac_read");return false;}
    std::snprintf(setupSsid,sizeof(setupSsid),"DeerSetup-%02X%02X%02X",mac[3],mac[4],mac[5]);
    wifi_config_t access={};std::strcpy(reinterpret_cast<char*>(access.ap.ssid),setupSsid);
    access.ap.ssid_len=std::strlen(setupSsid);access.ap.channel=1;access.ap.max_connection=2;
    access.ap.authmode=WIFI_AUTH_WPA2_PSK;access.ap.pmf_cfg.capable=true;
    std::strcpy(reinterpret_cast<char*>(access.ap.password),stored.accessPassword);
    if(esp_wifi_set_mode(WIFI_MODE_APSTA)!=ESP_OK||esp_wifi_set_config(WIFI_IF_AP,&access)!=ESP_OK||esp_wifi_start()!=ESP_OK) {fatal("wifi_start");return false;}
    // Wi-Fi is now running, so the hardware RNG has its RF entropy source.
    randomHex(csrf,16);
    esp_sntp_config_t ntp=ESP_NETIF_SNTP_DEFAULT_CONFIG_MULTIPLE(2,ESP_SNTP_SERVER_LIST("pool.ntp.org","time.cloudflare.com"));
    ntp.start=false;ntp.smooth_sync=false;ntp.sync_cb=synchronized;
    if(esp_netif_sntp_init(&ntp)!=ESP_OK||!connectSettings()||!startWeb()||
       xTaskCreate(worker,"control_network",4096,nullptr,3,nullptr)!=pdPASS) {fatal("control_service_start");return false;}
    std::printf("CONTROL_ACCESS setup_ssid=%s url=http://192.168.4.1 username=admin password=USB_ACCESS_COMMAND\n",setupSsid);
    log("BOOT");return true;
}
Snapshot snapshot(){Lock lock(coreMutex);auto s=core.tick(now());apply(s);return s;}
Ticket ticket(Capture kind){Lock lock(coreMutex);auto t=core.ticket(kind,now());apply(core.tick(now()));return t;}
bool valid(Ticket t){Lock lock(coreMutex);bool result=core.ticketValid(t,now());apply(core.tick(now()));return result;}
bool captureLight(Ticket t,bool on){Lock lock(coreMutex);bool result=core.captureLight(t,on,now());apply(core.tick(now()));return result;}
bool completeCalibration(Ticket t){Lock lock(coreMutex);if(!core.ticketValid(t,now()))return false;core.calibrationComplete(now());apply(core.tick(now()));return true;}
bool startAlarm(Ticket t){Lock lock(coreMutex);bool result=core.startAlarm(t,now());apply(core.tick(now()));return result;}
bool alarmEnded(){return ended.exchange(false);}
void fatal(const char* reason){if(coreMutex){Lock lock(coreMutex);core.fault(now());apply(core.tick(now()));}std::printf("ERROR control=%s outputs_inhibited=1\n",reason);}
bool pause(){
    ++pauseRequests;
    ++pauseEpoch;
    {Lock lock(coreMutex);core.setPaused(true,now());apply(core.tick(now()));}
    Lock settings(settingsMutex);auto next=stored;next.paused=1;
    if(!save(next)){fatal("pause_save");--pauseRequests;return false;}stored=next;
    {Lock lock(coreMutex);core.setPaused(true,now());apply(core.tick(now()));}
    --pauseRequests;log("PAUSE");return true;
}
bool resume(){
    Lock settings(settingsMutex);const auto epoch=pauseEpoch.load();
    auto s=snapshot();if(pauseRequests.load()||!s.configured||s.state==State::Fault)return false;
    auto next=stored;next.paused=0;
    if(!save(next)){fatal("resume_save");return false;}stored=next;
    {Lock lock(coreMutex);if(pauseRequests.load()||pauseEpoch.load()!=epoch)return false;core.setPaused(false,now());apply(core.tick(now()));}
    log("RESUME");return true;
}
bool calibrate(){bool result;{Lock lock(coreMutex);result=core.requestCalibration(now());apply(core.tick(now()));}if(result)log("CALIBRATION_REQUEST");return result;}
void rearm(){{Lock lock(coreMutex);core.cancel(now());apply(core.tick(now()));}rearmPending=true;log("REARM");}
bool takeRearm(){return rearmPending.exchange(false);}
void printAccess(){Lock settings(settingsMutex);std::printf("ACCESS setup_ssid=%s password=%s username=admin url=http://192.168.4.1\n",setupSsid,stored.accessPassword);}
void printStatus(){log("STATUS");}
}
