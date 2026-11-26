#include <jni.h>

#include <stdexcept>
#include <string>

extern "C" JNIEXPORT jstring JNICALL
Java_demo_jniapp_MainActivity_message(JNIEnv *env, jclass) {
  try {
    throw std::runtime_error("Hello from JNI");
  } catch (const std::exception &error) {
    const std::string message = error.what();
    return env->NewStringUTF(message.c_str());
  }
}
