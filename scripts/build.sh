#!/usr/bin/env bash
set -euo pipefail

root=$(cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"
case "$(findmnt -n -o FSTYPE -T out)" in
    tmpfs | ramfs) printf 'Use a disk-backed NDK build directory.\n' >&2; exit 1 ;;
esac
export NDK_WORK
NDK_WORK=$(mktemp -d "$root/out/build.XXXXXXXX")
printf 'Work directory: %s\n' "$NDK_WORK"
mkdir -p "$NDK_WORK/tmp"
export TMPDIR="$NDK_WORK/tmp"
python=${PYTHON:-python3}
"$python" scripts/inputs.py --stage "$NDK_WORK"
bash scripts/build-host.sh
"$python" scripts/build-sysroot.py
"$python" scripts/build-sysroot.py --llvm "$NDK_WORK/stage/host-llvm"
readarray -t abis < <(jq -r '.abis[]' locks/sources.json)
for abi in "${abis[@]}"; do
    bash scripts/build-runtimes.sh "$abi"
done
ndk="$NDK_WORK/stage/ndk"
"$python" scripts/assemble-ndk.py --output "$ndk" "${abis[@]}"
cp locks/sources.json "$ndk/licenses/source-lock.json"
"$python" scripts/package.py "$ndk"
