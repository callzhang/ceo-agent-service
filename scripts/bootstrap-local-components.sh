#!/usr/bin/env bash
set -u

FORMAT_TEXT=1
COMPONENT="all"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CHECKOUT_PYTHON="${REPO_ROOT}/.venv/bin/python"

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --format)
      if [[ "${2:-}" != "json" ]]; then
        printf 'usage: %s [--format json] [--component NAME]\n' "$0" >&2
        exit 2
      fi
      FORMAT_TEXT=0
      shift 2
      ;;
    --component)
      if [[ -z "${2:-}" ]]; then
        printf 'usage: %s [--format json] [--component NAME]\n' "$0" >&2
        exit 2
      fi
      COMPONENT="$2"
      shift 2
      ;;
    *)
      printf 'unknown argument: %s\n' "$1" >&2
      exit 2
      ;;
  esac
done

case "${COMPONENT}" in
  all|terminal-notifier|codex|nvwa-skill|ceo-business-skills) ;;
  *)
    printf 'unknown component: %s\n' "${COMPONENT}" >&2
    exit 2
    ;;
esac

RESULT_COMPONENTS=()
RESULT_STATUSES=()
RESULT_DETAILS=()
FAILED=0

record() {
  local component="$1"
  local status="$2"
  local detail="$3"
  RESULT_COMPONENTS+=("${component}")
  RESULT_STATUSES+=("${status}")
  RESULT_DETAILS+=("${detail}")
  if [[ "${status}" == "failed" ]]; then
    FAILED=1
  fi
  if [[ "${FORMAT_TEXT}" == "1" ]]; then
    printf '%s: %s - %s\n' "${component}" "${status}" "${detail}"
  fi
}

json_string() {
  "${CHECKOUT_PYTHON}" -c 'import json,sys; print(json.dumps(sys.stdin.read()), end="")'
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
  local index component status detail
  for ((index = 0; index < ${#RESULT_COMPONENTS[@]}; index++)); do
    component="${RESULT_COMPONENTS[${index}]}"
    status="${RESULT_STATUSES[${index}]}"
    detail="${RESULT_DETAILS[${index}]}"
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

ensure_ceo_business_skills() {
  local detail
  if [[ ! -x "${CHECKOUT_PYTHON}" ]]; then
    record "ceo-business-skills" "failed" "missing checkout Python interpreter: ${CHECKOUT_PYTHON}"
    return
  fi
  if ! "${CHECKOUT_PYTHON}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
    record "ceo-business-skills" "failed" "invalid checkout Python interpreter: ${CHECKOUT_PYTHON}"
    return
  fi
  if detail="$(
    PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}" "${CHECKOUT_PYTHON}" - "${HOME}/.agents/skills" 2>&1 <<'PY'
from pathlib import Path
import sys

from app.business_skills import BusinessSkillError, install_bundled_business_skills

try:
    installed = install_bundled_business_skills(Path(sys.argv[1]))
except BusinessSkillError as exc:
    raise SystemExit(str(exc))
print("installed " + ", ".join(item.name for item in installed))
PY
  )"; then
    record "ceo-business-skills" "done" "${detail}"
  else
    record "ceo-business-skills" "failed" "${detail}"
  fi
}

if [[ "${COMPONENT}" == "all" || "${COMPONENT}" == "terminal-notifier" ]]; then
  ensure_terminal_notifier
fi
if [[ "${COMPONENT}" == "all" || "${COMPONENT}" == "codex" ]]; then
  ensure_codex
fi
if [[ "${COMPONENT}" == "all" || "${COMPONENT}" == "nvwa-skill" ]]; then
  ensure_nvwa
fi
if [[ "${COMPONENT}" == "all" || "${COMPONENT}" == "ceo-business-skills" ]]; then
  ensure_ceo_business_skills
fi

if [[ "${FORMAT_TEXT}" == "0" ]]; then
  emit_json
fi

exit "${FAILED}"
