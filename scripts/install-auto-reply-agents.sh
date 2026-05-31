#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
target_dir="${HOME}/Library/LaunchAgents"
log_dir="${HOME}/Library/Logs/ceo-agent-service"
domain="gui/$(id -u)"
legacy_label_prefix="com.$(id -un).ceo-agent-service"
plist_names=(
  "com.ceo-agent-service.reply-producer.plist"
  "com.ceo-agent-service.reply-consumer.plist"
  "com.ceo-agent-service.audit-web.plist"
)
legacy_labels=(
  "${legacy_label_prefix}.reply-producer"
  "${legacy_label_prefix}.reply-consumer"
  "${legacy_label_prefix}.audit-web"
  "${legacy_label_prefix}.hourly-dry-run"
  "${legacy_label_prefix}.dry-run-consumer"
  "${legacy_label_prefix}.memory-flush"
)
legacy_plist_names=(
  "${legacy_label_prefix}.reply-producer.plist"
  "${legacy_label_prefix}.reply-consumer.plist"
  "${legacy_label_prefix}.audit-web.plist"
  "${legacy_label_prefix}.hourly-dry-run.plist"
  "${legacy_label_prefix}.dry-run-consumer.plist"
  "${legacy_label_prefix}.memory-flush.plist"
)
env_file="${CEO_ENV_FILE:-${repo_root}/.env}"
producer_interval_seconds="${CEO_PRODUCER_INTERVAL_SECONDS:-}"
if [[ -z "${producer_interval_seconds}" && -f "${env_file}" ]]; then
  producer_interval_seconds="$(
    awk -F= '$1 == "CEO_PRODUCER_INTERVAL_SECONDS" {
      value = substr($0, index($0, "=") + 1)
      gsub(/^[ \t"'"'"']+|[ \t"'"'"']+$/, "", value)
      print value
      exit
    }' "${env_file}"
  )"
fi
producer_interval_seconds="${producer_interval_seconds:-60}"
if [[ ! "${producer_interval_seconds}" =~ ^[1-9][0-9]*$ ]]; then
  printf 'invalid CEO_PRODUCER_INTERVAL_SECONDS: %s\n' "${producer_interval_seconds}" >&2
  exit 2
fi

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
  if [[ "${plist_name}" == "com.ceo-agent-service.reply-producer.plist" ]]; then
    /usr/libexec/PlistBuddy -c "Set :StartInterval ${producer_interval_seconds}" "${target_plist}"
  fi

  launchctl bootout "${domain}/${label}" 2>/dev/null || true
  launchctl bootout "${domain}" "${target_plist}" 2>/dev/null || true
  launchctl bootstrap "${domain}" "${target_plist}"
  launchctl kickstart -k "${domain}/${label}"

  printf 'installed %s\n' "${target_plist}"
done
