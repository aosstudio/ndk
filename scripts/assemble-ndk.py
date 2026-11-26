#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import TypedDict

from inputs import read_lock

WORK = Path(os.environ["NDK_WORK"])
STAGE = WORK / "stage"
SOURCE = WORK / "source/ndk"
LOCK = read_lock()
REVISION = LOCK["revision"]
HOST = LOCK["host"]


class AbiInfo(TypedDict):
    bitness: int
    default: bool
    deprecated: bool
    proc: str
    arch: str
    triple: str
    llvm_triple: str
    min_os_version: int


def install_metadata(
    ndk: Path, abis: dict[str, AbiInfo], platforms: dict[str, int | dict[str, int]]
) -> None:
    meta = ndk / "meta"
    meta.mkdir()
    (meta / "abis.json").write_text(json.dumps(abis, indent=2) + "\n")
    shutil.copyfile(SOURCE / "meta/platforms.json", meta / "platforms.json")
    first_api: dict[str, int] = {}
    sysroot = ndk / "toolchains/llvm/prebuilt" / HOST / "sysroot/usr/lib"
    for library in sorted(sysroot.glob("*/*/*.so")):
        if library.name != "libc++.so":
            api = int(library.parent.name)
            first_api[library.name] = min(first_api.get(library.name, api), api)
    (meta / "system_libs.json").write_text(json.dumps(first_api, indent=2) + "\n")
    aliases = platforms["aliases"]
    assert isinstance(aliases, dict)

    variables: dict[str, dict[str, str | int]] = {
        "abis": {
            "NDK_DEFAULT_ABIS": " ".join(
                a for a, info in abis.items() if info["default"]
            ),
            "NDK_DEPRECATED_ABIS": " ".join(
                a for a, info in abis.items() if info["deprecated"]
            ),
            "NDK_KNOWN_DEVICE_ABI32S": " ".join(
                a for a, info in abis.items() if info["bitness"] == 32
            ),
            "NDK_KNOWN_DEVICE_ABI64S": " ".join(
                a for a, info in abis.items() if info["bitness"] == 64
            ),
            "NDK_KNOWN_DEVICE_ABIS": " ".join(abis),
        },
        "platforms": {
            "NDK_MIN_PLATFORM_LEVEL": str(platforms["min"]),
            "NDK_MAX_PLATFORM_LEVEL": str(platforms["max"]),
            **{
                f"NDK_PLATFORM_ALIAS_{alias}": f"android-{api}"
                for alias, api in aliases.items()
            },
        },
        "system_libs": {"NDK_SYSTEM_LIBS": " ".join(sorted(first_api))},
    }
    for abi, info in abis.items():
        for field in ("proc", "arch", "triple", "llvm_triple", "min_os_version"):
            variables["abis"][f"NDK_ABI_{abi}_{field.upper()}"] = str(info[field])
        variables["abis"][f"NDK_PROC_{info['proc']}_ABI"] = abi
        variables["abis"][f"NDK_ARCH_{info['arch']}_ABI"] = abi

    for name, values in variables.items():
        (ndk / "build/core" / f"{name}.mk").write_text(
            "".join(f"{key} := {value}\n" for key, value in values.items())
        )
        (ndk / "build/cmake" / f"{name}.cmake").write_text(
            "".join(
                f'set({key} "{str(value).replace(" ", ";")}")\n'
                for key, value in values.items()
            )
        )


def assemble(ndk: Path, requested: list[str]) -> None:
    source_abis: dict[str, AbiInfo] = json.loads(
        (SOURCE / "meta/abis.json").read_text()
    )
    platforms = json.loads((SOURCE / "meta/platforms.json").read_text())
    abis = {abi: source_abis[abi] for abi in sorted(set(requested))}
    for abi, info in abis.items():
        info["min_os_version"] = (
            platforms["max"] if abi == "riscv64" else platforms["min"]
        )
        for file in ("libc++abi.a", "libc++_static.a", "libc++_shared.so"):
            if not (STAGE / "runtimes" / abi / "lib" / file).is_file():
                raise ValueError(f"Missing built runtime for {abi}: {file}")

    host = STAGE / "host-llvm"
    version = subprocess.check_output([host / "bin/clang", "--version"], text=True)
    clang_match = re.search(r"clang version (\d+\.\d+\.\d+)", version)
    if clang_match is None:
        raise ValueError(f"Cannot read Clang version from {version}")

    ndk.mkdir(parents=True, exist_ok=False)
    toolchain = ndk / "toolchains/llvm/prebuilt" / HOST
    shutil.copytree(host, toolchain)
    resource = Path(
        subprocess.check_output(
            [toolchain / "bin/clang", "--print-resource-dir"], text=True
        ).strip()
    )
    shutil.copytree(STAGE / "sysroot/usr/include", toolchain / "sysroot/usr/include")
    first_headers = STAGE / "runtimes" / next(iter(abis)) / "include/c++/v1"
    shutil.copytree(first_headers, toolchain / "sysroot/usr/include/c++/v1")

    for abi, info in abis.items():
        runtime_arch = {"arm64": "aarch64", "x86": "i386"}.get(
            info["arch"], info["arch"]
        )
        (resource / "lib/linux" / runtime_arch / "libatomic.a").write_text(
            "/* The atomic operations are provided by compiler-rt builtins. */\n"
        )
        triple = info["triple"]
        library = toolchain / "sysroot/usr/lib" / triple
        shutil.copytree(STAGE / "sysroot/usr/lib" / triple, library)
        runtime = STAGE / "runtimes" / abi
        if (runtime / "include/c++/v1/__config_site").read_bytes() != (
            first_headers / "__config_site"
        ).read_bytes():
            raise ValueError(f"libc++ configuration differs for {abi}")
        for name in (
            "libc++abi.a",
            "libc++_static.a",
            "libc++_shared.so",
            "libc++experimental.a",
        ):
            shutil.copy2(runtime / "lib" / name, library / name)
        for api in range(info["min_os_version"], platforms["max"] + 1):
            (library / str(api) / "libc++.a").write_text(
                "INPUT(-lc++_static -lc++abi)\n"
            )
            (library / str(api) / "libc++.so").write_text("INPUT(-lc++_shared)\n")
            target = "armv7a-linux-androideabi" if abi == "armeabi-v7a" else triple
            for clang in ("clang", "clang++"):
                wrapper = toolchain / "bin" / f"{target}{api}-{clang}"
                flag = " -mstackrealign" if abi == "x86" and api < 24 else ""
                wrapper.write_text(
                    "#!/bin/sh\n"
                    f'if [ "${{1-}}" = -cc1 ]; then exec "$(dirname "$0")/{clang}" "$@"; fi\n'
                    f'exec "$(dirname "$0")/{clang}" --target={target}{api}{flag} "$@"\n'
                )
                wrapper.chmod(0o755)

    shutil.copytree(SOURCE / "build", ndk / "build")
    wrapper = ndk / "ndk-build"
    wrapper.write_text('#!/bin/sh\nexec "$(dirname "$0")/build/ndk-build" "$@"\n')
    wrapper.chmod(0o755)
    major, minor, _ = REVISION.split(".")
    (ndk / "build/core/version.mk").write_text(
        f"NDK_MAJOR := {major}\nNDK_MINOR := {minor}\nNDK_BETA := 0\nNDK_CANARY := false\n"
    )
    (ndk / "build/cmake/compiler_id.cmake").write_text(
        "".join(
            f"set(CMAKE_{lang}_COMPILER_VERSION {clang_match[1]})\n"
            for lang in ("ASM", "C", "CXX")
        )
    )
    # The upstream CMake toolchain uses this exact prefix to parse the revision.
    (ndk / "source.properties").write_text(
        "Pkg.Desc = Android NDK\n"
        f"Pkg.Revision = {REVISION}\n"
        f"Pkg.BaseRevision = {REVISION}\n"
        f"Pkg.ReleaseName = {LOCK['release_name']}\n"
    )
    install_metadata(ndk, abis, platforms)
    notices = ndk / "licenses"
    notices.mkdir()
    for name, notice_path in {
        "ndk": SOURCE / "NOTICE",
        "bionic": WORK / "source/bionic/libc/NOTICE",
        "sysroot": WORK / "intake/sysroot-NOTICE",
        "llvm": WORK / "source/llvm-project/llvm/LICENSE.TXT",
        "compiler-rt": WORK / "source/llvm-project/compiler-rt/LICENSE.TXT",
        "libcxx": WORK / "source/llvm-project/libcxx/LICENSE.TXT",
        "libcxxabi": WORK / "source/llvm-project/libcxxabi/LICENSE.TXT",
        "libunwind": WORK / "source/llvm-project/libunwind/LICENSE.TXT",
        "liblog": WORK / "intake/liblog-NOTICE",
        "libnativehelper": WORK / "intake/nativehelper-NOTICE",
    }.items():
        shutil.copyfile(notice_path, notices / f"{name}.txt")


def main() -> None:
    parser = argparse.ArgumentParser(description="Assemble the source-built NDK.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("abis", nargs="+")
    args = parser.parse_args()
    assemble(args.output, args.abis)


if __name__ == "__main__":
    main()
