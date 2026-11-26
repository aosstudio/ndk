#!/usr/bin/env bash
set -euo pipefail

cd "${NDK_WORK:?Run make build from the repository root}"
pilot=$PWD
export TMPDIR="$pilot/tmp"
llvm="$pilot/stage/host-llvm"
sysroot="$pilot/stage/sysroot"
sources="$pilot/source/llvm-project"
jobs=${JOBS:-6}
abi=${1:-x86_64}
api=21
case "$abi" in
    x86_64) arch=x86_64; triple=x86_64-linux-android ;;
    x86) arch=i686; triple=i686-linux-android ;;
    arm64-v8a) arch=aarch64; triple=aarch64-linux-android ;;
    armeabi-v7a) arch=arm; triple=arm-linux-androideabi ;;
    riscv64) arch=riscv64; triple=riscv64-linux-android; api=35 ;;
    *) printf 'Unsupported target ABI %s\n' "$abi" >&2; exit 2 ;;
esac
target=${triple}${api}
if [[ "$abi" == armeabi-v7a ]]; then
    target=armv7a-linux-androideabi$api
fi

work="$pilot/build/runtimes/$abi"
install="$pilot/stage/runtimes/$abi"
resource="$("$llvm/bin/clang" --print-resource-dir)/lib/linux"
unwind_arch=${arch/i686/i386}
mkdir -p "$resource/$unwind_arch" "$install"
if [[ ! -e "$sysroot/usr/lib/$triple/$api/libc.so" ]]; then
    printf 'Build the target sysroot before the runtimes.\n' >&2
    exit 1
fi

common=(
    -G Ninja
    -DCMAKE_BUILD_TYPE=Release
    -DCMAKE_SYSTEM_NAME=Android
    -DCMAKE_SYSTEM_VERSION=1
    -DCMAKE_SYSTEM_PROCESSOR="$arch"
    -DCMAKE_SYSROOT="$sysroot"
    -DCMAKE_C_COMPILER="$llvm/bin/clang"
    -DCMAKE_CXX_COMPILER="$llvm/bin/clang++"
    -DCMAKE_ASM_COMPILER="$llvm/bin/clang"
    -DCMAKE_C_COMPILER_TARGET="$target"
    -DCMAKE_CXX_COMPILER_TARGET="$target"
    -DCMAKE_ASM_COMPILER_TARGET="$target"
    -DCMAKE_AR="$llvm/bin/llvm-ar"
    -DCMAKE_RANLIB="$llvm/bin/llvm-ranlib"
    -DCMAKE_STRIP="$llvm/bin/llvm-strip"
    -DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=ONLY
    -DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=ONLY
    -DCMAKE_FIND_ROOT_PATH_MODE_PACKAGE=ONLY
    -DCMAKE_FIND_ROOT_PATH_MODE_PROGRAM=NEVER
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON
    -DANDROID=1
    -DANDROID_PLATFORM_LEVEL="$api"
    -DLLVM_INCLUDE_TESTS=OFF
)
cmake -S "$sources/compiler-rt/lib/builtins" -B "$work/builtins" "${common[@]}" \
    -DCMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY \
    -DCOMPILER_RT_DEFAULT_TARGET_ONLY=ON \
    -DCOMPILER_RT_BUILTINS_HIDE_SYMBOLS=ON \
    -DCOMPILER_RT_EXCLUDE_ATOMIC_BUILTIN=OFF \
    -DCOMPILER_RT_OS_DIR=linux
cmake --build "$work/builtins" --parallel "$jobs"
builtins_name="libclang_rt.builtins-$arch-android.a"
install -m 644 "$work/builtins/lib/linux/$builtins_name" "$resource/$builtins_name"

unwind_flags="-rtlib=compiler-rt -unwindlib=none -nostdlib++"
cmake -S "$sources/runtimes" -B "$work/unwind" "${common[@]}" \
    -DCMAKE_C_FLAGS=-D_LIBUNWIND_USE_DLADDR=0 \
    -DCMAKE_CXX_FLAGS=-D_LIBUNWIND_USE_DLADDR=0 \
    -DCMAKE_EXE_LINKER_FLAGS="$unwind_flags" \
    -DLLVM_ENABLE_RUNTIMES=libunwind \
    -DLIBUNWIND_TARGET_TRIPLE="$target" \
    -DLIBUNWIND_USE_COMPILER_RT=ON \
    -DLIBUNWIND_ENABLE_SHARED=OFF \
    -DLIBUNWIND_HIDE_SYMBOLS=ON \
    -DLIBUNWIND_USE_FRAME_HEADER_CACHE=OFF \
    -DLIBUNWIND_INCLUDE_TESTS=OFF
cmake --build "$work/unwind" --parallel "$jobs"
install -m 644 "$work/unwind/lib/libunwind.a" "$resource/$unwind_arch/libunwind.a"

cxx_flags="-rtlib=compiler-rt -unwindlib=libunwind -nostdlib++"
cmake -S "$sources/runtimes" -B "$work/cxx" "${common[@]}" \
    -DCMAKE_INSTALL_PREFIX="$install" \
    -DCMAKE_INSTALL_LIBDIR=lib \
    -DANDROID_NATIVE_API_LEVEL="$api" \
    -DCMAKE_CXX_FLAGS=-DHAS_THREAD_LOCAL \
    -DCMAKE_EXE_LINKER_FLAGS="$cxx_flags" \
    -DCMAKE_SHARED_LINKER_FLAGS="$cxx_flags" \
    -DLLVM_ENABLE_RUNTIMES='libcxx;libcxxabi' \
    -DLIBCXXABI_ENABLE_SHARED=OFF \
    -DLIBCXXABI_USE_LLVM_UNWINDER=OFF \
    -DLIBCXXABI_USE_COMPILER_RT=ON \
    -DLIBCXXABI_INCLUDE_TESTS=OFF \
    -DLIBCXX_USE_COMPILER_RT=ON \
    -DLIBCXX_ABI_NAMESPACE=__ndk1 \
    -DLIBCXX_ENABLE_ABI_LINKER_SCRIPT=OFF \
    -DLIBCXX_ENABLE_STATIC_ABI_LIBRARY=ON \
    -DLIBCXX_STATICALLY_LINK_ABI_IN_SHARED_LIBRARY=ON \
    -DLIBCXX_STATICALLY_LINK_ABI_IN_STATIC_LIBRARY=OFF \
    -DLIBCXX_STATIC_OUTPUT_NAME=c++_static \
    -DLIBCXX_SHARED_OUTPUT_NAME=c++_shared \
    -DLIBCXX_INCLUDE_TESTS=OFF
cmake --build "$work/cxx" --parallel "$jobs"
cmake --install "$work/cxx" --strip
