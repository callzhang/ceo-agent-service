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

if [[ ! -f "${workbench_index}" ]]; then
  workbench_assets_missing
fi
if ! python3 - "${workbench_index}" "${workbench_asset_dir}" <<'PY'
from html.parser import HTMLParser
from pathlib import Path
import sys
from urllib.parse import unquote, urlsplit


class AssetReferences(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, _tag, attrs):
        for name, value in attrs:
            if name in {"href", "src"} and value:
                path = urlsplit(value).path
                if path.startswith("/workbench-assets/"):
                    self.paths.append(unquote(path.removeprefix("/workbench-assets/")))


index_path = Path(sys.argv[1])
asset_root = Path(sys.argv[2]).resolve()
try:
    index_path.resolve().relative_to(asset_root)
    parser = AssetReferences()
    parser.feed(index_path.read_text(encoding="utf-8"))
    for relative_path in parser.paths:
        candidate = (asset_root / relative_path).resolve()
        candidate.relative_to(asset_root)
        if not candidate.is_file():
            raise FileNotFoundError(candidate)
except (OSError, UnicodeError, ValueError):
    raise SystemExit(1)
PY
then
  workbench_assets_missing
fi

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
