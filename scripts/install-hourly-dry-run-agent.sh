#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
label="com.derek.ceo-agent-service.hourly-dry-run"
plist_name="com.derek.ceo-agent-service.hourly-dry-run.plist"
source_plist="${repo_root}/launchd/${plist_name}"
target_dir="${HOME}/Library/LaunchAgents"
target_plist="${target_dir}/${plist_name}"
log_dir="${HOME}/Library/Logs/ceo-agent-service"
domain="gui/$(id -u)"

mkdir -p "${target_dir}" "${log_dir}"
cp "${source_plist}" "${target_plist}"

launchctl bootout "${domain}/${label}" 2>/dev/null || true
launchctl bootout "${domain}" "${target_plist}" 2>/dev/null || true
launchctl bootstrap "${domain}" "${target_plist}"
launchctl kickstart -k "${domain}/${label}"

printf 'installed %s\n' "${target_plist}"
