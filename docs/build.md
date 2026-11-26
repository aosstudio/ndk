# Build the Linux x86-64 NDK

Use an Ubuntu 26.04 x86-64 host with a disk-backed checkout. The build needs Python 3.13 or later, Clang 21, the host C/C++ development files, CMake, Ninja, curl, jq, patch, zlib and zstd development packages. Allow about 12 GiB of free disk space for the build and separate space for verification.

```sh
sudo apt-get install build-essential clang-21 cmake curl jq libzstd-dev ninja-build patch zlib1g-dev
make build JOBS="$(nproc)"
```

The build downloads its pinned inputs to `out/inputs/`. Run `make fetch` to cache them independently. The source lock records SHA-256 for files and stable archives. For Gitiles generated archives it records a digest over file paths, permissions, contents and symlink targets. The compiler, linker, runtime libraries and link stubs are built locally. The public sysroot headers come from the pinned upstream header tree. License notices and the source lock travel with the ZIP.

The output is `out/dist/aosstudio-ndk-28.2.13676358-linux-x86_64.zip`. Intermediate files stay in the work directory printed by the build.

## Verify the ZIP

Install Bubblewrap with user namespaces available, along with CMake, Ninja and GNU Make. Run:

```sh
make verify
```

Verification adds the ZIP to a scratch SDK repository and installs it in the standard side-by-side NDK directory. It builds CMake fixtures for all packaged ABIs with both shared and static libc++, then builds an x86-64 JNI APK with AGP and `ndk-build` inside a network-isolated sandbox. The APK check covers its v2 signature, JNI symbol, C++ runtime, and 16 KiB page alignment. Verification uses the published AOSStudio SDK, JDK and AGP Nightlies. Remove `out/verify-inputs/` to refresh the cached Nightlies.

## Package coverage

The supported native API range is 21–35 for `armeabi-v7a`, `arm64-v8a`, `x86` and `x86_64`. Experimental `riscv64` targets API 35. The `ndk-build` launcher uses the host's GNU Make. This package focuses on C/C++ application builds and excludes LLDB, sanitizer runtimes and the API-specific static platform archives.
