#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}/apps/local-service"

export PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
export HOME="${CEO_SERVICE_HOME:-${HOME}}"
export CODEX_HOME="${CODEX_HOME:-${HOME}/.codex}"
export CEO_WORKER_DB="${CEO_WORKER_DB:-${repo_root}/data/auto-reply.sqlite3}"
export CEO_WORKSPACE="${CEO_WORKSPACE:-${HOME}/Documents/memory}"
export CEO_CORPUS_DIR="${CEO_CORPUS_DIR:-${repo_root}/corpus}"
export DWS_DISABLE_KEYCHAIN="${DWS_DISABLE_KEYCHAIN:-1}"
export DWS_KEYCHAIN_DIR="${DWS_KEYCHAIN_DIR:-${CEO_WORKSPACE}/Library/Application Support/dws-cli}"
export PYTHONPATH="${PYTHONPATH:-.}"

host="${CEO_AUDIT_WEB_HOST:-127.0.0.1}"
port="${CEO_AUDIT_WEB_PORT:-8765}"

args=(audit-web --host "${host}" --port "${port}")
if [[ "${CEO_AUDIT_WEB_RELOAD:-0}" == "1" ]]; then
  args+=(--reload)
fi

exec .venv/bin/python -m ceo_agent_service.cli "${args[@]}"
