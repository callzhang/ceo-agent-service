#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
label="com.stardust.ceo-agent.wechat-reader"
source_app="${CEO_WECHAT_READER_OUTPUT_DIR:-${HOME}/Library/Caches/CEO Agent/WeChatReaderBuild/dist}/CEO WeChat Reader.app"
target_root="${HOME}/Applications"
target_app="${target_root}/CEO WeChat Reader.app"
source_plist="${repo_root}/launchd/${label}.plist"
target_plist="${HOME}/Library/LaunchAgents/${label}.plist"
log_dir="${HOME}/Library/Logs/ceo-agent-service"
domain="gui/$(id -u)"
allow_adhoc=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --allow-adhoc)
      allow_adhoc=1
      shift
      ;;
    --app)
      source_app="$2"
      shift 2
      ;;
    *)
      printf 'unknown argument: %s\n' "$1" >&2
      exit 2
      ;;
  esac
done

codesign --verify --deep --strict --verbose=2 "${source_app}"
signature_info="$(codesign -dv --verbose=4 "${source_app}" 2>&1)"
if [[ "${signature_info}" == *"Signature=adhoc"* && "${allow_adhoc}" -ne 1 ]]; then
  printf '%s\n' \
    'Refusing to install a development-only ad-hoc build. Use a stable signing certificate, or pass --allow-adhoc explicitly.' >&2
  exit 2
fi

mkdir -p "${target_root}" "$(dirname "${target_plist}")" "${log_dir}"
launchctl bootout "${domain}/${label}" 2>/dev/null || true

staging_app="${target_root}/.CEO WeChat Reader.app.new"
backup_app="${target_root}/.CEO WeChat Reader.app.previous"
rm -rf "${staging_app}" "${backup_app}"
ditto --rsrc --extattr "${source_app}" "${staging_app}"
if [[ -e "${target_app}" ]]; then
  mv "${target_app}" "${backup_app}"
fi
mv "${staging_app}" "${target_app}"
rm -rf "${backup_app}"

cp "${source_plist}" "${target_plist}"
reader_executable="${target_app}/Contents/MacOS/CEO WeChat Reader"
/usr/libexec/PlistBuddy -c "Set :ProgramArguments:0 ${reader_executable}" "${target_plist}"
/usr/bin/sed -i '' "s|__HOME__|${HOME}|g" "${target_plist}"
plutil -lint "${target_plist}"
launchctl bootstrap "${domain}" "${target_plist}"
launchctl enable "${domain}/${label}"
launchctl kickstart -k "${domain}/${label}"

printf 'installed %s\n' "${target_app}"
printf 'started %s\n' "${domain}/${label}"
