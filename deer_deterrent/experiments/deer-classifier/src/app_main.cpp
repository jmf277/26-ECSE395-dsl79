#include <cinttypes>
#include <cstdio>
#include <cstring>
#include <limits>
#include <cstdarg>
#include <algorithm>
#include "driver/ledc.h"
#include "driver/usb_serial_jtag.h"
#include "esp_random.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "mbedtls/base64.h"
#include "cJSON.h"
#include "trial_control.hpp"
#include "trial_command.hpp"

#include "driver/gpio.h"
#include "esp_camera.h"
#include "esp_heap_caps.h"
#include "esp_psram.h"
#include "esp_timer.h"
#include "esp_app_desc.h"
#include "freertos/task.h"
#include "dl_image_jpeg.hpp"
#include "deer_detector.hpp"
#include "temporal_gate.hpp"

namespace {
constexpr float kThreshold = CONFIG_DEER_THRESHOLD_PERCENT / 100.0f;
constexpr int kJpegQuality = 12;  // camera JPEG quality, written into every frame record
#ifdef CONFIG_DEER_CAMERA_HMIRROR
constexpr bool kHmirror = true;
#else
constexpr bool kHmirror = false;
#endif
#ifdef CONFIG_DEER_CAMERA_VFLIP
constexpr bool kVflip = true;
#else
constexpr bool kVflip = false;
#endif

bool run_gate_selftest() {
  TemporalDeerGate test(kThreshold);
  bool passed = !test.update(true, 1.0f);
  passed &= test.update(true, 1.0f);
  passed &= test.update(true, 0.0f);
  passed &= !test.update(true, 0.0f);
  test.reset();
  passed &= !test.update(true, 1.0f);
  passed &= !test.update(true, std::numeric_limits<float>::quiet_NaN());
  passed &= !test.update(true, 1.0f);
  passed &= test.update(true, 1.0f);
  passed &= !test.update(false, 0.0f);
  passed &= !test.update(true, 1.0f);
  std::printf("GATE_SELFTEST %s\n", passed ? "PASS" : "FAIL");
  return passed;
}

bool run_inference_smoke(DeerDetector& detector) {
  constexpr size_t bytes = 224 * 224 * 3;
  void* pixels = heap_caps_aligned_alloc(16, bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (!pixels) {
    std::printf("INFERENCE_SELFTEST FAIL reason=allocation\n");
    return false;
  }
  std::memset(pixels, 114, bytes);
  const dl::image::img_t blank{pixels, 224, 224, dl::image::DL_IMAGE_PIX_TYPE_RGB888};
  float score = 0;
  const bool passed = detector.infer(blank, score);
  heap_caps_free(pixels);
  std::printf("INFERENCE_SELFTEST %s score=%.4f\n", passed ? "PASS" : "FAIL", score);
  return passed;
}

bool check_camera_pins() {
#ifndef CONFIG_DEER_PINS_CONFIRMED
  std::printf("ERROR n=0 reason=pins_unconfigured present=0\n");
  std::printf("Set the Deer recorded trials GPIOs in idf.py menuconfig before flashing.\n");
  return false;
#endif
  const int pins[] = {CONFIG_DEER_PIN_XCLK, CONFIG_DEER_PIN_SDA, CONFIG_DEER_PIN_SCL,
                      CONFIG_DEER_PIN_D0, CONFIG_DEER_PIN_D1, CONFIG_DEER_PIN_D2, CONFIG_DEER_PIN_D3,
                      CONFIG_DEER_PIN_D4, CONFIG_DEER_PIN_D5, CONFIG_DEER_PIN_D6, CONFIG_DEER_PIN_D7,
                      CONFIG_DEER_PIN_VSYNC, CONFIG_DEER_PIN_HREF, CONFIG_DEER_PIN_PCLK,
                      CONFIG_DEER_PIN_PWDN, CONFIG_DEER_PIN_RESET};
  for (size_t i = 0; i < sizeof(pins) / sizeof(pins[0]); ++i) {
    const int pin = pins[i];
    if (i >= 14 && pin == -1) continue;  // Optional PWDN and RESET only.
    if (!GPIO_IS_VALID_GPIO(pin) || pin == 19 || pin == 20 || pin == 5 || pin == 7 || pin == 10 || pin == 13 || pin == 21 || pin == 35 || pin == 36 || pin == 45 ||
        (pin >= 26 && pin <= 32)) {
      std::printf("ERROR n=0 reason=invalid_or_reserved_gpio gpio=%d present=0\n", pin);
      return false;
    }
    if ((i < 3 || i >= 14) && !GPIO_IS_VALID_OUTPUT_GPIO(pin)) {
      std::printf("ERROR n=0 reason=camera_output_gpio gpio=%d present=0\n", pin);
      return false;
    }
    for (size_t j = 0; j < i; ++j) {
      if (pins[j] == pin) {
        std::printf("ERROR n=0 reason=duplicate_gpio gpio=%d present=0\n", pin);
        return false;
      }
    }
  }
  return true;
}

void initialize_feather_power() {
  // Adafruit's TFT Feather gates the TFT and I2C pull-up rail with GPIO21.
  // Keep the TFT deselected and its backlight off while using the camera.
  gpio_set_level(GPIO_NUM_7, 1);
  gpio_set_direction(GPIO_NUM_7, GPIO_MODE_OUTPUT);
  gpio_set_level(GPIO_NUM_45, 0);
  gpio_set_direction(GPIO_NUM_45, GPIO_MODE_OUTPUT);
  gpio_set_level(GPIO_NUM_21, 1);
  gpio_set_direction(GPIO_NUM_21, GPIO_MODE_OUTPUT);
  vTaskDelay(pdMS_TO_TICKS(20));
}

esp_err_t initialize_camera() {
  camera_config_t config{};
  config.pin_pwdn = CONFIG_DEER_PIN_PWDN;
  config.pin_reset = CONFIG_DEER_PIN_RESET;
  config.pin_xclk = CONFIG_DEER_PIN_XCLK;
  config.pin_sccb_sda = CONFIG_DEER_PIN_SDA;
  config.pin_sccb_scl = CONFIG_DEER_PIN_SCL;
  config.pin_d0 = CONFIG_DEER_PIN_D0;
  config.pin_d1 = CONFIG_DEER_PIN_D1;
  config.pin_d2 = CONFIG_DEER_PIN_D2;
  config.pin_d3 = CONFIG_DEER_PIN_D3;
  config.pin_d4 = CONFIG_DEER_PIN_D4;
  config.pin_d5 = CONFIG_DEER_PIN_D5;
  config.pin_d6 = CONFIG_DEER_PIN_D6;
  config.pin_d7 = CONFIG_DEER_PIN_D7;
  config.pin_vsync = CONFIG_DEER_PIN_VSYNC;
  config.pin_href = CONFIG_DEER_PIN_HREF;
  config.pin_pclk = CONFIG_DEER_PIN_PCLK;
  config.xclk_freq_hz = CONFIG_DEER_CAMERA_XCLK_MHZ * 1000000;
  config.ledc_timer = LEDC_TIMER_0;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.pixel_format = PIXFORMAT_JPEG;
#ifdef CONFIG_DEER_CAMERA_VGA
  config.frame_size = FRAMESIZE_VGA;
#else
  config.frame_size = FRAMESIZE_QVGA;
#endif
  config.jpeg_quality = kJpegQuality;
  config.fb_count = 1;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  return esp_camera_init(&config);
}

SemaphoreHandle_t policyMutex = nullptr, serialMutex = nullptr;
recognition_trial::Controller controller(kThreshold);
char bootId[17];
char buildJson[256] = "{}";  // firmware identity, filled at boot from the application descriptor
bool hardwareReady = false;
class Lock {
  SemaphoreHandle_t mutex_;
public:
  explicit Lock(SemaphoreHandle_t m) : mutex_(m) { xSemaphoreTake(mutex_, portMAX_DELAY); }
  ~Lock() { xSemaphoreGive(mutex_); }
};
void line(const char* format, ...) {
  Lock lock(serialMutex);
  flockfile(stdout);
  va_list args; va_start(args, format); std::vprintf(format, args); va_end(args);
  std::printf("\n");
  funlockfile(stdout);
}
void applyOutputs(deterrent::Outputs value) {
  if (!hardwareReady) return;
  for (int pin : deterrent::whitePins) gpio_set_level(static_cast<gpio_num_t>(pin), value.white);
  ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_2, value.tone ? deterrent::toneDuty : 0);
  ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_2);
}
void outputTick(void*) {
  Lock lock(policyMutex);
  if (!usb_serial_jtag_is_connected() && (controller.armed || controller.busy || controller.alarmStart >= 0)) controller.stop();
  applyOutputs(controller.tick(esp_timer_get_time()));
}
bool initializeOutputs() {
  // White off; the buzzer input high, its silent level (shared/alarm_pattern.hpp).
  for (int number : {deterrent::whitePins[0], deterrent::whitePins[1], deterrent::whitePins[2], deterrent::buzzerPin}) {
    const auto pin = static_cast<gpio_num_t>(number);
    gpio_set_level(pin, number == deterrent::buzzerPin);
    if (gpio_set_direction(pin, GPIO_MODE_OUTPUT) != ESP_OK) return false;
  }
  gpio_set_direction(GPIO_NUM_5, GPIO_MODE_INPUT);
  gpio_set_pull_mode(GPIO_NUM_5, GPIO_PULLDOWN_ONLY);
  ledc_timer_config_t timer{};
  timer.speed_mode=LEDC_LOW_SPEED_MODE; timer.duty_resolution=static_cast<ledc_timer_bit_t>(deterrent::toneResolutionBits);
  timer.timer_num=LEDC_TIMER_1; timer.freq_hz=deterrent::toneHz; timer.clk_cfg=LEDC_AUTO_CLK;
  if (ledc_timer_config(&timer) != ESP_OK) return false;
  ledc_channel_config_t channel{};
  channel.gpio_num=deterrent::buzzerPin; channel.speed_mode=LEDC_LOW_SPEED_MODE;
  channel.channel=LEDC_CHANNEL_2; channel.timer_sel=LEDC_TIMER_1; channel.duty=0;
  channel.flags.output_invert=1;  // duty 0 holds the pin high; the half-duty tone stays half duty
  if (ledc_channel_config(&channel) != ESP_OK) return false;
  hardwareReady = true;
  esp_timer_create_args_t args{}; args.callback=outputTick; args.name="trial_outputs";
  esp_timer_handle_t timerHandle = nullptr;
  return esp_timer_create(&args, &timerHandle)==ESP_OK && esp_timer_start_periodic(timerHandle,20000)==ESP_OK;
}
unsigned remainingSeconds(int64_t until,int64_t now) {
  return until>now?unsigned((until-now+999999)/1000000):0u;
}
void status(const char* command, bool accepted) {
  bool armed,busy,inFlight,alarm,pir,full; unsigned trial,within,requested,warmup,pause; const char* mode; const char* lighting; const char* source;
  {
    Lock lock(policyMutex);
    const int64_t now=esp_timer_get_time();
    pir=controller.pirHigh;full=controller.recordInput;
    warmup=remainingSeconds(controller.warmupEndUs(),now);
    pause=controller.cooldownUntil?remainingSeconds(controller.cooldownUntil,now):0u;
    mode=controller.mode==recognition_trial::Mode::Manual?"manual":"pir";
    armed=controller.armed;busy=controller.busy;inFlight=controller.inFlight;
    alarm=controller.alarmStart>=0;trial=controller.trial;within=controller.within;requested=controller.requested;
    lighting=controller.lighting==recognition_trial::Lighting::White?"white":"ambient";
    source=controller.alarmSource==recognition_trial::AlarmSource::OutputTest?"output_test":controller.alarmSource==recognition_trial::AlarmSource::Recognition?"recognition":"none";
  }
  line("TRIAL_STATUS {\"boot\":\"%s\",\"command\":\"%s\",\"accepted\":%s,\"mode\":\"%s\",\"lighting\":\"%s\",\"recording\":\"%s\",\"armed\":%s,\"busy\":%s,\"in_flight\":%s,\"alarm\":%s,\"alarm_source\":\"%s\",\"trial\":%u,\"attempted\":%u,\"requested\":%u,\"pir\":%d,\"warmup_remaining_s\":%u,\"pause_remaining_s\":%u}",
    bootId,command,accepted?"true":"false",mode,lighting,full?"full":"jpeg",armed?"true":"false",busy?"true":"false",inFlight?"true":"false",alarm?"true":"false",source,trial,within,requested,pir?1:0,warmup,pause);
}
// One line whenever the sensor's level, warm-up or 120 s pause changes in sensor mode.
struct SensorState { bool pirMode=false,high=false,warmedUp=false,paused=false; };
bool operator!=(const SensorState& a,const SensorState& b) {
  return a.pirMode!=b.pirMode||a.high!=b.high||a.warmedUp!=b.warmedUp||a.paused!=b.paused;
}
void sensorEvent(const SensorState& s,bool armed,unsigned pauseSeconds,int64_t at) {
  line("PIR_STATE {\"boot\":\"%s\",\"mode\":\"%s\",\"pir\":%d,\"warmed_up\":%s,\"paused\":%s,\"pause_remaining_s\":%u,\"armed\":%s,\"at_us\":%lld}",
    bootId,s.pirMode?"pir":"manual",s.high?1:0,s.warmedUp?"true":"false",s.paused?"true":"false",pauseSeconds,armed?"true":"false",static_cast<long long>(at));
}
// TEST OUTPUT injects the alarm trigger a confirmed result sends; its start and
// end are recorded apart from frames, since it takes no picture and has no score.
void outputTestEvent(uint32_t id,const char* event) {
  line("OUTPUT_TEST {\"schema\":1,\"boot\":\"%s\",\"test\":%u,\"event\":\"%s\",\"source\":\"manual_command\",\"observed_us\":%lld}",
    bootId,id,event,static_cast<long long>(esp_timer_get_time()));
}
void commandTask(void*) {
  char buffer[80];
  for (;;) {
    if (!std::fgets(buffer,sizeof(buffer),stdin)) {std::clearerr(stdin);vTaskDelay(pdMS_TO_TICKS(25));continue;}
    size_t size=std::strlen(buffer);
    if (!size || buffer[size-1]!='\n') {
      int c;while((c=std::getchar())!=EOF&&c!='\n'){};
      status("invalid_line",false);continue;
    }
    buffer[--size]=0;if(size&&buffer[size-1]=='\r')buffer[--size]=0;
    const auto cmd=recognition_trial::parseCommand(buffer);
    using K=recognition_trial::CommandKind;
    bool accepted=false;
    {
      Lock lock(policyMutex);
      switch(cmd.kind) {
        case K::Capture: accepted=usb_serial_jtag_is_connected()&&controller.startManual(cmd.frames);break;
        case K::Manual:controller.setMode(recognition_trial::Mode::Manual);accepted=true;break;
        case K::Pir:controller.setMode(recognition_trial::Mode::Pir);accepted=true;break;
        case K::Arm:accepted=usb_serial_jtag_is_connected()&&controller.arm(true);break;
        case K::Disarm:accepted=controller.arm(false);break;
        case K::Stop:controller.stop();accepted=true;break;
        case K::Status:accepted=true;break;
        case K::Ambient:accepted=controller.setLighting(recognition_trial::Lighting::Ambient);break;
        case K::White:accepted=controller.setLighting(recognition_trial::Lighting::White);break;
        case K::RecordFull:accepted=controller.setRecording(true);break;
        case K::RecordJpeg:accepted=controller.setRecording(false);break;
        case K::OutputTest:
          accepted=usb_serial_jtag_is_connected()&&controller.startOutputTest(esp_timer_get_time());
          break;
        default:break;
      }
      applyOutputs(controller.tick(esp_timer_get_time()));
    }
    // Only parsed commands enter JSON, so arbitrary console text cannot inject it.
    status(cmd.kind==K::Invalid?"invalid_command":buffer,accepted);
  }
}
uint32_t checksum(const uint8_t* p,size_t size) {
  uint32_t hash=2166136261u;for(size_t i=0;i<size;++i)hash=(hash^p[i])*16777619u;return hash;
}
// STOP, DISARM or a mode change ends the model-input transfer at the next group
// of chunks; the picture, which is sent first and is small, is always completed.
bool blob(recognition_trial::Ticket t,const char* kind,const uint8_t* data,size_t size,bool interruptible,bool* cancelled) {
  if (!data || !size || !usb_serial_jtag_is_connected()) return false;
  line("TRIAL_BLOB {\"boot\":\"%s\",\"trial\":%u,\"frame\":%u,\"kind\":\"%s\",\"bytes\":%u,\"fnv1a\":\"%08" PRIx32 "\"}",bootId,t.trial,t.frame,kind,unsigned(size),checksum(data,size));
  char encoded[257];
  for(size_t offset=0;offset<size;offset+=180) {
    if(!usb_serial_jtag_is_connected()) return false;
    if(interruptible&&offset%3600==0) {
      bool current;
      {Lock lock(policyMutex);current=controller.valid(t);}
      if(!current) {
        if(cancelled)*cancelled=true;
        line("TRIAL_BLOB_CANCELLED %u %s %u",t.frame,kind,unsigned(offset));
        return false;
      }
    }
    size_t written=0;
    if(mbedtls_base64_encode(reinterpret_cast<unsigned char*>(encoded),sizeof(encoded),&written,data+offset,std::min(size_t(180),size-offset)))return false;
    encoded[written]=0;line("TRIAL_DATA %u %s %u %s",t.frame,kind,unsigned(offset),encoded);
    // Allow input cancellation and the independently timed response to run.
    if(offset%3600==0)vTaskDelay(1);
  }
  line("TRIAL_BLOB_END %u %s",t.frame,kind);return true;
}
void runFrame(recognition_trial::Ticket t,DeerDetector& detector) {
  const int64_t started=esp_timer_get_time();
  bool captureAllowed;
  {
    Lock lock(policyMutex);
    captureAllowed=controller.beginCapture(t,started);
    applyOutputs(controller.tick(started));
  }
  if(captureAllowed&&t.illuminated)vTaskDelay(pdMS_TO_TICKS(120));
  auto permitted=[&]() {
    Lock lock(policyMutex);
    return controller.capturePermitted(t,esp_timer_get_time());
  };
  // The one-buffer driver may hold a frame exposed before the manual command.
  // Flush two frames in the selected lighting; a failed flush is not a usable sample.
  for(int i=0;i<2&&captureAllowed;++i) {
    if(!permitted()){captureAllowed=false;break;}
    auto* stale=esp_camera_fb_get();
    if(stale)esp_camera_fb_return(stale);else captureAllowed=false;
  }
  camera_fb_t* frame=captureAllowed&&permitted()?esp_camera_fb_get():nullptr;
  const int64_t acquired=esp_timer_get_time();
  bool captureLightingOkay;
  {
    Lock lock(policyMutex);
    captureLightingOkay=controller.endCapture(t,acquired);
    applyOutputs(controller.tick(acquired));
  }
  const bool jpeg=frame&&frame->format==PIXFORMAT_JPEG&&frame->len>0&&frame->width<=640&&frame->height<=480;
  dl::image::img_t image{};
  if(jpeg)image=dl::image::sw_decode_jpeg({frame->buf,frame->len},dl::image::DL_IMAGE_PIX_TYPE_RGB888);
  const int64_t decoded=esp_timer_get_time();
  const bool decodedOkay=jpeg&&image.data&&image.width==frame->width&&image.height==frame->height;
  float score=0;
  InferenceRecord record;
  record.keepInput=t.recordInput;
  const bool inferenceOkay=captureAllowed&&captureLightingOkay&&decodedOkay&&detector.infer(image,score,false,&record);
  heap_caps_free(image.data);
  const int64_t inferred=esp_timer_get_time();
  recognition_trial::Decision decision;
  const char* mode; bool armed;
  {
    Lock lock(policyMutex);
    decision=controller.commit(t,inferenceOkay,score,inferred);
    armed=controller.armed;mode=t.pir?"pir":"manual";
    applyOutputs(controller.tick(inferred));
  }
  cJSON* result=cJSON_CreateObject();
  if(result) {
    cJSON_AddNumberToObject(result,"schema",2);cJSON_AddStringToObject(result,"boot",bootId);
    cJSON_AddNumberToObject(result,"trial",t.trial);cJSON_AddNumberToObject(result,"frame",t.frame);cJSON_AddNumberToObject(result,"within",t.within);
    cJSON_AddStringToObject(result,"trigger",mode);cJSON_AddStringToObject(result,"model_sha256",DEER_MODEL_SHA256);
    cJSON_AddBoolToObject(result,"valid",inferenceOkay);cJSON_AddBoolToObject(result,"committed",decision.accepted);
    cJSON_AddBoolToObject(result,"present",decision.present);cJSON_AddBoolToObject(result,"armed",armed);cJSON_AddBoolToObject(result,"alarm_started",decision.alarmStarted);
    cJSON_AddNumberToObject(result,"threshold",kThreshold);cJSON_AddNumberToObject(result,"window",3);cJSON_AddNumberToObject(result,"required",2);
    if(inferenceOkay)cJSON_AddNumberToObject(result,"score",score);else cJSON_AddNullToObject(result,"score");
    cJSON_AddStringToObject(result,"error",!captureLightingOkay?"capture_cancelled_or_lighting_timeout":!frame?"camera_capture":!jpeg?"frame_format":!decodedOkay?"jpeg_decode":!inferenceOkay?"inference_or_input_copy":"");
    cJSON_AddStringToObject(result,"lighting",t.illuminated?"white":"ambient");
    cJSON_AddBoolToObject(result,"capture_lighting_ok",captureLightingOkay);
    cJSON_AddNumberToObject(result,"light_settle_ms",t.illuminated?120:0);
    cJSON_AddNumberToObject(result,"discarded_frames_required",2);
    cJSON_AddNumberToObject(result,"jpeg_bytes",jpeg?frame->len:0);cJSON_AddNumberToObject(result,"camera_width",jpeg?frame->width:0);cJSON_AddNumberToObject(result,"camera_height",jpeg?frame->height:0);
    // input_bytes is the model input the score came from; input_sent says whether
    // this frame's exact copy follows (RECORD FULL) or was not kept (RECORD JPEG).
    cJSON_AddStringToObject(result,"recording",t.recordInput?"full":"jpeg");
    cJSON_AddNumberToObject(result,"input_bytes",record.inputBytes);
    cJSON_AddBoolToObject(result,"input_sent",record.input!=nullptr);
    cJSON_AddStringToObject(result,"input_dtype","int8");cJSON_AddStringToObject(result,"input_layout","NHWC");
    const int shape[]={1,224,224,3};cJSON_AddItemToObject(result,"input_shape",cJSON_CreateIntArray(shape,4));
    cJSON_AddNumberToObject(result,"input_exponent",record.exponent);
    cJSON_AddStringToObject(result,"input_scaling","real = signed_byte * 2**input_exponent; RGB normalization /255; letterbox 114");
    cJSON_AddNumberToObject(result,"started_us",started);cJSON_AddNumberToObject(result,"acquired_us",acquired);cJSON_AddNumberToObject(result,"inferred_us",inferred);
    cJSON_AddNumberToObject(result,"capture_us",acquired-started);cJSON_AddNumberToObject(result,"decode_us",decoded-acquired);
    cJSON_AddNumberToObject(result,"preprocessing_us",record.preprocessingUs);cJSON_AddNumberToObject(result,"input_copy_us",record.inputCopyUs);
    cJSON_AddNumberToObject(result,"model_us",record.modelUs);cJSON_AddNumberToObject(result,"postprocessing_us",record.postprocessingUs);
    cJSON_AddNumberToObject(result,"candidate_floor",kCandidateFloor);cJSON_AddNumberToObject(result,"nms_iou",kNmsIou);
    cJSON_AddNumberToObject(result,"max_boxes",kMaxBoxes);cJSON_AddNumberToObject(result,"jpeg_quality",kJpegQuality);
    cJSON_AddNumberToObject(result,"frame_interval_ms",CONFIG_DEER_FRAME_INTERVAL_MS);cJSON_AddNumberToObject(result,"camera_xclk_mhz",CONFIG_DEER_CAMERA_XCLK_MHZ);
    cJSON_AddBoolToObject(result,"hmirror",kHmirror);cJSON_AddBoolToObject(result,"vflip",kVflip);
    cJSON_AddItemToObject(result,"build",cJSON_Parse(buildJson));
    cJSON_AddStringToObject(result,"box_coordinates","left,top,right,bottom in original camera image pixels");
    auto* boxes=cJSON_AddArrayToObject(result,"boxes");
    for(const auto& b:record.boxes) {
      auto* j=cJSON_CreateObject();cJSON_AddNumberToObject(j,"category",b.category);cJSON_AddNumberToObject(j,"score",b.score);
      cJSON_AddItemToObject(j,"box",cJSON_CreateIntArray(b.box.data(),b.box.size()));cJSON_AddItemToArray(boxes,j);
    }
    char* json=cJSON_PrintUnformatted(result);
    if(json) {
      const int64_t transferStart=esp_timer_get_time();
      line("TRIAL_FRAME %s",json);cJSON_free(json);
      bool cancelled=false;
      const bool jpegSent=!jpeg||blob(t,"jpeg",frame->buf,frame->len,false,nullptr);
      const bool inputSent=!record.input||blob(t,"input",reinterpret_cast<const uint8_t*>(record.input),record.inputBytes,true,&cancelled);
      line("TRIAL_END {\"boot\":\"%s\",\"trial\":%u,\"frame\":%u,\"transfer_complete\":%s,\"transfer_cancelled\":%s,\"transfer_us\":%lld}",bootId,t.trial,t.frame,jpegSent&&inputSent?"true":"false",cancelled?"true":"false",static_cast<long long>(esp_timer_get_time()-transferStart));
    } else line("TRIAL_ERROR metadata_allocation frame=%u",t.frame);
    cJSON_Delete(result);
  } else line("TRIAL_ERROR metadata_allocation frame=%u",t.frame);
  if(frame)esp_camera_fb_return(frame);
  bool ended;
  {Lock lock(policyMutex);controller.release(t,esp_timer_get_time());ended=!controller.busy;}
  if(ended)status("TRIAL_FINISHED",true);
}
} // namespace

extern "C" void app_main() {
  setvbuf(stdout,nullptr,_IONBF,0);
  vTaskDelay(pdMS_TO_TICKS(1500));
  policyMutex=xSemaphoreCreateMutex();serialMutex=xSemaphoreCreateMutex();
  if(!policyMutex||!serialMutex)return;
  std::snprintf(bootId,sizeof(bootId),"%08" PRIx32 "%08" PRIx32,esp_random(),esp_random());
  const esp_app_desc_t* app=esp_app_get_description();
  char elf[65]={};esp_app_get_elf_sha256(elf,sizeof(elf));
  std::snprintf(buildJson,sizeof(buildJson),"{\"project\":\"%.31s\",\"version\":\"%.31s\",\"idf\":\"%.31s\",\"compiled\":\"%.15s %.15s\",\"elf_sha256\":\"%s\"}",
    app->project_name,app->version,app->idf_ver,app->date,app->time,elf);
  line("BOOT deer_recorded_trials schema=2 boot=%s model_bytes=%u sha256=%s firmware=%s",bootId,unsigned(DEER_MODEL_BYTES),DEER_MODEL_SHA256,buildJson);
  if(!run_gate_selftest()||!check_camera_pins())return;
  initialize_feather_power();
  if(!initializeOutputs()){line("ERROR outputs_init");return;}
  if(!esp_psram_is_initialized()||esp_psram_get_size()<2*1024*1024){line("ERROR psram_required");return;}
  const esp_err_t cameraError=initialize_camera();
  if(cameraError!=ESP_OK){line("ERROR camera_init code=0x%x name=%s",unsigned(cameraError),esp_err_to_name(cameraError));return;}
  sensor_t* sensor=esp_camera_sensor_get();
  if(!sensor||sensor->id.PID!=OV5640_PID){line("ERROR expected_ov5640");esp_camera_deinit();return;}
#ifdef CONFIG_DEER_CAMERA_HMIRROR
  sensor->set_hmirror(sensor,1);
#endif
#ifdef CONFIG_DEER_CAMERA_VFLIP
  sensor->set_vflip(sensor,1);
#endif
  DeerDetector detector;
  if(!detector.initialize()||!run_inference_smoke(detector)){esp_camera_deinit();return;}
  for(unsigned i=0;i<8;++i){auto* f=esp_camera_fb_get();if(f)esp_camera_fb_return(f);vTaskDelay(pdMS_TO_TICKS(100));}
  line("READY boot=%s mode=manual armed=0 threshold=%.4f window=3 required=2 model_input=224x224x3 lighting=ambient recording=full commands=CAPTURE_n,MODE_MANUAL,MODE_PIR,LIGHT_AMBIENT,LIGHT_WHITE,RECORD_FULL,RECORD_JPEG,TEST_OUTPUT,ARM,DISARM,STOP,STATUS",bootId,kThreshold);
  if(xTaskCreate(commandTask,"trial_commands",4096,nullptr,3,nullptr)!=pdPASS){line("ERROR command_task");return;}
  int64_t nextCapture=0;
  SensorState reported;
  while(true) {
    bool started=false;uint32_t testStart=0;recognition_trial::Ticket ticket;recognition_trial::OutputTestEnd testEnd;
    SensorState sensor;bool sensorArmed=false;unsigned sensorPause=0;int64_t sensorAt=0;
    {
      Lock lock(policyMutex);
      const auto now=esp_timer_get_time();
      testStart=controller.takeOutputTestStart();testEnd=controller.takeOutputTestEnd();
      if(usb_serial_jtag_is_connected()) {
        started=controller.pollPir(gpio_get_level(GPIO_NUM_5),now);
        if(now>=nextCapture)ticket=controller.take();
      }
      sensor.pirMode=controller.mode==recognition_trial::Mode::Pir;sensor.high=controller.pirHigh;
      sensor.warmedUp=now>=controller.warmupEndUs();sensor.paused=controller.cooldownUntil!=0;
      sensorArmed=controller.armed;sensorPause=sensor.paused?remainingSeconds(controller.cooldownUntil,now):0u;sensorAt=now;
    }
    if(sensor!=reported&&(sensor.pirMode||reported.pirMode))sensorEvent(sensor,sensorArmed,sensorPause,sensorAt);
    reported=sensor;
    if(testStart)outputTestEvent(testStart,"started");
    if(testEnd.id)outputTestEvent(testEnd.id,testEnd.completed?"completed":"cancelled");
    if(started)status("PIR_TRIGGER",true);
    if(ticket.generation) {
      runFrame(ticket,detector);
      nextCapture=esp_timer_get_time()+int64_t(CONFIG_DEER_FRAME_INTERVAL_MS)*1000;
    }
    vTaskDelay(pdMS_TO_TICKS(20));
  }
}
