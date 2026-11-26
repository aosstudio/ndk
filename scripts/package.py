import argparse
from pathlib import Path
import shutil
import stat
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from inputs import ROOT, read_lock


def package(ndk: Path) -> Path:
    lock = read_lock()
    revision = lock["revision"]
    if (
        f"Pkg.Revision = {revision}"
        not in (ndk / "source.properties").read_text().splitlines()
    ):
        raise ValueError(f"Expected NDK {revision} at {ndk}")
    dist = ROOT / "out/dist"
    dist.mkdir(exist_ok=True)
    filename = f"aosstudio-ndk-{revision}-{lock['host']}.zip"
    with TemporaryDirectory(prefix=".package-", dir=dist) as directory:
        archive = Path(directory) / filename
        with ZipFile(archive, "x") as bundle:
            for entry in sorted(ndk.rglob("*")):
                mode = entry.lstat().st_mode
                if stat.S_ISDIR(mode):
                    continue
                if not stat.S_ISREG(mode):
                    raise ValueError(f"Expected a regular SDK ZIP member: {entry}")
                info = ZipInfo(
                    (Path(f"ndk-{revision}") / entry.relative_to(ndk)).as_posix(),
                    date_time=(2025, 7, 8, 0, 0, 0),
                )
                info.compress_type = ZIP_DEFLATED
                info.compress_level = 4
                info.external_attr = mode << 16
                info.file_size = entry.stat().st_size
                with bundle.open(info, "w") as dest, entry.open("rb") as src:
                    shutil.copyfileobj(src, dest, 1024 * 1024)
        return archive.replace(dist / filename)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Package the assembled NDK for SDK Manager."
    )
    parser.add_argument("ndk", type=Path)
    args = parser.parse_args()
    print(package(args.ndk))


if __name__ == "__main__":
    main()
