cmake_minimum_required(VERSION 3.22)
project(AOSStudioJniSample LANGUAGES CXX)

add_library(greeting SHARED greeting.cpp)
target_compile_features(greeting PRIVATE cxx_std_17)
target_compile_options(greeting PRIVATE -fexceptions)
