LOCAL_PATH := $(call my-dir)

include $(CLEAR_VARS)
LOCAL_MODULE := greeting
LOCAL_SRC_FILES := greeting.cpp
LOCAL_CPP_FEATURES := exceptions
LOCAL_CPPFLAGS := -std=c++17
include $(BUILD_SHARED_LIBRARY)
