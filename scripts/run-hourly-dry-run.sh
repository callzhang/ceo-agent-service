#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
log_dir="${CEO_LOG_DIR:-${HOME}/Library/Logs/ceo-agent-service}"
lock_dir="${CEO_DRY_RUN_LOCK_DIR:-${TMPDIR:-/tmp}/ceo-agent-service-hourly-dry-run.lock}"

mkdir -p "${log_dir}"
if ! mkdir "${lock_dir}" 2>/dev/null; then
  if [[ -f "${lock_dir}/pid" ]] && kill -0 "$(cat "${lock_dir}/pid")" 2>/dev/null; then
    printf '%s hourly dry-run skipped: previous run still active\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    exit 0
  fi
  rm -rf "${lock_dir}"
  mkdir "${lock_dir}"
fi
printf '%s\n' "$$" > "${lock_dir}/pid"
trap 'rm -rf "${lock_dir}"' EXIT

cd "${repo_root}/apps/local-service"

export PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
export PYTHONPATH="${PYTHONPATH:-.}"
export CEO_WORKSPACE="${CEO_WORKSPACE:-${HOME}/Documents/memory}"
export CEO_WORKER_DB="${CEO_WORKER_DB:-${repo_root}/data/auto-reply.sqlite3}"
export CEO_CORPUS_DIR="${CEO_CORPUS_DIR:-${repo_root}/corpus}"
export CEO_NOT_SEND_MESSAGE="1"

printf '%s hourly not-send-message starting\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
.venv/bin/python -m ceo_agent_service.cli run-once \
  --not-send-message \
  --db "${CEO_WORKER_DB}" \
  --workspace "${CEO_WORKSPACE}" \
  --corpus-dir "${CEO_CORPUS_DIR}"
printf '%s hourly not-send-message finished\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
