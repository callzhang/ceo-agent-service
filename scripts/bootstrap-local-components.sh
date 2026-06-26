#!/usr/bin/env bash
set -u

FORMAT_TEXT=1
if [[ "${1:-}" == "--format" && "${2:-}" == "json" ]]; then
  FORMAT_TEXT=0
fi

RESULTS=()
FAILED=0

record() {
  local component="$1"
  local status="$2"
  local detail="$3"
  RESULTS+=("${component}"$'\t'"${status}"$'\t'"${detail}")
  if [[ "${status}" == "failed" ]]; then
    FAILED=1
  fi
  if [[ "${FORMAT_TEXT}" == "1" ]]; then
    printf '%s: %s - %s\n' "${component}" "${status}" "${detail}"
  fi
}

json_string() {
  python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()), end="")'
}

emit_json() {
  local summary
  if [[ "${FAILED}" == "0" ]]; then
    summary="Local CLI components were checked and repaired."
  else
    summary="Some CLI components still need an approved installer or manual authorization."
  fi

  printf '{"status":'
  if [[ "${FAILED}" == "0" ]]; then
    printf '"done"'
  else
    printf '"failed"'
  fi
  printf ',"summary":'
  printf '%s' "${summary}" | json_string
  printf ',"components":['
  local first=1
  local row component status detail
  for row in "${RESULTS[@]}"; do
    IFS=$'\t' read -r component status detail <<< "${row}"
    if [[ "${first}" == "0" ]]; then
      printf ','
    fi
    first=0
    printf '{"name":'
    printf '%s' "${component}" | json_string
    printf ',"status":'
    printf '%s' "${status}" | json_string
    printf ',"detail":'
    printf '%s' "${detail}" | json_string
    printf '}'
  done
  printf ']}\n'
}

command_path() {
  command -v "$1" 2>/dev/null || true
}

short_version() {
  "$@" 2>/dev/null | head -n 1 || true
}

install_with_command() {
  local command_var="$1"
  local configured_command="${!command_var:-}"
  if [[ -z "${configured_command}" ]]; then
    return 1
  fi
  bash -lc "${configured_command}"
}

ensure_terminal_notifier() {
  local path
  path="$(command_path terminal-notifier)"
  if [[ -n "${path}" ]]; then
    record "terminal-notifier" "done" "available at ${path}"
    return
  fi

  local brew
  brew="$(command_path brew)"
  if [[ -z "${brew}" ]]; then
    record "terminal-notifier" "failed" "Homebrew is missing; install Homebrew or install terminal-notifier from an approved package."
    return
  fi

  if "${brew}" install terminal-notifier; then
    path="$(command_path terminal-notifier)"
    if [[ -n "${path}" ]]; then
      record "terminal-notifier" "done" "installed with Homebrew at ${path}"
    else
      record "terminal-notifier" "failed" "Homebrew finished but terminal-notifier is still not on PATH."
    fi
  else
    record "terminal-notifier" "failed" "Homebrew install terminal-notifier failed."
  fi
}

ensure_dws() {
  local path version upgrade_detail
  path="$(command_path dws)"
  if [[ -n "${path}" ]]; then
    version="$(short_version dws --version)"
    upgrade_detail="upgrade check skipped"
    if dws upgrade --check --format json >/dev/null 2>&1; then
      if dws upgrade -y --format json >/dev/null 2>&1; then
        upgrade_detail="upgrade command completed"
      else
        upgrade_detail="upgrade command failed; existing dws remains available"
      fi
    else
      upgrade_detail="upgrade check failed; existing dws remains available"
    fi
    record "dws" "done" "available at ${path}${version:+ (${version})}; ${upgrade_detail}"
    return
  fi

  if [[ -n "${DWS_INSTALLER_PATH:-}" ]]; then
    if "${DWS_INSTALLER_PATH}"; then
      path="$(command_path dws)"
      if [[ -n "${path}" ]]; then
        record "dws" "done" "installed with DWS_INSTALLER_PATH at ${path}"
        return
      fi
      record "dws" "failed" "DWS_INSTALLER_PATH completed but dws is still not on PATH."
      return
    fi
    record "dws" "failed" "DWS_INSTALLER_PATH failed."
    return
  fi

  if install_with_command "DWS_INSTALL_COMMAND"; then
    path="$(command_path dws)"
    if [[ -n "${path}" ]]; then
      record "dws" "done" "installed with DWS_INSTALL_COMMAND at ${path}"
      return
    fi
    record "dws" "failed" "DWS_INSTALL_COMMAND completed but dws is still not on PATH."
    return
  fi

  record "dws" "failed" "Missing dws and no approved DWS_INSTALLER_PATH or DWS_INSTALL_COMMAND was provided."
}

ensure_codex() {
  local path version
  path="$(command_path codex)"
  if [[ -n "${path}" ]]; then
    version="$(short_version codex --version)"
    record "codex" "done" "available at ${path}${version:+ (${version})}"
    return
  fi

  if install_with_command "CODEX_INSTALL_COMMAND"; then
    path="$(command_path codex)"
    if [[ -n "${path}" ]]; then
      record "codex" "done" "installed with CODEX_INSTALL_COMMAND at ${path}"
      return
    fi
    record "codex" "failed" "CODEX_INSTALL_COMMAND completed but codex is still not on PATH."
    return
  fi

  record "codex" "failed" "Missing codex and no approved CODEX_INSTALL_COMMAND was provided."
}

copy_nvwa_source() {
  local source="$1"
  local target="${HOME}/.agents/skills/nuwa"
  mkdir -p "${target}"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a "${source%/}/" "${target}/"
  else
    cp -R "${source%/}/." "${target}/"
  fi
}

ensure_nvwa() {
  local primary="${HOME}/.agents/skills/nuwa/SKILL.md"
  local legacy="${HOME}/.agents/skills/huashu-nuwa/SKILL.md"
  if [[ -f "${primary}" || -f "${legacy}" ]]; then
    record "nvwa-skill" "done" "Nvwa skill is available."
    return
  fi

  if [[ -n "${NVWA_SKILL_SOURCE:-}" && -d "${NVWA_SKILL_SOURCE}" ]]; then
    if copy_nvwa_source "${NVWA_SKILL_SOURCE}"; then
      if [[ -f "${primary}" ]]; then
        record "nvwa-skill" "done" "installed from NVWA_SKILL_SOURCE."
        return
      fi
      record "nvwa-skill" "failed" "NVWA_SKILL_SOURCE copied but SKILL.md is missing."
      return
    fi
    record "nvwa-skill" "failed" "Failed to copy NVWA_SKILL_SOURCE."
    return
  fi

  record "nvwa-skill" "failed" "Missing Nvwa skill and no approved NVWA_SKILL_SOURCE directory was provided."
}

ensure_terminal_notifier
ensure_dws
ensure_codex
ensure_nvwa

if [[ "${FORMAT_TEXT}" == "0" ]]; then
  emit_json
fi

exit "${FAILED}"
