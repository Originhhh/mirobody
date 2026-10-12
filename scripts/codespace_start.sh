#!/usr/bin/env bash
#
# Starts Postgres, the task worker and the HTTP server.
#
# `postAttachCommand` runs this on every attach, so it exits as soon as the
# server already answers. Running it by hand restarts nothing either; stop the
# two processes first if you want a clean start.
#
# The server creates the schema and seeds the demo record on first start
# (`server/bootstrap.py`), so there is no separate migrate or seed step.

set -euo pipefail
cd "$(dirname "$0")/.."

PG_VERSION=16
LOG_DIR="${TMPDIR:-/tmp}"

if [[ ! -x .venv/bin/mirobody ]]; then
    echo "scripts/codespace_start.sh: .venv is missing." >&2
    echo "Run scripts/codespace_provision.sh first." >&2
    exit 1
fi

# Exported, not merely left in the file for the configuration layer to find.
# `mirobody dev` decides whether to generate JWT_KEY and CONFIG_ENCRYPTION_KEY
# BEFORE `Config.init` loads `.env`, so a value that only lives in the file is
# replaced on every start and no session survives a codespace restart.
#
# Sourced before the port is read so an edit to `.env` is honoured here too.
if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env || echo "warning: .env could not be sourced; continuing with the environment as it is" >&2
    set +a
fi

HTTP_PORT="${HTTP_PORT:-18060}"

# A dev container has no systemd, so the cluster does not come back on its own
# after a codespace restart.
sudo pg_ctlcluster "${PG_VERSION}" main start 2>/dev/null || true

if curl -fsS "http://127.0.0.1:${HTTP_PORT}/api/health" >/dev/null 2>&1; then
    echo "Mirobody is already running on port ${HTTP_PORT}."
    exit 0
fi

# shellcheck disable=SC1091
. .venv/bin/activate

# Both commands run from the repository root on purpose. `config.yaml` is the
# defaults only while `config.llm.yaml` sits beside it, and the web client at
# `frontend/` is mounted only when it exists in the working directory: a
# `pip install` outside a checkout gets neither.
nohup mirobody worker config.yaml >"${LOG_DIR}/mirobody-worker.log" 2>&1 &
nohup mirobody dev config.yaml --host 0.0.0.0 --port "${HTTP_PORT}" \
    >"${LOG_DIR}/mirobody-server.log" 2>&1 &

# Each forwarded port is served on its own domain, and the server's own hint can
# only name localhost (`server._print_setup_link`, which says so). Printed here
# instead, from the variables Codespaces itself sets.
base="http://localhost:${HTTP_PORT}"
if [[ -n "${CODESPACE_NAME:-}" && -n "${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-}" ]]; then
    base="https://${CODESPACE_NAME}-${HTTP_PORT}.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}"
fi

echo "Mirobody is starting."
echo "  app:    ${base}/"
if [[ -n "${SETUP_TOKEN:-}" ]]; then
    echo "  model:  ${base}/setup?token=${SETUP_TOKEN}"
fi
echo "  logs:   ${LOG_DIR}/mirobody-server.log"
echo "          ${LOG_DIR}/mirobody-worker.log"
echo
# The port is private, so the browser authenticates against GitHub before it
# reaches the app: one redirect, then the page. Saying so beats a reader taking
# the interstitial for a hang. The first start also creates the schema and seeds
# the demo record, which takes a few seconds.
echo "The first request may wait a few seconds: the schema and the demo record"
echo "are created on this first start."
