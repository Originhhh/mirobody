#!/usr/bin/env bash
#
# One-time setup for a GitHub Codespace, run from `postCreateCommand`.
#
# PostgreSQL is installed natively rather than through Docker. A codespace that
# ran Compose would need docker-in-docker, a second network stack, and a
# multi-gigabyte image pull before the first page load. Zulip, whose stack is
# heavier than this one, provisions its codespaces the same native way
# (`tools/setup/provision-codespace`).
#
# Ubuntu 24.04 ships PostgreSQL 16 and pgvector 0.6.0. `schema/00_prolog.sql`
# needs `CREATE EXTENSION vector`, pg_trgm and pgcrypto and nothing newer.

set -euo pipefail
cd "$(dirname "$0")/.."

PG_VERSION=16
DB_USER=mirobody
DB_PASSWORD=mirobody
DB_NAME=mirobody

echo "== PostgreSQL ${PG_VERSION} with pgvector =="
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    postgresql "postgresql-${PG_VERSION}-pgvector"

# A dev container has no systemd, so the cluster is started by hand. It does not
# come back after a codespace restart; `codespace_start.sh` starts it again.
sudo pg_ctlcluster "${PG_VERSION}" main start 2>/dev/null || true

# `sudo` in the base image is granted for `(root)` only, so `sudo -u postgres`
# falls through to a password prompt, and with no TTY it waits for one forever:
# measured at over ten minutes before it was killed. Zulip's codespace script
# writes the same rule for the same reason.
echo "== sudo for the postgres account =="
printf '%s ALL=(postgres) NOPASSWD: ALL\n' "$(id -un)" \
    | sudo tee /etc/sudoers.d/postgres-nopasswd >/dev/null
sudo chmod 0440 /etc/sudoers.d/postgres-nopasswd
sudo visudo -c >/dev/null

echo "== database =="
# `CREATE EXTENSION` needs a superuser, and the Compose stack's POSTGRES_USER is
# one too (`docker-compose` passes no NOSUPERUSER), so this role matches it.
# `sudo -n` throughout: without it a missing rule hangs instead of failing.
sudo -n -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" | grep -q 1 \
    || sudo -n -u postgres psql -q -c \
        "CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASSWORD}' SUPERUSER"
sudo -n -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1 \
    || sudo -n -u postgres createdb -O "${DB_USER}" "${DB_NAME}"

echo "== data bundles =="
# Without this the resolver reads LFS pointer stubs and every lookup raises.
git lfs install --local
git lfs pull

echo "== Python package =="
# A virtualenv, as docs/quickstart.md documents it. Ubuntu's system Python is
# externally managed (PEP 668), and the console script has to be on PATH for
# `codespace_start.sh`, which is simpler to arrange than to explain.
python -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e '.[app]'

# `.env` is gitignored and `Config.init` reads it first, which is where
# `deploy.sh` keeps its settings too. JWT_KEY and CONFIG_ENCRYPTION_KEY are
# written once: regenerating them would sign every session out and leave the
# stored provider credentials undecryptable.
if [[ ! -f .env ]]; then
    echo "== .env =="
    umask 077
    {
        # `mirobody dev` takes a URL and nothing else: `--pg-url`, `PG_URL` or
        # `DATABASE_URL`. `serve` and the worker read the `PG_*` keys instead,
        # so both spellings are written.
        echo "PG_URL=postgres://${DB_USER}:${DB_PASSWORD}@127.0.0.1:5432/${DB_NAME}"
        echo "PG_HOST=127.0.0.1"
        echo "PG_PORT=5432"
        echo "PG_USER=${DB_USER}"
        echo "PG_PASSWORD=${DB_PASSWORD}"
        echo "PG_DBNAME=${DB_NAME}"
        echo "PG_SCHEMA=public"
        echo "HTTP_HOST=0.0.0.0"
        echo "HTTP_PORT=18060"
        echo "SEED_DEMO_DATA=true"
        echo "JWT_KEY=$(.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))')"
        echo "CONFIG_ENCRYPTION_KEY=$(.venv/bin/python -c 'import secrets; print(secrets.token_hex(16))')"
        # Pinned so the link `codespace_start.sh` prints keeps working across a
        # restart. Unset, the first-run page mints a new token per process and
        # any link printed earlier is dead.
        echo "SETUP_TOKEN=$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(18))')"
    } > .env
fi

echo
echo "Mirobody is installed. The app starts on attach; see scripts/codespace_start.sh."
