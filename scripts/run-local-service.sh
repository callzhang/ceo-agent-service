#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

export PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
export HOME="${CEO_SERVICE_HOME:-${HOME}}"
export PYTHONPATH="${PYTHONPATH:-.}"
export PYTHONDONTWRITEBYTECODE=1
export CEO_CONDA_PREFIX="${CEO_CONDA_PREFIX:-${HOME}/miniforge3}"
export CEO_PYTHON="${CEO_PYTHON:-${CEO_CONDA_PREFIX}/bin/python}"
export CODEX_HOME="${CODEX_HOME:-${HOME}/.codex}"
export CEO_WORKSPACE="${CEO_WORKSPACE:-${HOME}/Documents/memory}"
export CEO_DING_ROBOT_NAME="${CEO_DING_ROBOT_NAME:-磊哥}"
export CEO_WORKER_DB="${CEO_WORKER_DB:-${repo_root}/data/auto-reply.sqlite3}"
export CEO_NOT_SEND_MESSAGE="${CEO_NOT_SEND_MESSAGE:-${CEO_DRY_RUN:-0}}"
export CEO_CORPUS_DIR="${CEO_CORPUS_DIR:-${repo_root}/data/corpus}"
if [[ -z "${CEO_OKR_LIVE_SOURCE_COMMAND:-}" ]]; then
  okr_user_placeholder='{user_id}'
  okr_period_placeholder='{period_label}'
  export CEO_OKR_LIVE_SOURCE_COMMAND="${CEO_PYTHON} ${HOME}/.agents/skills/dingtang-okr-review/scripts/dingteam_okr_browser_source.py fetch --user-id ${okr_user_placeholder} --period-label ${okr_period_placeholder}"
fi

ceo_agent_cmd=("${CEO_PYTHON}" -c 'from app.cli import main; main()')

if [[ -n "${CEO_MAX_BATCHES:-}" ]]; then
  exec "${ceo_agent_cmd[@]}" run-once --max-batches "${CEO_MAX_BATCHES}"
fi

exec "${ceo_agent_cmd[@]}" service \
  --host "${CEO_AUDIT_WEB_HOST:-127.0.0.1}" \
  --port "${CEO_AUDIT_WEB_PORT:-8765}"
