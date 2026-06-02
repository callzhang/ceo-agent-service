#!/usr/bin/env bash
set -euo pipefail

workspace="${CEO_WORKSPACE:-${HOME}/Documents/memory}"
service_home="${CEO_SERVICE_HOME:-${HOME}}"
keychain_dir="${DWS_KEYCHAIN_DIR:-${workspace}/Library/Application Support/dws-cli}"
dws_bin="${DWS_BIN:-dws}"
path_value="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
service_user="${CEO_SERVICE_USER:-${USER:-$(id -un)}}"

run_unread_probe() {
  local name="$1"
  local home="$2"
  shift 2
  local output_file
  output_file="$(mktemp)"
  set +e
  env -i \
    PATH="${path_value}" \
    USER="${service_user}" \
    LOGNAME="${service_user}" \
    HOME="${home}" \
    "$@" \
    "${dws_bin}" chat message list-unread-conversations --count 1 --timeout 5 --format json \
    >"${output_file}" 2>&1
  local exit_code=$?
  set -e

  printf '%s exit=%s ' "${name}" "${exit_code}"
  /usr/bin/python3 - "$output_file" <<'PY'
import json
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(errors="replace")
try:
    payload = json.loads(text)
except json.JSONDecodeError:
    print(text[:300].replace("\n", " "))
    raise SystemExit

error = payload.get("error")
if error:
    print(f"reason={error.get('reason')} message={error.get('message')}")
else:
    print(f"success={payload.get('success')}")
PY
  rm -f "${output_file}"
}

run_unread_probe "default-user-auth" "${service_home}"
run_unread_probe "forced-file-keychain" "${service_home}" \
  DWS_DISABLE_KEYCHAIN=1 \
  DWS_KEYCHAIN_DIR="${keychain_dir}"

if [[ "${1:-}" == "--include-native-keychain" ]]; then
  printf '%s\n' "native-keychain probe may trigger a macOS Keychain dialog."
  run_unread_probe "native-keychain" "${service_home}" DWS_DISABLE_KEYCHAIN=0
fi
