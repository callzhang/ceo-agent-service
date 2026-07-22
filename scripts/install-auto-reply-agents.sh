#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
release_root="${CEO_RELEASE_ROOT:-${HOME}/.local/share/ceo-agent-service}"
runtime_root="${CEO_SERVICE_DATA_ROOT:-${HOME}/Library/Application Support/CEO Agent Service}"
target_dir="${HOME}/Library/LaunchAgents"
log_dir="${HOME}/Library/Logs/ceo-agent-service"
domain="gui/$(id -u)"
legacy_label_prefix="com.$(id -un).ceo-agent-service"
plist_names=(
  "com.ceo-agent-service.main.plist"
)
obsolete_labels=(
  "com.ceo-agent-service.reply-producer"
  "com.ceo-agent-service.reply-consumer"
  "com.ceo-agent-service.audit-web"
)
legacy_labels=(
  "${legacy_label_prefix}.reply-producer"
  "${legacy_label_prefix}.reply-consumer"
  "${legacy_label_prefix}.audit-web"
  "${legacy_label_prefix}.hourly-dry-run"
  "${legacy_label_prefix}.dry-run-consumer"
  "${legacy_label_prefix}.memory-flush"
)
obsolete_plist_names=(
  "com.ceo-agent-service.reply-producer.plist"
  "com.ceo-agent-service.reply-consumer.plist"
  "com.ceo-agent-service.audit-web.plist"
)
legacy_plist_names=(
  "${legacy_label_prefix}.reply-producer.plist"
  "${legacy_label_prefix}.reply-consumer.plist"
  "${legacy_label_prefix}.audit-web.plist"
  "${legacy_label_prefix}.hourly-dry-run.plist"
  "${legacy_label_prefix}.dry-run-consumer.plist"
  "${legacy_label_prefix}.memory-flush.plist"
)

mkdir -p "${target_dir}" "${log_dir}" "${runtime_root}"
chmod 700 "${runtime_root}"

if [[ -f "${repo_root}/.env" && ! -f "${runtime_root}/service.env" ]]; then
  cp -p "${repo_root}/.env" "${runtime_root}/service.env"
  chmod 600 "${runtime_root}/service.env"
fi

legacy_db="${repo_root}/data/auto-reply.sqlite3"
runtime_db="${runtime_root}/auto-reply.sqlite3"
if [[ -f "${legacy_db}" && ! -f "${runtime_db}" ]]; then
  "${repo_root}/.venv/bin/python" - "${legacy_db}" "${runtime_db}" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as source, sqlite3.connect(sys.argv[2]) as target:
    source.backup(target)
PY
  chmod 600 "${runtime_db}"
fi

commit="$(git -C "${repo_root}" rev-parse HEAD)"
"${repo_root}/.venv/bin/python" - "${repo_root}" "${release_root}" "${commit}" <<'PY'
import sys
from pathlib import Path

from app.quality.release import LocalReleaseManager

manager = LocalReleaseManager(repo=Path(sys.argv[1]), release_root=Path(sys.argv[2]))
manager.activate(manager.build(sys.argv[3]))
PY

for label in "${obsolete_labels[@]}"; do
  launchctl bootout "${domain}/${label}" 2>/dev/null || true
done
for label in "${legacy_labels[@]}"; do
  launchctl bootout "${domain}/${label}" 2>/dev/null || true
done
for plist_name in "${obsolete_plist_names[@]}"; do
  rm -f "${target_dir}/${plist_name}"
done
for plist_name in "${legacy_plist_names[@]}"; do
  rm -f "${target_dir}/${plist_name}"
done

for plist_name in "${plist_names[@]}"; do
  label="${plist_name%.plist}"
  source_plist="${repo_root}/launchd/${plist_name}"
  target_plist="${target_dir}/${plist_name}"

  cp "${source_plist}" "${target_plist}"

  launchctl bootout "${domain}/${label}" 2>/dev/null || true
  launchctl bootout "${domain}" "${target_plist}" 2>/dev/null || true
  launchctl bootstrap "${domain}" "${target_plist}"
  launchctl kickstart -k "${domain}/${label}"

  printf 'installed %s\n' "${target_plist}"
done
