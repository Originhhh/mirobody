#!/usr/bin/env bash
# Run the synthetic Q&A corpus through an existing benchmark stack.
# Keep fixed deployment endpoints protected: prepare the benchmark .env manually.
set -euo pipefail
MODE=${1:?docker-gpu, docker-cpu or native}
RUNS=${RUNS:-3}
[[ "$RUNS" =~ ^[1-9][0-9]*$ ]] || { echo "RUNS must be a positive integer" >&2; exit 2; }
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
M=${MIROBODY_DIR:-$(cd "$HERE/../../.." && pwd)}
G=${CORPUS:-$HOME/mirobody-gen-seed7}
OUT=${OUT:-$HOME/mirobody-results}
B=$M/benchmarks/local_models
PY=${BENCH_PYTHON:-$M/.venv/bin/python}
STAMP=$(date +%Y%m%d-%H%M%S)-$$
export BENCH_QA_GENERATION=${BENCH_QA_GENERATION:-win1}
[[ "$BENCH_QA_GENERATION" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "Invalid BENCH_QA_GENERATION" >&2; exit 2; }
# The shipped Mac state lists these accounts as loaded on another database.
[[ ! "$BENCH_QA_GENERATION" =~ ^qa[1-6]$ ]] || { echo "Use a fresh BENCH_QA_GENERATION for this stack, e.g. win1." >&2; exit 2; }
export MIROBODY_URL=${MIROBODY_URL:-http://localhost:18060} MIROBODY_COMPOSE_DIR=$M PYTHONPATH=$M
export LLAMA_ROUTER=${LLAMA_ROUTER:-http://127.0.0.1:8080}
case $MODE in
    native) export LLAMA_ROUTER_FROM_APP=${LLAMA_ROUTER_FROM_APP:-http://host.docker.internal:8080/v1} ;;
    docker-gpu | docker-cpu) export LLAMA_ROUTER_FROM_APP=${LLAMA_ROUTER_FROM_APP:-http://llama:8080/v1} ;;
    *) echo "unknown mode: $MODE" >&2; exit 2 ;;
esac
mkdir -p "$OUT"
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$OUT/chain.log"; }
[ -x "$PY" ] || { say "No benchmark Python at $PY; install the source environment first."; exit 1; }
for command in curl docker; do command -v "$command" >/dev/null || { say "Missing $command"; exit 1; }; done
curl -fsS --noproxy '*' --max-time 15 "$MIROBODY_URL/api/health" >/dev/null || { say "The app does not answer on $MIROBODY_URL"; exit 1; }
curl -fsS --noproxy '*' --max-time 15 "$LLAMA_ROUTER/models" >/dev/null || { say "No model server on $LLAMA_ROUTER"; exit 1; }
[ -f "$G/files.jsonl" ] || { say "No corpus at $G"; exit 1; }

loaded_record() {
    "$PY" - "$B" "${1:-local}" <<'PY'
import json
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
from common import qa_email, sign_in
from load import LOAD_STATE
base = Path(sys.argv[1])
try:
    state = json.loads(Path(LOAD_STATE).read_text(encoding="utf-8"))
    plan = json.loads((base / "plan.json").read_text(encoding="utf-8"))
    for person, config in plan["qa_people"].items():
        entry = state[qa_email(person)]
        steps = entry["steps"]
        for rel in config["documents"]:
            if steps["documents"][rel].get("status") != "done":
                raise ValueError("A document did not finish")
        if config["genomics"] and not steps.get("genomics", {}).get("active_set"):
            raise ValueError("The genotype did not load")
        if sys.argv[2] == "live":
            account = sign_in(qa_email(person), tz=entry["tz"], lang=entry["lang"])
            if str(account.user_id) != str(entry["user_id"]):
                raise ValueError("The saved account belongs to another stack")
            files = account.get("/api/v1/data/uploaded-files", limit=100)["data"]["files"]
            keys = {f["file_key"] for f in files}
            if any(steps["documents"][rel]["file_key"] not in keys for rel in config["documents"]):
                raise ValueError("A saved document is missing on this stack")
            if config["genomics"]:
                active = account.get("/api/v1/genomics/active-set")["data"]["active_set"]
                if active != steps["genomics"]["active_set"]:
                    raise ValueError("The saved genotype is missing on this stack")
except (OSError, KeyError, ValueError, TypeError):
    raise SystemExit(1)
PY
}
marker="$OUT/load-$BENCH_QA_GENERATION.done"
image=$(cd "$M" && docker compose config --images mirobody)
say "mode $MODE; checkout $(git -C "$M" log --oneline -1); image $image; record $BENCH_QA_GENERATION"
if [ ! -f "$marker" ] || ! loaded_record; then
    rm -f "$marker"
    # run.py configures a size itself, so only document loading requires ready.
    setup=$(curl -fsS --noproxy '*' --max-time 15 "$MIROBODY_URL/mirobody.json" |
        "$PY" -c 'import json,sys; print(json.load(sys.stdin).get("__MODEL_SETUP__", "unknown"))')
    [ "$setup" = ready ] || { say "Configure a model before loading documents (setup: $setup)."; exit 1; }
    say "loading question accounts; only completed documents produce a load marker"
    "$PY" "$B/load.py" --corpus "$G" --qa 2>&1 | tee "$OUT/load-$BENCH_QA_GENERATION-$STAMP.log"
    loaded_record || { say "Document/genotype loading is incomplete; retry loading before Q&A."; exit 1; }
    "$PY" "$B/load.py" --corpus "$G" --qa --verify 2>&1 | tee "$OUT/load-verify-$BENCH_QA_GENERATION-$STAMP.txt"
    loaded_record live || { say "Saved accounts/documents differ from this stack; use a fresh BENCH_QA_GENERATION."; exit 1; }
    touch "$marker"
else
    # Check the accounts against this live stack before trusting a local marker.
    "$PY" "$B/load.py" --corpus "$G" --qa --verify 2>&1 | tee "$OUT/load-verify-$BENCH_QA_GENERATION-$STAMP.txt"
    loaded_record live || { rm -f "$marker"; say "Saved accounts/documents differ from this stack; use a fresh BENCH_QA_GENERATION."; exit 1; }
fi
for ((n=1; n<=RUNS; n++)); do
    name=win-$MODE-$STAMP-$n
    say "$name start"
    mkdir "$OUT/$name"
    if "$PY" "$B/run.py" --size small --name "$name" --corpus "$G" --parts qa --no-hash \
        --out "$OUT/$name" > "$OUT/run-$name.log" 2>&1; then
        say "$name exit 0"
    else
        status=$?
        say "$name exit $status; see $OUT/run-$name.log"
        exit "$status"
    fi
    [ -f "$OUT/$name/summary.md" ] || { say "Missing summary for $name"; exit 1; }
    awk '/Questions passed|answered \/ right|added 2026|Seconds per/ {sub(/^\| /,"",$0); print}' "$OUT/$name/summary.md" | tee -a "$OUT/chain.log"
    if ! docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' > "$OUT/$name/docker-stats.txt" 2>&1; then
        say "WARNING: docker stats failed; see $OUT/$name/docker-stats.txt"
    fi
    if command -v nvidia-smi >/dev/null && ! nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv >> "$OUT/$name/docker-stats.txt" 2>&1; then
        say "WARNING: nvidia-smi failed; see docker-stats.txt"
    fi
done
say "done; bash $HERE/collect.sh packs $OUT"
