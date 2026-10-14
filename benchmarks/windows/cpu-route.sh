#!/usr/bin/env bash
# Compare GPU-style KV, the CPU preset, and the CPU preset with every visible CPU.
# Usage: bash benchmarks/windows/cpu-route.sh [case.json] [fresh-outdir]
# The configured CPU preset and its initial running/stopped state are restored.
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
export PYTHONUTF8=1
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
M=${MIROBODY_DIR:-$(cd "$HERE/../.." && pwd)}
CASE=${1:-$HERE/results/raw/cpu_case_one.json}
OUT=${2:-$HOME/mirobody-results/cpu-route-$(date +%Y%m%d-%H%M%S)-$$}
ROUTER=${LLAMA_ROUTER:-http://127.0.0.1:8080}
HEALTH_ATTEMPTS=${BENCH_HEALTH_ATTEMPTS:-60}
MODEL_ATTEMPTS=${BENCH_MODEL_ATTEMPTS:-120}
WAIT_SECONDS=${BENCH_WAIT_SECONDS:-5}
PROBE="$HERE/speed_probe.py"
for command in docker curl python3 uv cmp; do command -v "$command" >/dev/null || { echo "Missing $command" >&2; exit 1; }; done
for file in "$CASE" "$PROBE" "$M/docker/local-models.ini" "$M/docker/local-models-cpu.ini"; do
    [ -f "$file" ] || { echo "Missing file: $file" >&2; exit 1; }
done
[ ! -e "$OUT" ] || { echo "Choose a fresh output directory: $OUT" >&2; exit 1; }
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
CASE=$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$CASE")
cd "$M"
WORK=$(mktemp -d)
# The richer restore trap below replaces this after the read-only snapshot.
trap 'rm -rf "$WORK"' EXIT
TOUCHED=0
ORIGINAL_ID=$(docker compose --profile local-cpu ps --all --quiet llama_cpu)
ORIGINAL_RUNNING=false
RESTORE_PRESET=$(docker compose --profile local-cpu config --format json | python3 -c '
import json,sys
config=json.load(sys.stdin)
print(next(v["source"] for v in config["services"]["llama_cpu"]["volumes"]
           if v["target"]=="/presets/local-models.ini"))')
if [ -n "$ORIGINAL_ID" ]; then
    ORIGINAL_RUNNING=$(docker inspect --format '{{.State.Running}}' "$ORIGINAL_ID")
    mounted=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/presets/local-models.ini"}}{{.Source}}{{end}}{{end}}' "$ORIGINAL_ID")
    if [ -f "$mounted" ]; then
        RESTORE_PRESET=$mounted
    else
        # Docker Desktop may report its daemon-internal bind path. Reuse the
        # caller's Compose source only when it has the exact mounted contents.
        docker cp "$ORIGINAL_ID:/presets/local-models.ini" "$WORK/original.ini"
        cmp -s "$RESTORE_PRESET" "$WORK/original.ini" || {
            echo "Cannot restore the mounted preset from $mounted; align Compose first." >&2
            rm -rf "$WORK"
            exit 1
        }
    fi
fi
[ -f "$RESTORE_PRESET" ] || { echo "Missing original CPU preset: $RESTORE_PRESET" >&2; rm -rf "$WORK"; exit 1; }
cleanup() {
    local status=$? restored=0
    trap - EXIT
    if [ "$TOUCHED" = 1 ]; then
        if [ -n "$ORIGINAL_ID" ]; then
            COMPOSE_PROFILES=local-cpu LOCAL_MODELS_PRESET="$RESTORE_PRESET" \
                docker compose --profile local-cpu up -d --no-deps --force-recreate llama_cpu > "$OUT/restore.log" 2>&1 || restored=1
            if [ "$ORIGINAL_RUNNING" != true ]; then
                docker compose --profile local-cpu stop llama_cpu >> "$OUT/restore.log" 2>&1 || restored=1
            fi
        else
            docker compose --profile local-cpu rm --stop --force llama_cpu > "$OUT/restore.log" 2>&1 || restored=1
        fi
        if [ "$restored" != 0 ]; then
            echo "Failed to restore llama_cpu; see $OUT/restore.log" >&2
            # A failed restore may still bind one of these files.
            echo "Temporary presets retained at $WORK" >&2
            exit 1
        fi
    fi
    rm -rf "$WORK"
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

wait_router() {
    local i
    for ((i=0; i<HEALTH_ATTEMPTS; i++)); do
        if curl -fsS --noproxy '*' --max-time 5 "$ROUTER/health" > "$WORK/health.json" 2>/dev/null &&
           python3 -c 'import json,sys; sys.exit(json.load(open(sys.argv[1])).get("status") != "ok")' "$WORK/health.json"; then
            return 0
        fi
        sleep "$WAIT_SECONDS"
    done
    echo "Router did not become healthy at $ROUTER; see Docker logs." >&2
    return 1
}
wait_model() {
    local i
    for ((i=0; i<MODEL_ATTEMPTS; i++)); do
        if curl -fsS --noproxy '*' --max-time 15 "$ROUTER/models" > "$WORK/models.json" 2>/dev/null &&
           python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(not any(m["id"]=="minicpm5-2b" and m.get("status",{}).get("value")=="loaded" for m in d.get("data",[])))' "$WORK/models.json"; then
            return 0
        fi
        sleep "$WAIT_SECONDS"
    done
    echo "MiniCPM5-2B did not load; probe was not run." >&2
    return 1
}
run_variant() {
    local label=$1 source=$2 threads=${3:-} preset="$WORK/$1.ini"
    python3 - "$source" "$preset" "$threads" <<'PY'
from pathlib import Path
import sys
text = Path(sys.argv[1]).read_text(encoding="utf-8")
if sys.argv[3]:
    marker = "[minicpm5-2b]\n"
    if text.count(marker) != 1:
        raise SystemExit("Missing/ambiguous MiniCPM5-2B preset section")
    text = text.replace(marker, marker + "threads = " + sys.argv[3] + "\n")
Path(sys.argv[2]).write_text(text, encoding="utf-8")
PY
    echo "########## $label ##########"
    TOUCHED=1
    COMPOSE_PROFILES=local-cpu LOCAL_MODELS_PRESET="$preset" \
        docker compose --profile local-cpu up -d --no-deps --force-recreate llama_cpu > "$OUT/$label-start.log" 2>&1
    wait_router
    curl -fsS --noproxy '*' --max-time 20 "$ROUTER/models" > "$OUT/$label-models.json"
    curl -fsS --noproxy '*' --max-time 30 -X POST "$ROUTER/models/load" \
        -H 'Content-Type: application/json' -d '{"model":"minicpm5-2b"}' > "$OUT/$label-load.json"
    wait_model
    docker compose logs --no-color --tail 80 llama_cpu > "$OUT/$label-server.log" 2>&1
    uv run --no-project --python 3.12 "$PROBE" "$ROUTER" "$label" "$CASE" "$OUT/$label.out.json" 2>&1 | tee "$OUT/$label-probe.log"
}
run_variant cpu-A-deployed "$M/docker/local-models.ini"
run_variant cpu-B-cpu-preset "$M/docker/local-models-cpu.ini"
threads=$(docker compose exec -T llama_cpu nproc)
[[ "$threads" =~ ^[1-9][0-9]*$ ]] || { echo "Invalid visible CPU count: $threads" >&2; exit 1; }
run_variant cpu-C-all-threads "$M/docker/local-models-cpu.ini" "$threads"
echo "########## summary ##########"
python3 - "$OUT" <<'PY'
import json
from pathlib import Path
import sys
print(f'{"variant":22} {"prompt_t/s":10} {"TTFT_s":10} {"gen_t/s":10} wall_s')
for label in ("cpu-A-deployed", "cpu-B-cpu-preset", "cpu-C-all-threads"):
    for value in json.loads((Path(sys.argv[1]) / f"{label}.out.json").read_text(encoding="utf-8")).values():
        print(f'{label:22} {str(value.get("pp_tps")):10} {str(value.get("ttft_s")):10} {str(value.get("gen_tps")):10} {value.get("wall")}')
PY
