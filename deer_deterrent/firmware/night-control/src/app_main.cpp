#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstring>
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "driver/usb_serial_jtag.h"
#include "esp_camera.h"
#include "esp_heap_caps.h"
#include "esp_psram.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "dl_image_jpeg.hpp"
#include "mbedtls/base64.h"
#include "status_display.hpp"
#include "approach_gate.hpp"
#include "control_service.hpp"

namespace {
constexpr int W=80, H=60, N=W*H;
constexpr int BASELINE_FRAMES=12;
constexpr int PIXEL_DELTA=25;
constexpr int MIN_REGION_PIXELS=approach::minimumArea;
uint16_t baseline[N] = {};
uint8_t gray[N], mask[N], cleanMask[N];
uint16_t queuePixels[N];
int samples=0;
bool cameraOnline=false;
unsigned deterrentCount=0;
approach::Gate growth;

camera_fb_t* captureWithWhiteFlash(night_control::Ticket ticket) {
    if(!control_service::captureLight(ticket,true)) return nullptr;
    vTaskDelay(pdMS_TO_TICKS(120));
    // Use only frames exposed under the same authorized illumination as reference.
    for(int i=0;i<2;++i) {
        if(!control_service::valid(ticket)) {control_service::captureLight(ticket,false);return nullptr;}
        auto stale=esp_camera_fb_get();
        if(!stale) {control_service::captureLight(ticket,false);return nullptr;}
        esp_camera_fb_return(stale);
    }
    auto frame=control_service::valid(ticket)?esp_camera_fb_get():nullptr;
    control_service::captureLight(ticket,false);
    if(frame&&!control_service::valid(ticket)) {esp_camera_fb_return(frame);return nullptr;}
    return frame;
}
void cameraStandby() {
    if(cameraOnline) esp_camera_deinit();
    cameraOnline=false;
    gpio_set_level(GPIO_NUM_37,1);
    gpio_set_direction(GPIO_NUM_37,GPIO_MODE_OUTPUT);
}

using Region=approach::Region;
Region largestRegion() {
    // Majority filter removes isolated changing pixels; then find connected components.
    std::memset(cleanMask,0,N);
    for(int y=1;y<H-1;++y) for(int x=1;x<W-1;++x) {
        int neighbors=0;
        for(int dy=-1;dy<=1;++dy) for(int dx=-1;dx<=1;++dx) neighbors+=mask[(y+dy)*W+x+dx];
        cleanMask[y*W+x]=neighbors>=5;
    }
    Region best;
    for(int i=0;i<N;++i) {
        if(!cleanMask[i]) continue;
        int head=0,tail=0,x0=W,y0=H,x1=0,y1=0,area=0;
        queuePixels[tail++]=i; cleanMask[i]=0;
        while(head<tail) {
            int p=queuePixels[head++],x=p%W,y=p/W;
            ++area; x0=std::min(x0,x);x1=std::max(x1,x);y0=std::min(y0,y);y1=std::max(y1,y);
            const int next[4]={x>0?p-1:-1,x<W-1?p+1:-1,y>0?p-W:-1,y<H-1?p+W:-1};
            for(int q:next) if(q>=0 && cleanMask[q]) {cleanMask[q]=0;queuePixels[tail++]=q;}
        }
        if(area>best.area) best={area,x0,y0,x1-x0+1,y1-y0+1};
    }
    return best;
}

bool algorithmSelftest() {
    std::memset(mask,0,N);
    if(largestRegion().area!=0) return false;
    for(int y=10;y<34;++y) for(int x=10;x<34;++x) mask[y*W+x]=1;
    Region large=largestRegion();
    if(large.area<MIN_REGION_PIXELS || large.x!=10 || large.y!=10) return false;
    std::memset(mask,0,N);
    for(int y=10;y<15;++y) for(int x=10;x<15;++x) mask[y*W+x]=1;
    if(largestRegion().area>=MIN_REGION_PIXELS) return false;
    std::memset(mask,0,N);
    for(int p=0;p<N;p+=101) mask[p]=1;
    return largestRegion().area==0;
}

void cameraPowerSetup() {
    for(auto pair : {std::pair<int,int>{7,1},{45,0},{21,1},{10,0},{37,0}}) {
        gpio_set_level(static_cast<gpio_num_t>(pair.first),pair.second);
        gpio_set_direction(static_cast<gpio_num_t>(pair.first),GPIO_MODE_OUTPUT);
    }
    // TFT CS remains HIGH and backlight LOW while its SPI pins drive RGB.
    for(auto pin : {GPIO_NUM_13,GPIO_NUM_36,GPIO_NUM_35}) {
        gpio_set_level(pin,0);
        gpio_set_direction(pin,GPIO_MODE_OUTPUT);
    }
    gpio_set_direction(GPIO_NUM_5,GPIO_MODE_INPUT);
    gpio_set_pull_mode(GPIO_NUM_5,GPIO_PULLDOWN_ONLY);
    vTaskDelay(pdMS_TO_TICKS(20));
}

esp_err_t cameraInit() {
    camera_config_t c={};
    c.pin_pwdn=37;c.pin_reset=-1;c.pin_xclk=17;
    c.pin_sccb_sda=1;c.pin_sccb_scl=41;
    c.pin_d0=8;c.pin_d1=12;c.pin_d2=14;c.pin_d3=11;
    c.pin_d4=15;c.pin_d5=9;c.pin_d6=16;c.pin_d7=6;
    c.pin_vsync=2;c.pin_href=18;c.pin_pclk=42;
    c.xclk_freq_hz=20000000;c.ledc_timer=LEDC_TIMER_0;c.ledc_channel=LEDC_CHANNEL_0;
    c.pixel_format=PIXFORMAT_JPEG;c.frame_size=FRAMESIZE_QVGA;
    c.jpeg_quality=12;c.fb_count=1;c.fb_location=CAMERA_FB_IN_PSRAM;c.grab_mode=CAMERA_GRAB_WHEN_EMPTY;
    return esp_camera_init(&c);
}

void commandTask(void*) {
    char command[80];
    while(true) {
        if(std::fgets(command,sizeof(command),stdin)) {
            // Exact complete commands only; a long line is discarded, not split.
            size_t length=std::strlen(command);
            if(length==0||command[length-1]!='\n') {int c;while((c=std::getchar())!=EOF&&c!='\n'){};std::printf("COMMAND rejected=too_long_or_incomplete\n");continue;}
            command[--length]=0;
            if(length&&command[length-1]=='\r') command[--length]=0;
            if(std::strcmp(command,"PAUSE")==0) std::printf("COMMAND PAUSE saved=%d\n",control_service::pause());
            else if(std::strcmp(command,"RESUME")==0) std::printf("COMMAND RESUME accepted=%d\n",control_service::resume());
            else if(std::strcmp(command,"BASELINE")==0) std::printf("COMMAND BASELINE accepted=%d\n",control_service::calibrate());
            else if(std::strcmp(command,"ARM")==0) control_service::rearm();
            else if(std::strcmp(command,"ACCESS")==0) control_service::printAccess();
            else if(std::strcmp(command,"STATUS")==0) control_service::printStatus();
            else std::printf("COMMAND rejected=unknown\n");
        } else std::clearerr(stdin);
        vTaskDelay(pdMS_TO_TICKS(50));
    }
}

uint32_t fnv1a(const uint8_t* p,size_t length) {
    uint32_t hash=2166136261u;
    for(size_t i=0;i<length;++i) hash=(hash^p[i])*16777619u;
    return hash;
}
void transmitPhoto(unsigned n,camera_fb_t* fb,const char* metadata) {
    // Battery-only operation must not spend time streaming photos to no host.
    if(!usb_serial_jtag_is_connected()) return;
    std::printf("PHOTO_BEGIN %s\n",metadata);
    char encoded[257];
    for(size_t offset=0;offset<fb->len;offset+=180) {
        if(!usb_serial_jtag_is_connected()) return;
        size_t written=0;
        size_t size=std::min(size_t(180),fb->len-offset);
        if(mbedtls_base64_encode(reinterpret_cast<unsigned char*>(encoded),sizeof(encoded),&written,fb->buf+offset,size)!=0) break;
        encoded[written]=0;
        std::printf("PHOTO_DATA %s\n",encoded);
    }
    std::printf("PHOTO_END %u\n",n);
}
}

extern "C" void app_main() {
    setvbuf(stdout,nullptr,_IONBF,0);
    cameraPowerSetup();
    cameraStandby();
    vTaskDelay(pdMS_TO_TICKS(1500));
    std::printf("BOOT pir_size_deterrent control=solar_ntp pause=persistent default=PAUSED buzzer=3_pulses_1500ms_on_500ms_gap rgb=WHITE photo_flash=ON alarm_flash_hz=5 tft=OFF wifi=AP_STA\n");
    const bool pass=algorithmSelftest(),growthPass=approach::selftest();
    std::printf("MOTION_SELFTEST %s\n",pass?"PASS":"FAIL");
    std::printf("APPROACH_SELFTEST %s minimum_area_percent=10 window_frames=5 net_growth_percent=25 minimum_gain_pixels=96\n",growthPass?"PASS":"FAIL");
    if(!pass||!growthPass) return;
    if(!control_service::initialize()) {std::printf("ERROR control_initialization; outputs_inhibited=1\n");return;}
    if(!esp_psram_is_initialized()) {control_service::fatal("psram_missing");return;}
    if(xTaskCreate(commandTask,"commands",4096,nullptr,3,nullptr)!=pdPASS) {control_service::fatal("command_task");return;}
#ifdef CAMERA_DIAGNOSTIC
    std::printf("READY CAMERA_DIAGNOSTIC commands=ACCESS,STATUS,PAUSE,RESUME,BASELINE,ARM explicit_calibration_only=1 max_frames=12 buzzer=OFF\n");
#else
    std::printf("READY commands=ACCESS,STATUS,PAUSE,RESUME,BASELINE,ARM automatic_calibration=night_only explicit_calibration=enabled_setup PIR_warmup=60_seconds\n");
#endif
    const int64_t pirWarmupEnd=esp_timer_get_time()+60000000;
    int64_t calibrateAfter=0,cooldownUntil=0,lastHeartbeat=0,lastPhoto=0;
    bool active=false,previousPir=true;
    unsigned burstFrames=0,n=0;
    uint64_t seenGeneration=0;
    while(true) {
        if(control_service::alarmEnded()) std::printf("DETERRENT_OFF count=%u\n",deterrentCount);
        auto policy=control_service::snapshot();
        const bool manualRearm=control_service::takeRearm();
        if(policy.generation!=seenGeneration||manualRearm) {
            if(active) std::printf("BURST_ABORT reason=%s generation=%llu frames=%u\n",night_control::stateName(policy.state),static_cast<unsigned long long>(policy.generation),burstFrames);
            active=false;cooldownUntil=0;growth.reset();previousPir=true;cameraStandby();
            if(!policy.baselineReady) {samples=0;std::memset(baseline,0,sizeof(baseline));}
            calibrateAfter=esp_timer_get_time()+(policy.calibrationRequested?3000000:8000000);
            seenGeneration=policy.generation;
            if(policy.calibrationRequested) std::printf("CALIBRATION clear_view_now; starts_in=3_seconds setup_illumination=1\n");
            if(manualRearm) std::printf("ARMED manual_rearm=1 pir=%d wait_for_LOW_then_HIGH=1 policy=%s\n",gpio_get_level(GPIO_NUM_5),night_control::stateName(policy.state));
        }
        int64_t now=esp_timer_get_time();
        bool allowed=policy.captureAllowed;
#ifdef CAMERA_DIAGNOSTIC
        allowed=allowed&&policy.calibrationRequested;
#endif
        if(!allowed) {
            cameraStandby();
            if(now-lastHeartbeat>=1000000) {
                std::printf("STATUS state=%s pir=%d deterrents=%u camera=STANDBY paused=%d clock_valid=%d\n",night_control::stateName(policy.state),gpio_get_level(GPIO_NUM_5),deterrentCount,int(policy.paused),int(policy.clockValid));
                lastHeartbeat=now;
            }
            vTaskDelay(pdMS_TO_TICKS(50));continue;
        }
        if(!policy.baselineReady&&now<calibrateAfter) {vTaskDelay(pdMS_TO_TICKS(50));continue;}
        if(policy.baselineReady&&!active) {
            cameraStandby();
            const bool high=gpio_get_level(GPIO_NUM_5);
            const bool cooldownExpired=cooldownUntil&&now>=cooldownUntil;
            if(cooldownExpired) cooldownUntil=0;
            if(now>=pirWarmupEnd&&!cooldownUntil&&high&&(!previousPir||cooldownExpired)) {
                active=true;burstFrames=0;growth.reset();previousPir=true;
                std::printf("PIR_TRIGGER starting_max_10_frames=1 generation=%llu\n",static_cast<unsigned long long>(policy.generation));
            } else {
                previousPir=high;
                if(now-lastHeartbeat>=1000000) {
                    std::printf("STATUS state=%s pir=%d cooldown_s=%lld deterrents=%u camera=STANDBY\n",now<pirWarmupEnd?"PIR_WARMUP":(cooldownUntil?"COOLDOWN":"ARMED"),int(high),cooldownUntil?static_cast<long long>((cooldownUntil-now+999999)/1000000):0LL,deterrentCount);
                    lastHeartbeat=now;
                }
                vTaskDelay(pdMS_TO_TICKS(50));continue;
            }
        }
        const auto ticket=control_service::ticket(policy.baselineReady?night_control::Capture::Monitoring:night_control::Capture::Calibration);
        if(!ticket.generation||ticket.generation!=policy.generation||ticket.generation!=seenGeneration) continue;
        if(!cameraOnline) {
            if(cameraInit()!=ESP_OK) {
                std::printf("ERROR camera_wake; capture_aborted=1\n");
                active=false;cooldownUntil=0;previousPir=true;growth.reset();cameraStandby();
                vTaskDelay(pdMS_TO_TICKS(500));continue;
            }
            cameraOnline=true;
            vTaskDelay(pdMS_TO_TICKS(1200));
            for(int i=0;i<3&&control_service::valid(ticket);++i) {auto discard=esp_camera_fb_get();if(discard)esp_camera_fb_return(discard);vTaskDelay(pdMS_TO_TICKS(80));}
        }
        if(!control_service::valid(ticket)) continue;
        auto fb=captureWithWhiteFlash(ticket);
        if(!fb) {
            if(control_service::valid(ticket)) std::printf("ERROR camera_capture\n");
            active=false;cooldownUntil=0;previousPir=true;growth.reset();cameraStandby();
            vTaskDelay(pdMS_TO_TICKS(250));continue;
        }
        const int64_t captureUptime=esp_timer_get_time();
        const auto capturePolicy=control_service::snapshot();
        const dl::image::jpeg_img_t jpeg{fb->buf,fb->len};
        auto image=dl::image::sw_decode_jpeg(jpeg,dl::image::DL_IMAGE_PIX_TYPE_RGB888);
        if(!image.data||image.width!=320||image.height!=240) {
            heap_caps_free(image.data);esp_camera_fb_return(fb);
            active=false;cooldownUntil=0;previousPir=true;growth.reset();cameraStandby();
            std::printf("ERROR jpeg_decode\n");vTaskDelay(pdMS_TO_TICKS(500));continue;
        }
        auto rgb=static_cast<uint8_t*>(image.data);
        for(int y=0;y<H;++y) for(int x=0;x<W;++x) {
            unsigned sum=0;
            for(int dy=0;dy<4;++dy) for(int dx=0;dx<4;++dx) {
                size_t p=((y*4+dy)*320+x*4+dx)*3;
                sum+=(77*rgb[p]+150*rgb[p+1]+29*rgb[p+2])>>8;
            }
            gray[y*W+x]=sum/16;
        }
        heap_caps_free(image.data);
        if(!control_service::valid(ticket)) {esp_camera_fb_return(fb);continue;}
        ++n;
        const char* state="CALIBRATING";
        Region region;
        int brightnessShift=0;
        bool fire=false;
        if(!policy.baselineReady) {
            for(int i=0;i<N;++i) baseline[i]+=gray[i];
            if(++samples==BASELINE_FRAMES) {
                for(int i=0;i<N;++i) baseline[i]/=BASELINE_FRAMES;
                if(control_service::completeCalibration(ticket)) std::printf("CALIBRATION complete; baseline_fixed=1\n");
                else {samples=0;std::memset(baseline,0,sizeof(baseline));}
            }
#ifdef CAMERA_DIAGNOSTIC
            state="CAMERA_TEST";
#endif
        } else {
            // Unchanged fixed-reference image metric and five-frame growth gate.
            int histogram[511]={};
            for(int i=0;i<N;++i) ++histogram[int(gray[i])-int(baseline[i])+255];
            int cumulative=0;
            for(int j=0;j<511;++j) {cumulative+=histogram[j];if(cumulative>=N/2){brightnessShift=j-255;break;}}
            for(int i=0;i<N;++i) mask[i]=std::abs(int(gray[i])-int(baseline[i])-brightnessShift)>=PIXEL_DELTA;
            region=largestRegion();
            bool big=region.area>=MIN_REGION_PIXELS;
            bool approaching=growth.update(region);
            state=approaching?"LARGE_CHANGE":(big?"WAITING_FOR_GROWTH":(region.area?"SMALL_CHANGE":"CLEAR"));
#ifndef CAMERA_DIAGNOSTIC
            if(active&&approaching) {
                fire=control_service::startAlarm(ticket);
                if(fire) {
                    ++deterrentCount;
                    std::printf("DETERRENT_ON frame=%u count=%u pulses=3 on_ms=1500 gap_ms=500 duration_ms=5500 frequency_hz=2000 white_flash_hz=5\n",n,deterrentCount);
                }
            }
#endif
        }
        if(active) ++burstFrames;
        const int64_t decisionUptime=esp_timer_get_time();
        char metadata[1100];
        std::snprintf(metadata,sizeof(metadata),"{\"frame\":%u,\"state\":\"%s\",\"area_percent\":%.3f,\"cutoff_percent\":10.0,\"growth_window\":5,\"growth_required_percent\":25,\"growth_min_gain_pixels\":96,\"growth_frames\":%d,\"growth_percent\":%d,\"growth_pixels\":%d,\"changed_pixels\":%d,\"brightness_shift\":%d,\"bbox\":[%d,%d,%d,%d],\"grid\":[80,60],\"pir\":%d,\"baseline_samples\":%d,\"jpeg_bytes\":%u,\"fnv1a\":\"%08lx\",\"control_generation\":%llu,\"clock_valid\":%s,\"utc_capture_us\":%lld,\"capture_uptime_us\":%lld,\"decision_uptime_us\":%lld,\"policy_state\":\"%s\",\"deterrent_started\":%s}",n,state,100.0*region.area/N,growth.frames(),growth.gainPercent(),growth.gainPixels(),region.area,brightnessShift,region.x,region.y,region.w,region.h,gpio_get_level(GPIO_NUM_5),samples,unsigned(fb->len),static_cast<unsigned long>(fnv1a(fb->buf,fb->len)),static_cast<unsigned long long>(ticket.generation),capturePolicy.clockValid?"true":"false",static_cast<long long>(capturePolicy.utcUs),static_cast<long long>(captureUptime),static_cast<long long>(decisionUptime),night_control::stateName(capturePolicy.state),fire?"true":"false");
        std::printf("MOTION %s\n",metadata);
        if(active||esp_timer_get_time()-lastPhoto>=2000000) {transmitPhoto(n,fb,metadata);lastPhoto=esp_timer_get_time();}
        esp_camera_fb_return(fb);
        if(active&&(fire||burstFrames>=10)) {
            std::printf("BURST_END frames=%u large_confirmed=%d\n",burstFrames,int(fire));
            active=false;growth.reset();cooldownUntil=fire?0:esp_timer_get_time()+120000000;
            previousPir=true;cameraStandby();
            if(fire) std::printf("CONFIRMED cooldown_s=0 wait_for_alarm_end_then_PIR_LOW=1 camera=STANDBY\n");
            else std::printf("COOLDOWN duration_s=120 reason=NO_GROWING_LARGE_TARGET camera=STANDBY\n");
        }
        vTaskDelay(pdMS_TO_TICKS(250));
    }
}
