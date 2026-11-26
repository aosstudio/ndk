#!/usr/bin/env python3
import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from inputs import read_lock

PILOT = Path(os.environ["NDK_WORK"])
INTAKE = PILOT / "intake"
BIONIC = PILOT / "source/bionic"
NDK = PILOT / "source/ndk"
SYSROOT = PILOT / "stage/sysroot"
GENERATED = PILOT / "build/sysroot"
NDK_REVISION = read_lock()["revision"]
CORE_MAPS = {
    "libc": BIONIC / "libc/libc.map.txt",
    "libdl": BIONIC / "libdl/libdl.map.txt",
    "libm": BIONIC / "libm/libm.map.txt",
    "libstdc++": BIONIC / "libc/libstdc++.map.txt",
}
PLATFORM_FIRST_API = {
    "libEGL": 9,
    "libGLESv1_CM": 9,
    "libGLESv2": 9,
    "libGLESv3": 18,
    "libOpenMAXAL": 14,
    "libOpenSLES": 9,
    "libaaudio": 26,
    "libamidi": 29,
    "libandroid": 9,
    "libbinder_ndk": 29,
    "libcamera2ndk": 24,
    "libicu": 31,
    "libjnigraphics": 9,
    "liblog": 9,
    "libmediandk": 21,
    "libnativehelper": 31,
    "libnativewindow": 26,
    "libneuralnetworks": 27,
    "libsync": 26,
    "libvulkan": 24,
    "libz": 9,
}
UNVERSIONED = {
    "libEGL",
    "libGLESv1_CM",
    "libGLESv2",
    "libGLESv3",
    "libOpenMAXAL",
    "libOpenSLES",
    "libaaudio",
    "libandroid",
    "libcamera2ndk",
    "libicu",
    "libjnigraphics",
    "liblog",
    "libmediandk",
    "libvulkan",
    "libz",
}


@dataclass(frozen=True)
class Target:
    abi: str
    arch: str
    triple: str
    api: int

    @property
    def clang_target(self) -> str:
        triple = (
            "armv7a-linux-androideabi" if self.abi == "armeabi-v7a" else self.triple
        )
        return f"{triple}{self.api}"

    @property
    def directory(self) -> Path:
        return Path(self.triple) / str(self.api)

    def libraries(self) -> dict[str, Path]:
        maps = {
            name: INTAKE / "platform" / f"{name}.map.txt"
            for name, api in PLATFORM_FIRST_API.items()
            if self.api >= api
        }
        return {**CORE_MAPS, **maps}


def installed_targets() -> tuple[list[Target], dict[str, int]]:
    with (NDK / "meta/platforms.json").open() as source:
        platforms = json.load(source)
    with (NDK / "meta/abis.json").open() as source:
        abis = json.load(source)
    targets = [
        Target(abi, info["arch"], info["triple"], api)
        for abi, info in abis.items()
        for api in range(
            platforms["max"] if abi == "riscv64" else platforms["min"],
            platforms["max"] + 1,
        )
    ]
    return targets, platforms["aliases"]


def install_headers() -> None:
    include = SYSROOT / "usr/include"
    shutil.copytree(PILOT / "source/r28c-public-headers", include, dirs_exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from pathlib import Path; from ndk import config; "
            "from ndk.ndkversionheadergenerator import NdkVersionHeaderGenerator; "
            "NdkVersionHeaderGenerator(config.major, config.hotfix, config.beta, "
            "int(sys.argv[1]), config.canary).write(Path(sys.argv[2]))",
            NDK_REVISION.split(".")[-1],
            str(include / "android/ndk-version.h"),
        ],
        cwd=NDK,
        check=True,
    )


def generate_stubs(targets: list[Target], api_map: dict[str, int]) -> None:
    GENERATED.mkdir(parents=True, exist_ok=True)
    map_file = GENERATED / "api-levels.json"
    map_file.write_text(json.dumps(api_map))
    for target in targets:
        work = GENERATED / target.directory
        work.mkdir(parents=True, exist_ok=True)
        for name, symbol_map in target.libraries().items():
            subprocess.run(
                [
                    sys.executable,
                    str(INTAKE / "ndkstubgen.py"),
                    f"--api={target.api}",
                    f"--arch={target.arch}",
                    "--api-map",
                    str(map_file),
                    str(symbol_map),
                    str(work / f"{name}.c"),
                    str(work / f"{name}.versions"),
                    str(work / f"{name}.symbols"),
                ],
                check=True,
            )


def link_stubs(targets: list[Target], clang: Path) -> None:
    for target in targets:
        work = GENERATED / target.directory
        install = SYSROOT / "usr/lib" / target.directory
        install.mkdir(parents=True, exist_ok=True)
        for name in target.libraries():
            version = work / f"{name}.versions"
            # AOSP leaves liblog unversioned through the finalized API levels.
            version_flags = (
                []
                if name in UNVERSIONED or not version.stat().st_size
                else [f"-Wl,--version-script,{version}"]
            )
            subprocess.run(
                [
                    str(clang),
                    f"--target={target.clang_target}",
                    "-shared",
                    "-nostdlib",
                    "-fPIC",
                    "-fno-builtin",
                    "-fno-stack-protector",
                    "-fno-unwind-tables",
                    "-fno-asynchronous-unwind-tables",
                    f"-Wl,-soname,{name}.so",
                    *version_flags,
                    str(work / f"{name}.c"),
                    "-o",
                    str(install / f"{name}.so"),
                ],
                check=True,
            )

        subprocess.run(
            [
                str(clang),
                f"--target={target.clang_target}",
                "-I",
                str(BIONIC / "libc"),
                "-c",
                str(BIONIC / "libc/arch-common/bionic/crt_pad_segment.S"),
                "-o",
                str(install / "crt_pad_segment.o"),
            ],
            check=True,
        )


def install_crt(llvm: Path) -> None:
    work = GENERATED / "crt"
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from pathlib import Path; "
            "from ndk.crtobjectbuilder import CrtObjectBuilder; "
            "CrtObjectBuilder.PREBUILTS_PATH = Path(sys.argv[3]).parent; "
            "CrtObjectBuilder(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[4])).build()",
            str(llvm),
            str(work),
            str(SYSROOT),
            NDK_REVISION.split(".")[-1],
        ],
        cwd=NDK,
        check=True,
    )
    shutil.copytree(work, SYSROOT / "usr/lib", dirs_exist_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the NDK sysroot.")
    parser.add_argument(
        "--llvm", type=Path, help="Prefix of the source-built host LLVM"
    )
    args = parser.parse_args()
    os.environ["TMPDIR"] = str(PILOT / "tmp")
    targets, api_map = installed_targets()
    if args.llvm:
        llvm = args.llvm.resolve(strict=True)
        link_stubs(targets, llvm / "bin/clang")
        install_crt(llvm)
        print(
            f"Built sysroot libraries and startup objects for {len(targets)} target/API pairs"
        )
    else:
        install_headers()
        generate_stubs(targets, api_map)
        print(f"Generated headers and stub sources for {len(targets)} target/API pairs")


if __name__ == "__main__":
    main()
