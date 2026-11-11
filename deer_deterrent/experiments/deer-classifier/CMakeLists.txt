cmake_minimum_required(VERSION 3.16)
# Keep the application discoverable by both ESP-IDF and PlatformIO.
list(APPEND EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/src")
list(REMOVE_DUPLICATES EXTRA_COMPONENT_DIRS)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(deer_recorded_trials)
if(NOT IDF_TARGET STREQUAL "esp32s3")
    message(FATAL_ERROR "This model and firmware require ESP32-S3. Run idf.py set-target esp32s3.")
endif()
