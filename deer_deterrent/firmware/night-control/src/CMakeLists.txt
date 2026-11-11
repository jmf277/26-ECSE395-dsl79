idf_component_register(SRCS "app_main.cpp" "solar.cpp" "control_policy.cpp"
                            "control_config.cpp" "control_service.cpp" INCLUDE_DIRS "."
                       # The growth gate and the status display are the MVP program's
                       # headers, used from its folder so the rule exists once.
                       PRIV_INCLUDE_DIRS "../../motion-size/src"
                       PRIV_REQUIRES esp_timer esp_psram driver mbedtls esp_lcd
                                     esp_wifi esp_event esp_netif esp_http_server
                                     nvs_flash json bootloader_support)
option(DEER_CAMERA_DIAGNOSTIC "Explicit, bounded calibration capture; alarms disabled" OFF)
if(DEER_CAMERA_DIAGNOSTIC)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE CAMERA_DIAGNOSTIC=1)
endif()
