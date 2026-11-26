import hashlib
from importlib import import_module
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory, mkdtemp
from typing import TypedDict
from xml.etree import ElementTree as ET
from zipfile import ZIP_STORED, ZipFile

from inputs import ROOT, Input, download, fetch, fetch_all, read_lock, sha256

OUT = ROOT / "out"
CACHE = OUT / "verify-inputs"
MACHINES = {
    "armeabi-v7a": "ARM",
    "arm64-v8a": "AArch64",
    "x86": "Intel 80386",
    "x86_64": "Advanced Micro Devices X86-64",
    "riscv64": "RISC-V",
}
GRADLE_VERIFICATION = "https://schema.gradle.org/dependency-verification"


class VerificationLock(TypedDict):
    sdk_source: Input
    sdk: str
    jdk: str
    agp: str


def nightly(repo: str, filename: str) -> Path:
    asset = CACHE / filename
    if not asset.is_file():
        with TemporaryDirectory(dir=CACHE) as work:
            candidate = Path(work) / filename
            download(
                f"https://github.com/aosstudio/{repo}/releases/download/latest/{filename}",
                candidate,
            )
            candidate.replace(asset)
    return asset


def add_ndk_to_feed(feed: Path, archive: Path, revision: str) -> None:
    (feed / archive.name).hardlink_to(archive)
    with archive.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha1").hexdigest()
    xsi = "http://www.w3.org/2001/XMLSchema-instance"
    ET.register_namespace("xsi", xsi)
    for index in sorted(feed.glob("repository2-*.xml")):
        tree = ET.parse(index)
        root = tree.getroot()
        ET.register_namespace("s", root.tag.partition("}")[0][1:])
        root.set("xmlns:g", "http://schemas.android.com/repository/android/generic/02")
        path = f"ndk;{revision}"
        existing = root.find(f"./remotePackage[@path='{path}']")
        if existing is not None:
            root.remove(existing)
        item = ET.SubElement(root, "remotePackage", path=path)
        ET.SubElement(item, "type-details", {f"{{{xsi}}}type": "g:genericDetailsType"})
        version = ET.SubElement(item, "revision")
        for name, part in zip(
            ("major", "minor", "micro"), revision.split("."), strict=True
        ):
            ET.SubElement(version, name).text = part
        ET.SubElement(item, "display-name").text = f"AOSStudio NDK {revision}"
        choice = ET.SubElement(ET.SubElement(item, "archives"), "archive")
        complete = ET.SubElement(choice, "complete")
        ET.SubElement(complete, "size").text = str(archive.stat().st_size)
        ET.SubElement(complete, "checksum", type="sha1").text = checksum
        ET.SubElement(complete, "url").text = archive.name
        ET.SubElement(choice, "host-os").text = "linux"
        ET.SubElement(choice, "host-arch").text = "x64"
        tree.write(index, encoding="utf-8", xml_declaration=True)


def elf_machine(elf: str) -> str:
    return next(
        (
            line.partition("Machine:")[2].strip()
            for line in elf.splitlines()
            if "Machine:" in line
        ),
        "",
    )


def verify_cmake(work: Path, ndk: Path, readelf: Path, abis: list[str]) -> None:
    source = ROOT / "tests/jni-app/src/main/jni"
    for abi in abis:
        api = "35" if abi == "riscv64" else "24"
        for stl in ("c++_shared", "c++_static"):
            directory = work / f"{abi}-{stl}"
            subprocess.run(
                [
                    "cmake",
                    "-G",
                    "Ninja",
                    "-S",
                    source,
                    "-B",
                    directory,
                    f"-DCMAKE_TOOLCHAIN_FILE={ndk}/build/cmake/android.toolchain.cmake",
                    f"-DANDROID_ABI={abi}",
                    f"-DANDROID_PLATFORM=android-{api}",
                    f"-DANDROID_STL={stl}",
                ],
                check=True,
            )
            subprocess.run(["cmake", "--build", directory], check=True)
            elf = subprocess.check_output(
                [
                    readelf,
                    "--file-header",
                    "--dynamic-table",
                    directory / "libgreeting.so",
                ],
                text=True,
            )
            (directory / "elf.txt").write_text(elf)
            machine = elf_machine(elf)
            shared = "Shared library: [libc++_shared.so]" in elf
            if machine != MACHINES[abi] or shared != (stl == "c++_shared"):
                raise ValueError(f"Unexpected CMake ELF for {abi} and {stl}: {machine}")


def update_agp_metadata(metadata: Path, agp: Path) -> None:
    tree = ET.parse(metadata)
    namespace = {"g": GRADLE_VERIFICATION}
    for module in tree.findall("g:components/g:component", namespace):
        group, name, version = (
            module.attrib[key] for key in ("group", "name", "version")
        )
        if group != "com.android.tools.build" or name not in {"builder", "gradle"}:
            continue
        for artifact in module.findall("g:artifact", namespace):
            file = (
                agp / group.replace(".", "/") / name / version / artifact.attrib["name"]
            )
            for checksum in artifact.findall("g:sha256", namespace):
                checksum.set("value", sha256(file))
                checksum.set("origin", "AOSStudio AGP Nightly for this run")
    ET.register_namespace("", GRADLE_VERIFICATION)
    tree.write(metadata, encoding="utf-8", xml_declaration=True)


def verify_apk(
    work: Path, source: Path, sdk: Path, readelf: Path, gradle: Path
) -> None:
    project = work / "project"
    project.mkdir()
    fixture = source / "examples/built-in"
    shutil.copyfile(fixture / "build.gradle", project / "build.gradle")
    shutil.copytree(fixture / "gradle", project / "gradle")
    shutil.copytree(ROOT / "tests/jni-app", project / "jni-app")
    (project / "settings.gradle").write_text("include 'jni-app'\n")
    update_agp_metadata(
        project / "gradle/verification-metadata.xml", work / "maven/agp"
    )
    for name in ("tmp", "home", "gradle-cache"):
        (work / name).mkdir()
    sys.path.insert(0, str(source / "scripts"))
    runtime = import_module("sdk_runtime")
    sandbox, java_home = runtime.sandbox(None, work / "jdk")
    environment = {
        "JAVA_HOME": java_home,
        "PATH": f"{java_home}/bin:/usr/bin:/bin",
        "HOME": "/mnt/output/home",
        "ANDROID_HOME": "/mnt/sdk",
        "ANDROID_USER_HOME": "/mnt/output/home/.android",
        "GRADLE_USER_HOME": "/mnt/output/gradle-cache",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
    }
    isolated = [
        *sandbox,
        "--ro-bind-try",
        "/etc/alternatives",
        "/etc/alternatives",
        "--ro-bind",
        str(sdk),
        "/mnt/sdk",
        "--ro-bind",
        str(work / "maven"),
        "/mnt/inputs",
        "--ro-bind",
        str(CACHE / "maven/dependencies"),
        "/mnt/inputs/dependencies",
        "--ro-bind",
        str(gradle),
        "/mnt/gradle",
        "--bind",
        str(work),
        "/mnt/output",
        "--bind",
        str(work / "tmp"),
        "/tmp",
        *(
            arg
            for key, value in environment.items()
            for arg in ("--setenv", key, value)
        ),
        "--chdir",
        "/mnt/output/project",
        "--",
    ]
    build_tools = "/mnt/sdk/build-tools/36.0.0"
    subprocess.run(
        [
            *isolated,
            "/mnt/gradle/bin/gradle",
            "--offline",
            "--no-daemon",
            "--no-build-cache",
            "--dependency-verification",
            "strict",
            "-Dorg.gradle.vfs.watch=false",
            "-Duser.home=/mnt/output/home",
            f"-Pandroid.aapt2FromMavenOverride={build_tools}/aapt2",
            "-Pandroid.builder.sdkDownload=false",
            ":jni-app:assembleDebug",
        ],
        check=True,
    )
    apk = "jni-app/build/outputs/apk/debug/jni-app-debug.apk"
    signature = subprocess.check_output(
        [
            *isolated,
            f"{build_tools}/apksigner",
            "verify",
            "--verbose",
            "--print-certs",
            apk,
        ],
        text=True,
    )
    (work / "signature.log").write_text(signature)
    if "Verified using v2 scheme (APK Signature Scheme v2): true" not in signature:
        raise ValueError("Expected APK Signature Scheme v2")
    subprocess.run(
        [*isolated, f"{build_tools}/zipalign", "-c", "-P", "16", "4", apk], check=True
    )
    with ZipFile(project / apk) as archive:
        libraries = sorted(
            name
            for name in archive.namelist()
            if name.startswith("lib/") and name.endswith(".so")
        )
        if "classes.dex" not in archive.namelist() or libraries != [
            "lib/x86_64/libc++_shared.so",
            "lib/x86_64/libgreeting.so",
        ]:
            raise ValueError(f"Unexpected JNI APK contents: {libraries}")
        for name in libraries:
            if archive.getinfo(name).compress_type != ZIP_STORED:
                raise ValueError(f"Expected an uncompressed native library: {name}")
            path = work / Path(name).name
            path.write_bytes(archive.read(name))
            elf = subprocess.check_output(
                [
                    readelf,
                    "--file-header",
                    "--program-headers",
                    "--dynamic-table",
                    "--dyn-syms",
                    "--version-info",
                    "--wide",
                    path,
                ],
                text=True,
            )
            path.with_suffix(".elf.txt").write_text(elf)
            loads = [
                line.split()[-1]
                for line in elf.splitlines()
                if line.strip().startswith("LOAD ")
            ]
            if (
                elf_machine(elf) != MACHINES["x86_64"]
                or set(loads) != {"0x4000"}
                or "GLIBC_" in elf
                or "[libc.so.6]" in elf
            ):
                raise ValueError(f"Unexpected Android native library layout: {name}")
            if path.name == "libgreeting.so" and (
                "Java_demo_jniapp_MainActivity_message" not in elf
                or "Shared library: [libc++_shared.so]" not in elf
            ):
                raise ValueError("Expected a JNI export using libc++_shared")
    print(f"Verified JNI APK: {project / apk}", flush=True)


def main() -> None:
    lock = read_lock()
    revision, host = lock["revision"], lock["host"]
    archive = OUT / "dist" / f"aosstudio-ndk-{revision}-{host}.zip"
    if not archive.is_file():
        raise FileNotFoundError(f"Run make build before verification: {archive}")
    work = Path(mkdtemp(prefix="verify.", dir=OUT))
    print(f"Verification directory: {work}", flush=True)
    records: VerificationLock = json.loads(
        (ROOT / "locks/verification.json").read_text()
    )
    sdk_archive = CACHE / "sdk-source.tar.gz"
    fetch(sdk_archive, records["sdk_source"])
    with tarfile.open(sdk_archive) as bundle:
        bundle.extractall(work, filter="data")
    (source,) = work.glob("sdk-*")
    for repo, filename in (
        ("sdk", records["sdk"]),
        ("jdk", records["jdk"]),
        ("agp", records["agp"]),
    ):
        with tarfile.open(nightly(repo, filename)) as bundle:
            bundle.extractall(work / repo, filter="data")
    sdk_lock = json.loads((source / "locks/release.json").read_text())
    components = sdk_lock["components"]
    feed = work / "sdk/feed"
    add_ndk_to_feed(feed, archive, revision)
    with ZipFile(feed / components["cmdline-tools"]["name"]) as bundle:
        bundle.extractall(work / "bootstrap")
    manager = work / "bootstrap/cmdline-tools"
    (manager / "bin/sdkmanager").chmod(0o755)
    sdk = work / "installed"
    subprocess.run(
        [
            sys.executable,
            source / "scripts/install_sdk.py",
            "--feed",
            feed,
            "--destination",
            sdk,
            "--jdk",
            work / "jdk",
            "--manager",
            manager,
            "--",
            *sdk_lock["default_packages"],
            f"ndk;{revision}",
        ],
        check=True,
    )
    ndk = sdk / "ndk" / revision
    readelf = ndk / "toolchains/llvm/prebuilt" / host / "bin/llvm-readelf"
    if not (ndk / "package.xml").is_file():
        raise ValueError(f"SDK Manager did not register NDK {revision}")
    print(f"SDK Manager installed NDK {revision}", flush=True)
    verify_cmake(work, ndk, readelf, lock["abis"])

    maven = CACHE / "maven"
    dependencies: dict[str, Input] = json.loads(
        (source / "locks/verification.json").read_text()
    )
    fetch_all(
        [(maven / "dependencies" / name, entry) for name, entry in dependencies.items()]
    )
    inputs = work / "maven"
    (inputs / "dependencies").mkdir(parents=True)
    (agp,) = (work / "agp").glob("*/maven")
    agp.rename(inputs / "agp")
    gradle_inputs = components["gradle"]
    gradle_archive = CACHE / gradle_inputs["name"]
    fetch(gradle_archive, gradle_inputs)
    with ZipFile(gradle_archive) as bundle:
        bundle.extractall(work)
        for member in bundle.infolist():
            mode = (member.external_attr >> 16) & 0o777
            if mode and not member.is_dir():
                (work / member.filename).chmod(mode)
    (gradle,) = (folder for folder in work.glob("gradle-*") if folder.is_dir())
    verify_apk(work, source, sdk, readelf, gradle)


if __name__ == "__main__":
    main()
