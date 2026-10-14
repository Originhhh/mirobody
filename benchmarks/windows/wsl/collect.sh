#!/usr/bin/env bash
# shellcheck disable=SC2016
# The bash -c diagnostics expand $1 in the child, preserving paths with spaces.
# Archive synthetic benchmark results; environment diagnostics retain failures.
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
M=${MIROBODY_DIR:-$(cd "$HERE/../../.." && pwd)}
OUT=${OUT:-$HOME/mirobody-results}
KIT=${KIT:-/mnt/c/mirobody-bench}
SPEED_RESULTS=${SPEED_RESULTS:-}
[ -d "$OUT" ] || { echo "No results directory: $OUT" >&2; exit 1; }
mkdir -p "$KIT"
# Collecting diagnostics is best effort; failed commands are recorded instead
# of discarding the remaining evidence when a model container has crashed.
diagnostic() {
    echo "## $1"
    shift
    "$@" || echo "diagnostic exited $?"
}
{
    echo "## $(date -Is)"
    diagnostic checkout git -C "$M" log --oneline -3
    diagnostic changes git -C "$M" status --short
    diagnostic images bash -c 'cd "$1" && docker compose config --images' bash "$M"
    diagnostic containers bash -c 'cd "$1" && docker compose ps' bash "$M"
    diagnostic docker docker version --format '{{.Server.Version}} {{.Server.Os}}/{{.Server.Arch}}'
    diagnostic kernel uname -a
    diagnostic cpus nproc
    diagnostic memory free -g
    if command -v nvidia-smi >/dev/null; then diagnostic gpu nvidia-smi; fi
    diagnostic app-log bash -c 'cd "$1" && docker compose logs --no-color --tail 300 mirobody mirobody_worker' bash "$M"
    diagnostic model-log bash -c 'cd "$1" && docker compose --profile local --profile local-cpu logs --no-color --tail 200 llama llama_cpu' bash "$M"
} > "$OUT/environment.txt" 2>&1
if [ -n "$SPEED_RESULTS" ]; then
    [ -d "$SPEED_RESULTS" ] || { echo "No native results directory: $SPEED_RESULTS" >&2; exit 1; }
    # Separate native runs by directory name instead of overwriting quality files.
    mkdir -p "$OUT/native"
    cp -R "$SPEED_RESULTS" "$OUT/native/"
fi
archive="$KIT/mirobody-windows-results-$(date +%Y%m%d-%H%M%S)-$$.tgz"
OUT=$(cd "$OUT" && pwd)
tar czf "$archive" -C "$(dirname "$OUT")" "$(basename "$OUT")"
ls -la "$archive"
