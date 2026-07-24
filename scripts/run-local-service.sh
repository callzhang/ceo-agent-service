#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

export PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
export HOME="${CEO_SERVICE_HOME:-${HOME}}"
export PYTHONPATH="${PYTHONPATH:-.}"
export CODEX_HOME="${CODEX_HOME:-${HOME}/.codex}"
export CEO_CODEX_MODEL="${CEO_CODEX_MODEL:-gpt-5.5}"
export CEO_CODEX_MODEL_REASONING_EFFORT="${CEO_CODEX_MODEL_REASONING_EFFORT:-medium}"
export CEO_WORKSPACE="${CEO_WORKSPACE:-${HOME}/Documents/memory}"
export CEO_DING_ROBOT_NAME="${CEO_DING_ROBOT_NAME:-磊哥}"
export CEO_WORKER_DB="${CEO_WORKER_DB:-${HOME}/Library/Application Support/ceo-agent-service/auto-reply.sqlite3}"
export CEO_NOT_SEND_MESSAGE="${CEO_NOT_SEND_MESSAGE:-${CEO_DRY_RUN:-0}}"
export CEO_CORPUS_DIR="${CEO_CORPUS_DIR:-${repo_root}/data/corpus}"
export CEO_OKR_LIVE_SOURCE_COMMAND="${CEO_OKR_LIVE_SOURCE_COMMAND:-${repo_root}/scripts/dingteam_okr_live_source.py --user-id {user_id} --period-label {period_label}}"

ceo_agent_cmd=(.venv/bin/python -c 'from app.cli import main; main()')
if [[ -x .venv/bin/ceo-agent ]]; then
  ceo_agent_cmd=(.venv/bin/ceo-agent)
fi

# app.config loads the repo-local .env.  The preflight itself is always local
# and exits silently when Feishu is disabled; optional SDK and credential
# checks run only after the operator explicitly enables the channel.
.venv/bin/python - <<'PY'
import importlib
from importlib import metadata
import sys

from app.config import feishu_app_id, feishu_app_secret, feishu_enabled


if not feishu_enabled():
    raise SystemExit(0)

sdk_configured = True
required_sdks = (
    ("lark_channel", "lark-channel-sdk", "1.2.0"),
    ("lark_oapi", "lark-oapi", "1.7.1"),
)
for module_name, distribution_name, required_version in required_sdks:
    try:
        importlib.import_module(module_name)
        if metadata.version(distribution_name) != required_version:
            sdk_configured = False
    except Exception:
        sdk_configured = False

app_id_configured = bool(feishu_app_id())
app_secret_configured = bool(feishu_app_secret())
statuses = {
    "sdk": "configured" if sdk_configured else "missing",
    "app_id": "configured" if app_id_configured else "missing",
    "app_secret": "configured" if app_secret_configured else "missing",
}
print(
    "feishu_preflight "
    + " ".join(f"{name}={status}" for name, status in statuses.items()),
    file=sys.stderr,
)
if not all((sdk_configured, app_id_configured, app_secret_configured)):
    raise SystemExit(1)
PY

if [[ -n "${CEO_MAX_BATCHES:-}" ]]; then
  exec "${ceo_agent_cmd[@]}" run-once --max-batches "${CEO_MAX_BATCHES}"
fi

exec "${ceo_agent_cmd[@]}" service \
  --host "${CEO_AUDIT_WEB_HOST:-127.0.0.1}" \
  --port "${CEO_AUDIT_WEB_PORT:-8765}"
