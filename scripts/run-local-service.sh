#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

export PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
export HOME="${CEO_SERVICE_HOME:-${HOME}}"
export PYTHONPATH="${PYTHONPATH:-.}"
export CODEX_HOME="${CODEX_HOME:-${HOME}/.codex}"
export CEO_WORKSPACE="${CEO_WORKSPACE:-${HOME}/Documents/memory}"
export CEO_WORKER_DB="${CEO_WORKER_DB:-${repo_root}/data/auto-reply.sqlite3}"
export CEO_NOT_SEND_MESSAGE="${CEO_NOT_SEND_MESSAGE:-${CEO_DRY_RUN:-1}}"
export CEO_POLL_INTERVAL_SECONDS="${CEO_POLL_INTERVAL_SECONDS:-30}"
export CEO_PRODUCER_INTERVAL_SECONDS="${CEO_PRODUCER_INTERVAL_SECONDS:-60}"
export CEO_CONSUMER_POLL_INTERVAL_SECONDS="${CEO_CONSUMER_POLL_INTERVAL_SECONDS:-10}"
export CEO_BATCH_SECONDS="${CEO_BATCH_SECONDS:-120}"
export CEO_CORPUS_DIR="${CEO_CORPUS_DIR:-${repo_root}/corpus}"
export DWS_DISABLE_KEYCHAIN="${DWS_DISABLE_KEYCHAIN:-1}"
export DWS_KEYCHAIN_DIR="${DWS_KEYCHAIN_DIR:-${CEO_WORKSPACE}/Library/Application Support/dws-cli}"

ceo_agent_cmd=(.venv/bin/python -c 'from app.cli import main; main()')
if [[ -x .venv/bin/ceo-agent ]]; then
  ceo_agent_cmd=(.venv/bin/ceo-agent)
fi

if [[ -n "${CEO_MAX_BATCHES:-}" ]]; then
  exec "${ceo_agent_cmd[@]}" run-once --max-batches "${CEO_MAX_BATCHES}"
fi

exec "${ceo_agent_cmd[@]}" service \
  --host "${CEO_AUDIT_WEB_HOST:-127.0.0.1}" \
  --port "${CEO_AUDIT_WEB_PORT:-8765}" \
  --producer-interval-seconds "${CEO_PRODUCER_INTERVAL_SECONDS}" \
  --consumer-poll-interval-seconds "${CEO_CONSUMER_POLL_INTERVAL_SECONDS}"
