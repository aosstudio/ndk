#!/usr/bin/env bash
set -euo pipefail

cd "${NDK_WORK:?Run make build from the repository root}"
pilot=$PWD
case "$(findmnt --noheadings --output FSTYPE --target "$pilot")" in
    tmpfs | ramfs)
        printf 'Use a disk-backed directory for the NDK build.\n' >&2
        exit 1
        ;;
esac

mkdir -p "$pilot/tmp" "$pilot/home" "$pilot/logs"
export TMPDIR="$pilot/tmp"
cc=${CC:-/usr/bin/clang-21}
cxx=${CXX:-/usr/bin/clang++-21}
jobs=${JOBS:-10}
build="$pilot/build/host-llvm"
prefix="$pilot/stage/host-llvm"

cmake -S "$pilot/source/llvm-project/llvm" -B "$build" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_COMPILER="$cc" \
    -DCMAKE_CXX_COMPILER="$cxx" \
    -DCMAKE_INSTALL_PREFIX="$prefix" \
    -DCLANG_VENDOR=AOSStudio \
    -DLLVM_ENABLE_PROJECTS='clang;lld' \
    -DLLVM_TARGETS_TO_BUILD='AArch64;ARM;BPF;RISCV;WebAssembly;X86' \
    -DLLVM_PARALLEL_LINK_JOBS=1 \
    -DLLVM_INCLUDE_TESTS=OFF \
    -DLLVM_INCLUDE_EXAMPLES=OFF \
    -DLLVM_INCLUDE_BENCHMARKS=OFF \
    -DLLVM_INCLUDE_DOCS=OFF \
    -DLLVM_ENABLE_TERMINFO=OFF \
    -DLLVM_ENABLE_LIBEDIT=OFF \
    -DLLVM_ENABLE_LIBXML2=OFF \
    -DLLVM_ENABLE_ZLIB=FORCE_ON \
    -DLLVM_ENABLE_ZSTD=FORCE_ON \
    -DLLVM_ENABLE_CURL=OFF \
    -DCLANG_INCLUDE_TESTS=OFF \
    -DCLANG_ENABLE_ARCMT=OFF \
    -DCLANG_ENABLE_STATIC_ANALYZER=OFF \
    -DCLANG_DEFAULT_LINKER=lld \
    -DLLVM_INSTALL_TOOLCHAIN_ONLY=ON \
    -DCMAKE_CXX_FLAGS="-include cstdint -ffile-prefix-map=$pilot=/usr/src/aosstudio-ndk" \
    -DCMAKE_C_FLAGS="-ffile-prefix-map=$pilot=/usr/src/aosstudio-ndk"

cmake --build "$build" --parallel "$jobs" --target \
    clang lld llvm-ar llvm-cov llvm-dwp llvm-nm llvm-objcopy llvm-objdump \
    llvm-profdata llvm-readobj llvm-size llvm-strings llvm-symbolizer \
    clang-resource-headers

for component in \
    clang lld llvm-ar llvm-cov llvm-dwp llvm-nm llvm-objcopy llvm-objdump \
    llvm-profdata llvm-readobj llvm-size llvm-strings llvm-symbolizer \
    llvm-ranlib llvm-strip clang-resource-headers; do
    cmake --install "$build" --strip --component "$component"
done
for pair in llvm-readobj:llvm-readelf llvm-symbolizer:llvm-addr2line ld.lld:ld; do
    ln -sfn "${pair%:*}" "$prefix/bin/${pair#*:}"
done
"$prefix/bin/clang" --version
"$prefix/bin/ld.lld" --version
