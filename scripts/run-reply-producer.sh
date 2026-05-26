#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
log_dir="${CEO_LOG_DIR:-${HOME}/Library/Logs/ceo-agent-service}"
lock_dir="${CEO_REPLY_PRODUCER_LOCK_DIR:-${TMPDIR:-/tmp}/ceo-agent-service-reply-producer.lock}"

mkdir -p "${log_dir}"
if ! mkdir "${lock_dir}" 2>/dev/null; then
  if [[ -f "${lock_dir}/pid" ]] && kill -0 "$(cat "${lock_dir}/pid")" 2>/dev/null; then
    printf '%s producer skipped: previous run still active\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
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
export CEO_NOT_SEND_MESSAGE="0"
export CEO_LIVE_SEND_BLOCKERS_ACCEPTED="1"
export CEO_PRINCIPAL_NAME="${CEO_PRINCIPAL_NAME:-Derek}"
export CEO_PRINCIPAL_DISPLAY_NAME="${CEO_PRINCIPAL_DISPLAY_NAME:-Derek}"
export CEO_PRINCIPAL_HANDOFF_NAME="${CEO_PRINCIPAL_HANDOFF_NAME:-磊哥}"
export CEO_MENTION_ALIASES="${CEO_MENTION_ALIASES:-@Derek Zen,@磊哥}"
export CEO_CURRENT_USER_DISPLAY_NAMES="${CEO_CURRENT_USER_DISPLAY_NAMES:-磊哥,Derek Zen,Derek,Lei Zhang}"
export CEO_STYLE_SPEAKER_NAMES="${CEO_STYLE_SPEAKER_NAMES:-磊哥,Derek}"
export CEO_ASSISTANT_SIGNATURE="${CEO_ASSISTANT_SIGNATURE:-（by磊哥分身）}"
export CEO_HANDOFF_ACK="${CEO_HANDOFF_ACK:-我让磊哥本人看一下。（by磊哥分身）}"

printf '%s producer live starting\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
.venv/bin/python -m ceo_agent_service.cli produce-once \
  --db "${CEO_WORKER_DB}" \
  --workspace "${CEO_WORKSPACE}" \
  --corpus-dir "${CEO_CORPUS_DIR}"
printf '%s producer live finished\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
