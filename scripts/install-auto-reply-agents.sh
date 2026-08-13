#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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

workbench_asset_dir="${repo_root}/app/static/workbench"
workbench_index="${workbench_asset_dir}/index.html"
workbench_assets_missing() {
  printf '%s\n' \
    'workbench assets missing; run npm install --prefix frontend && npm run build:workbench' \
    >&2
  exit 1
}

if [[ ! -f "${workbench_index}" || -L "${workbench_index}" ]]; then
  workbench_assets_missing
fi
if ! python3 - "${workbench_index}" "${workbench_asset_dir}" <<'PY'
from html.parser import HTMLParser
from pathlib import Path
import stat
import sys
from urllib.parse import unquote, urlsplit


class AssetReferences(HTMLParser):
    def __init__(self):
        super().__init__()
        self.module_scripts = []
        self.stylesheets = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "script" and values.get("type") == "module":
            self._append_asset(values.get("src"), self.module_scripts)
        if tag == "link" and "stylesheet" in values.get("rel", "").split():
            self._append_asset(values.get("href"), self.stylesheets)

    @staticmethod
    def _append_asset(value, destination):
        path = urlsplit(value or "").path
        if path.startswith("/workbench-assets/"):
            destination.append(unquote(path.removeprefix("/workbench-assets/")))


def is_regular_unlinked_asset(asset_root, relative_path, suffix):
    relative = Path(relative_path)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or relative.suffix != suffix
    ):
        return False
    candidate = asset_root
    try:
        for part in relative.parts:
            candidate = candidate / part
            metadata = candidate.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                return False
        return stat.S_ISREG(metadata.st_mode)
    except OSError:
        return False


index_path = Path(sys.argv[1])
asset_root = Path(sys.argv[2])
try:
    if not is_regular_unlinked_asset(asset_root, "index.html", ".html"):
        raise ValueError("invalid index")
    parser = AssetReferences()
    parser.feed(index_path.read_text(encoding="utf-8"))
    if not parser.module_scripts:
        raise ValueError("module script missing")
    if not all(
        is_regular_unlinked_asset(asset_root, path, ".js")
        for path in parser.module_scripts
    ):
        raise ValueError("module script invalid")
    if not all(
        is_regular_unlinked_asset(asset_root, path, ".css")
        for path in parser.stylesheets
    ):
        raise ValueError("stylesheet invalid")
except (OSError, UnicodeError, ValueError):
    raise SystemExit(1)
PY
then
  workbench_assets_missing
fi

for plist_name in "${plist_names[@]}"; do
  source_plist="${repo_root}/launchd/${plist_name}"
  if [[ ! -f "${source_plist}" || -L "${source_plist}" ]]; then
    printf 'install prerequisite missing: launchd/%s\n' "${plist_name}" >&2
    exit 1
  fi
done

mkdir -p "${target_dir}" "${log_dir}"

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
