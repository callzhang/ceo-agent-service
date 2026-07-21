#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bundle_id="com.stardust.ceo-agent.wechat-reader"
app_name="CEO WeChat Reader"
build_home="${HOME}/Library/Caches/CEO Agent/WeChatReaderBuild"
output_dir="${CEO_WECHAT_READER_OUTPUT_DIR:-${build_home}/dist}"
build_root="${build_home}/work"
builder_python="${CEO_WECHAT_READER_BUILD_PYTHON:-${repo_root}/.venv-reader-build/bin/python}"
signing_identity="${CEO_WECHAT_READER_SIGNING_IDENTITY:-}"
adhoc=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --adhoc)
      adhoc=1
      shift
      ;;
    --output-dir)
      output_dir="$2"
      shift 2
      ;;
    *)
      printf 'unknown argument: %s\n' "$1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "${signing_identity}" && "${adhoc}" -ne 1 ]]; then
  printf '%s\n' \
    'A stable SIGNING_IDENTITY is required. Set CEO_WECHAT_READER_SIGNING_IDENTITY or pass --adhoc for a development-only build.' >&2
  exit 2
fi

if [[ ! -x "${builder_python}" ]] || ! "${builder_python}" -m PyInstaller --version >/dev/null 2>&1; then
  printf 'PyInstaller build environment is missing: %s\n' "${builder_python}" >&2
  printf '%s\n' \
    'Create it with: python3 -m venv .venv-reader-build && .venv-reader-build/bin/pip install -e ".[reader-build]"' >&2
  exit 2
fi

mkdir -p "${output_dir}" "${build_root}/work" "${build_root}/spec"

pyinstaller_args=(
  --noconfirm
  --clean
  --onedir
  --windowed
  --name "${app_name}"
  --osx-bundle-identifier "${bundle_id}"
  --distpath "${output_dir}"
  --workpath "${build_root}/pyinstaller"
  --specpath "${build_root}/spec"
)
if [[ -n "${signing_identity}" ]]; then
  pyinstaller_args+=(--codesign-identity "${signing_identity}")
fi
pyinstaller_args+=("${repo_root}/app/wechat/reader_helper.py")

"${builder_python}" -m PyInstaller "${pyinstaller_args[@]}"

app_path="${output_dir}/${app_name}.app"
info_plist="${app_path}/Contents/Info.plist"
/usr/libexec/PlistBuddy -c 'Add :LSUIElement bool true' "${info_plist}" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c 'Set :LSUIElement true' "${info_plist}"
# Repeated local builds can inherit Finder/resource-fork xattrs that codesign rejects.
/usr/bin/xattr -cr "${app_path}"

if [[ -n "${signing_identity}" ]]; then
  # A local self-signed identity has no Apple Team ID. Hardened Runtime's
  # library validation would therefore reject the bundled Python dylibs even
  # though they are signed by the same certificate.
  codesign --force --deep --sign "${signing_identity}" "${app_path}"
else
  codesign --force --deep --sign - "${app_path}"
fi
codesign --verify --deep --strict --verbose=2 "${app_path}"

actual_bundle_id="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "${info_plist}")"
if [[ "${actual_bundle_id}" != "${bundle_id}" ]]; then
  printf 'unexpected bundle identifier: %s\n' "${actual_bundle_id}" >&2
  exit 1
fi
printf 'built %s (%s)\n' "${app_path}" "${actual_bundle_id}"
