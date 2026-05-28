#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}/apps/local-service"

export PATH="/Users/derek/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
export HOME="${CEO_SERVICE_HOME:-/Users/derek}"
export CODEX_HOME="${CODEX_HOME:-/Users/derek/.codex}"
export CEO_WORKSPACE="${CEO_WORKSPACE:-/Users/derek/Documents/memory}"
export CEO_WORKER_DB="${CEO_WORKER_DB:-${repo_root}/data/auto-reply.sqlite3}"
export CEO_NOT_SEND_MESSAGE="${CEO_NOT_SEND_MESSAGE:-${CEO_DRY_RUN:-1}}"
export CEO_POLL_INTERVAL_SECONDS="${CEO_POLL_INTERVAL_SECONDS:-30}"
export CEO_BATCH_SECONDS="${CEO_BATCH_SECONDS:-120}"
export CEO_CORPUS_DIR="${CEO_CORPUS_DIR:-${repo_root}/corpus}"
export DWS_DISABLE_KEYCHAIN="${DWS_DISABLE_KEYCHAIN:-1}"
export DWS_KEYCHAIN_DIR="${DWS_KEYCHAIN_DIR:-${CEO_WORKSPACE}/Library/Application Support/dws-cli}"

ceo_agent_cmd=(.venv/bin/python -c 'from ceo_agent_service.cli import main; main()')
if [[ -x .venv/bin/ceo-agent ]]; then
  ceo_agent_cmd=(.venv/bin/ceo-agent)
fi

if [[ -n "${CEO_MAX_BATCHES:-}" ]]; then
  exec "${ceo_agent_cmd[@]}" run-once --max-batches "${CEO_MAX_BATCHES}"
fi

exec "${ceo_agent_cmd[@]}" run
