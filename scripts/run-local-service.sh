#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}/apps/local-service"

export CEO_WORKSPACE="${CEO_WORKSPACE:-${HOME}/Documents/memory}"
export CEO_WORKER_DB="${CEO_WORKER_DB:-${repo_root}/data/auto-reply.sqlite3}"
export CEO_NOT_SEND_MESSAGE="${CEO_NOT_SEND_MESSAGE:-${CEO_DRY_RUN:-1}}"
export CEO_POLL_INTERVAL_SECONDS="${CEO_POLL_INTERVAL_SECONDS:-30}"
export CEO_BATCH_SECONDS="${CEO_BATCH_SECONDS:-120}"
export CEO_CORPUS_DIR="${CEO_CORPUS_DIR:-${repo_root}/corpus}"

ceo_agent_cmd=(.venv/bin/python -c 'from ceo_agent_service.cli import main; main()')
if [[ -x .venv/bin/ceo-agent ]]; then
  ceo_agent_cmd=(.venv/bin/ceo-agent)
fi

if [[ -n "${CEO_MAX_BATCHES:-}" ]]; then
  exec "${ceo_agent_cmd[@]}" run-once --max-batches "${CEO_MAX_BATCHES}"
fi

exec "${ceo_agent_cmd[@]}" run
