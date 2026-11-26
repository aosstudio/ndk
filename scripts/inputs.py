import argparse
from concurrent.futures import ThreadPoolExecutor
from base64 import b64decode
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
from tempfile import TemporaryDirectory
from typing import TypedDict

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "out/inputs"
SOURCES = ROOT / "locks/sources.json"


class Input(TypedDict, total=False):
    url: str
    sha256: str
    tree_sha256: str
    license: str
    encoding: str


class SourceLock(TypedDict):
    revision: str
    release_name: str
    host: str
    abis: list[str]
    release_manifest: str
    llvm_android_revision: str
    archives: dict[str, Input]
    patches: dict[str, Input]
    files: dict[str, Input]


def read_lock() -> SourceLock:
    result: SourceLock = json.loads(SOURCES.read_text(encoding="utf-8"))
    return result


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def tree_sha256(path: Path) -> str:
    # Gitiles repacks archives with variable tar and gzip metadata.
    members: dict[str, tuple[str, int, str]] = {}
    with tarfile.open(path) as archive:
        for member in archive:
            if member.isdir():
                continue
            name = member.name.removeprefix("./")
            if name in members:
                raise ValueError(f"Repeated path in {path.name}: {name}")
            if member.isfile():
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(f"Unreadable member: {name}")
                with source:
                    content = hashlib.sha256(source.read()).hexdigest()
            elif member.issym():
                content = member.linkname
            else:
                raise ValueError(f"Unexpected source archive member: {name}")
            members[name] = (member.type.decode("ascii"), member.mode & 0o777, content)
    return hashlib.sha256(
        json.dumps(members, sort_keys=True).encode("utf-8")
    ).hexdigest()


def download(url: str, path: Path) -> None:
    subprocess.run(
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--location",
            "--retry",
            "3",
            "--connect-timeout",
            "30",
            "--proto",
            "=https",
            "--proto-redir",
            "=https",
            "--output",
            str(path),
            url,
        ],
        check=True,
    )


def fetch(destination: Path, entry: Input) -> None:
    if "tree_sha256" in entry:
        digest, expected = tree_sha256, entry["tree_sha256"]
    else:
        digest, expected = sha256, entry["sha256"]
    if destination.is_file() and digest(destination) == expected:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".fetch-", dir=destination.parent) as work:
        candidate = Path(work) / "download"
        download(entry["url"], candidate)
        if entry.get("encoding") == "base64":
            candidate.write_bytes(b64decode(candidate.read_bytes()))
        if digest(candidate) != expected:
            raise ValueError(f"SHA-256 mismatch for {entry['url']}")
        candidate.replace(destination)
        print(f"Fetched {destination.name}", flush=True)


def fetch_all(downloads: list[tuple[Path, Input]]) -> None:
    with ThreadPoolExecutor(max_workers=8) as executor:
        for job in [executor.submit(fetch, dest, spec) for dest, spec in downloads]:
            job.result()


def source_member(member: tarfile.TarInfo, target: str) -> tarfile.TarInfo | None:
    # Bionic's formatting configuration references a separate AOSP project.
    if (
        member.name == ".clang-format"
        and member.linkname == "../system/core/.clang-format-2"
    ):
        return None
    return tarfile.data_filter(member, target)


def sources(directory: Path | None) -> None:
    lock = read_lock()
    fetch_all(
        [
            (CACHE / group / name, entry)
            for group, entries in (
                ("archives", lock["archives"]),
                ("patches", lock["patches"]),
                ("files", lock["files"]),
            )
            for name, entry in entries.items()
        ]
    )
    if directory is None:
        return
    source = directory / "source"
    source.mkdir()
    for filename, target in (
        ("llvm-project.tar.gz", source),
        ("ndk.tar.gz", source / "ndk"),
        ("bionic.tar.gz", source / "bionic"),
        ("r28c-headers.tar.gz", source / "r28c-public-headers"),
    ):
        with tarfile.open(CACHE / "archives" / filename) as archive:
            archive.extractall(target, filter=source_member)
    (llvm,) = source.glob("llvm-project-*")
    llvm.rename(source / "llvm-project")
    shutil.copytree(
        CACHE / "files", directory / "intake", ignore=shutil.ignore_patterns(".fetch-*")
    )
    for filename in lock["patches"]:
        print(f"Applying {filename}", flush=True)
        subprocess.run(
            [
                "patch",
                "--batch",
                "--forward",
                "-p1",
                "-i",
                str(CACHE / "patches" / filename),
            ],
            cwd=source / "llvm-project",
            check=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch and stage the pinned r28c sources."
    )
    parser.add_argument("--stage", type=Path)
    args = parser.parse_args()
    sources(args.stage)


if __name__ == "__main__":
    main()
