#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}/apps/local-service"

export PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
export HOME="${CEO_SERVICE_HOME:-${HOME}}"
export PYTHONPATH="${PYTHONPATH:-.}"
export CODEX_HOME="${CODEX_HOME:-${HOME}/.codex}"
export CEO_WORKSPACE="${CEO_WORKSPACE:-${HOME}/Documents/memory}"
export CEO_WORKER_DB="${CEO_WORKER_DB:-${repo_root}/data/auto-reply.sqlite3}"
export CEO_CORPUS_DIR="${CEO_CORPUS_DIR:-${repo_root}/corpus}"
export DWS_DISABLE_KEYCHAIN="${DWS_DISABLE_KEYCHAIN:-1}"
export DWS_KEYCHAIN_DIR="${DWS_KEYCHAIN_DIR:-${CEO_WORKSPACE}/Library/Application Support/dws-cli}"
export CEO_NOT_SEND_MESSAGE="0"
export CEO_LIVE_SEND_BLOCKERS_ACCEPTED="1"

poll_interval="${CEO_CONSUMER_POLL_INTERVAL_SECONDS:-10}"

exec .venv/bin/python -m ceo_agent_service.cli consume \
  --poll-interval-seconds "${poll_interval}" \
  --db "${CEO_WORKER_DB}" \
  --workspace "${CEO_WORKSPACE}" \
  --corpus-dir "${CEO_CORPUS_DIR}"
