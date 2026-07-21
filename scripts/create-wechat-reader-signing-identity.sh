#!/usr/bin/env bash
set -euo pipefail

identity="${CEO_WECHAT_READER_LOCAL_SIGNING_IDENTITY:-CEO WeChat Reader Local Signing}"
keychain="${CEO_WECHAT_READER_SIGNING_KEYCHAIN:-$(
  security default-keychain -d user \
    | sed -e 's/^[[:space:]\"]*//' -e 's/[[:space:]\"]*$//'
)}"
openssl_bin="${OPENSSL_BIN:-/opt/homebrew/bin/openssl}"

if [[ ! -x "${openssl_bin}" ]]; then
  printf 'OpenSSL 3 is required at %s (or set OPENSSL_BIN).\n' "${openssl_bin}" >&2
  exit 2
fi

has_valid_identity() {
  security find-identity -v -p codesigning "${keychain}" 2>/dev/null \
    | grep -F "\"${identity}\"" >/dev/null
}

if has_valid_identity; then
  printf 'stable signing identity already ready: %s\n' "${identity}"
  exit 0
fi

umask 077
work_dir="$(mktemp -d /private/tmp/ceo-wechat-reader-signing.XXXXXX)"
trap 'rm -rf "${work_dir}"' EXIT
cert_path="${work_dir}/certificate.pem"

if security find-certificate -c "${identity}" -p "${keychain}" >"${cert_path}" 2>/dev/null; then
  printf 'found existing certificate; repairing its code-signing trust\n'
else
  key_path="${work_dir}/private-key.pem"
  p12_path="${work_dir}/identity.p12"
  p12_password="$(${openssl_bin} rand -hex 24)"

  "${openssl_bin}" req -x509 -newkey rsa:3072 -sha256 -days 3650 -nodes \
    -keyout "${key_path}" \
    -out "${cert_path}" \
    -subj "/CN=${identity}/O=Stardust Local Development" \
    -addext 'basicConstraints=critical,CA:FALSE' \
    -addext 'keyUsage=critical,digitalSignature' \
    -addext 'extendedKeyUsage=critical,codeSigning'

  # ``-legacy`` keeps the PKCS#12 compatible with macOS Security.framework.
  "${openssl_bin}" pkcs12 -export -legacy \
    -out "${p12_path}" \
    -inkey "${key_path}" \
    -in "${cert_path}" \
    -passout "pass:${p12_password}"

  # The private key is non-exportable and only /usr/bin/codesign is pre-authorized.
  security import "${p12_path}" -k "${keychain}" \
    -P "${p12_password}" -x -T /usr/bin/codesign
fi

if ! "${openssl_bin}" x509 -in "${cert_path}" -noout -purpose \
  | grep -F 'Code signing : Yes' >/dev/null; then
  printf 'certificate is not restricted to code signing: %s\n' "${identity}" >&2
  exit 1
fi

printf '%s\n' 'macOS will request authentication to trust this identity for code signing only.'
security add-trusted-cert -r trustRoot -p codeSign -k "${keychain}" "${cert_path}"

if ! has_valid_identity; then
  printf 'identity was created but is not valid for code signing: %s\n' "${identity}" >&2
  exit 1
fi

printf 'stable signing identity ready: %s\n' "${identity}"
