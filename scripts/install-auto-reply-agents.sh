#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
target_dir="${HOME}/Library/LaunchAgents"
log_dir="${HOME}/Library/Logs/ceo-agent-service"
domain="gui/$(id -u)"
plist_names=(
  "com.derek.ceo-agent-service.reply-producer.plist"
  "com.derek.ceo-agent-service.reply-consumer.plist"
  "com.derek.ceo-agent-service.memory-flush.plist"
)
legacy_labels=(
  "com.derek.ceo-agent-service.hourly-dry-run"
  "com.derek.ceo-agent-service.dry-run-consumer"
)
legacy_plist_names=(
  "com.derek.ceo-agent-service.hourly-dry-run.plist"
  "com.derek.ceo-agent-service.dry-run-consumer.plist"
)

mkdir -p "${target_dir}" "${log_dir}"

for label in "${legacy_labels[@]}"; do
  launchctl bootout "${domain}/${label}" 2>/dev/null || true
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
