#!/usr/bin/env bash
set -euo pipefail

workspace="${CEO_WORKSPACE:-/Users/derek/Documents/memory}"
keychain_dir="${DWS_KEYCHAIN_DIR:-${workspace}/Library/Application Support/dws-cli}"
dws_bin="${DWS_BIN:-dws}"
path_value="/Users/derek/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

run_unread_probe() {
  local name="$1"
  local home="$2"
  local disable_keychain="$3"
  local keychain="$4"
  local output_file
  output_file="$(mktemp)"
  set +e
  env -i \
    PATH="${path_value}" \
    USER=derek \
    LOGNAME=derek \
    HOME="${home}" \
    DWS_DISABLE_KEYCHAIN="${disable_keychain}" \
    DWS_KEYCHAIN_DIR="${keychain}" \
    "${dws_bin}" chat message list-unread-conversations --count 1 --format json \
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

bad_keychain_dir="$(mktemp -d)"
trap 'rm -rf "${bad_keychain_dir}"' EXIT

run_unread_probe "correct-file-keychain" "${workspace}" "1" "${keychain_dir}"
run_unread_probe "wrong-file-keychain" "${workspace}" "1" "${bad_keychain_dir}"

if [[ "${1:-}" == "--include-native-keychain" ]]; then
  printf '%s\n' "native-keychain probe may trigger a macOS Keychain dialog."
  run_unread_probe "native-keychain" "${workspace}" "0" "${keychain_dir}"
fi
