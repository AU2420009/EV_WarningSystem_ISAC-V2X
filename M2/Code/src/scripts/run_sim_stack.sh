#!/usr/bin/env bash
# Launch OpenISAC's ChannelSimulator + BS + UE (no radios) with this project's overlays.
#
#   OPENISAC_DIR=~/OpenISAC scripts/run_sim_stack.sh [--dry-run] [--no-launch]
#
# Needs OpenISAC built:  cd build && cmake .. && make -j ChannelSimulator BS UE
# Order matters (docs): ChannelSimulator first, then BS, then UE; all from build/ because
# BS and UE read BS.yaml / UE.yaml from their working directory.
#   --dry-run    only write the merged BS.yaml / UE.yaml and print what would run
#   --no-launch  alias of --dry-run
# Stop with Ctrl-C; the script then stops all three processes.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
: "${OPENISAC_DIR:?set OPENISAC_DIR to your OpenISAC checkout}"
BUILD="${OPENISAC_BUILD_DIR:-$OPENISAC_DIR/build}"
PY="${PYTHON:-python3}"
DRY=0
for a in "$@"; do
  case "$a" in
    --dry-run|--no-launch) DRY=1 ;;
    *) echo "unknown argument: $a" >&2; exit 2 ;;
  esac
done

for f in BS_Sim_Duplex.yaml UE_Sim_Duplex.yaml; do
  [[ -f "$OPENISAC_DIR/config/$f" ]] || { echo "missing $OPENISAC_DIR/config/$f" >&2; exit 1; }
done
mkdir -p "$BUILD"
"$PY" "$HERE/apply_overlay.py" "$OPENISAC_DIR/config/BS_Sim_Duplex.yaml" "$ROOT/config/BS_Sim_Duplex.yaml" "$BUILD/BS.yaml"
"$PY" "$HERE/apply_overlay.py" "$OPENISAC_DIR/config/UE_Sim_Duplex.yaml" "$ROOT/config/UE_Sim_Duplex.yaml" "$BUILD/UE.yaml"
echo "wrote $BUILD/BS.yaml and $BUILD/UE.yaml"

if [[ "$DRY" == 1 ]]; then
  echo "dry run: would start ./ChannelSimulator, ./BS, ./UE in $BUILD"
  exit 0
fi

for b in ChannelSimulator BS UE; do
  [[ -x "$BUILD/$b" ]] || { echo "missing $BUILD/$b (build OpenISAC first)" >&2; exit 1; }
done

PIDS=()
cleanup() { for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done; wait 2>/dev/null || true; }
trap cleanup EXIT INT TERM

cd "$BUILD"
./ChannelSimulator & PIDS+=($!)
sleep 2
./BS & PIDS+=($!)
sleep 2
./UE & PIDS+=($!)
echo "stack up (ChannelSimulator, BS, UE). UDP: BS in 50000 -> UE out 50001; UE in 50002 -> BS out 50003."
wait
