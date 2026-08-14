import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from math import log2
from typing import Any

from app.config import forbidden_path_prefixes


FORBIDDEN_MARKERS = (
    *forbidden_path_prefixes(),
    "codex",
    "graphify",
    "workspace",
    "本地 workspace",
    "本地检索",
    "graphify evidence",
    "source:",
    "sources:",
    "source=",
    "source =",
    "来源：",
    "citation",
    "session_id",
    "sessionid",
    "session id",
    "thread_id",
    "thread id",
    "codex_session",
)

_CREDENTIAL_PATTERNS = (
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(r"\bsk_live_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{30,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{6,}\b", re.IGNORECASE),
    re.compile(r"\bBasic\s+[A-Za-z0-9+/]{8,}={0,2}\b", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@", re.IGNORECASE),
    re.compile(
        r"\b[A-Za-z0-9_.-]*(?:password|token|api[_-]?key|private[_-]?key|secret)"
        r"\s*[:=]\s*(?:['\"])?[^\s'\"<>]{4,}",
        re.IGNORECASE,
    ),
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", re.IGNORECASE),
)

_OPAQUE_CREDENTIAL_CANDIDATE = re.compile(
    r"(?<![A-Za-z0-9_+/=])[A-Za-z0-9_+/=]{40,}(?![A-Za-z0-9_+/=])"
)
_CREDENTIAL_BOUNDARY_ERROR = "credential-bearing data is not allowed"


def contains_credential(text: str, *, credential_context: bool = False) -> bool:
    """Return whether text is a credential, using entropy only in declared contexts.

    Recognizable credential formats are unsafe anywhere. Generic high-entropy
    strings are common identifiers and digests, so they are credentials only
    when their containing field or argument has credential semantics.
    """
    if any(pattern.search(text) for pattern in _CREDENTIAL_PATTERNS):
        return True
    if not credential_context:
        return False
    return any(
        _is_high_confidence_opaque_credential(match.group(0))
        for match in _OPAQUE_CREDENTIAL_CANDIDATE.finditer(text)
    )


def _is_high_confidence_opaque_credential(candidate: str) -> bool:
    if len(candidate) < 40:
        return False
    character_classes = sum(
        (
            any(character.islower() for character in candidate),
            any(character.isupper() for character in candidate),
            any(character.isdigit() for character in candidate),
            any(character in "_+/=" for character in candidate),
        )
    )
    if character_classes < 3:
        return False
    frequencies = Counter(candidate)
    entropy = -sum(
        (count / len(candidate)) * log2(count / len(candidate))
        for count in frequencies.values()
    )
    return entropy >= 3.5


def assert_no_credentials(
    value: Any,
    *,
    field_name: str = "",
    credential_context: bool = False,
) -> None:
    """Reject credential-bearing JSON-like data without including it in the error."""
    effective_context = credential_context or (
        bool(field_name) and is_sensitive_field_name(field_name)
    )
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and (
                is_sensitive_field_name(key) or contains_credential(key)
            ):
                raise ValueError(_CREDENTIAL_BOUNDARY_ERROR)
            assert_no_credentials(
                item,
                field_name=key if isinstance(key, str) else "",
                credential_context=effective_context,
            )
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            assert_no_credentials(item, credential_context=effective_context)
        return
    if isinstance(value, str) and contains_credential(
        value, credential_context=effective_context
    ):
        raise ValueError(_CREDENTIAL_BOUNDARY_ERROR)


def assert_no_credential_arguments(argv: Sequence[str]) -> None:
    """Validate CLI arguments with flag names providing credential context."""
    header_value_follows = False
    for argument in argv:
        if header_value_follows:
            _assert_no_credential_header(argument)
            header_value_follows = False
        assert_no_credentials(argument)
        if argument in {"-H", "--header", "--proxy-header"}:
            header_value_follows = True
            continue
        if argument.startswith("-H") and len(argument) > 2:
            _assert_no_credential_header(argument[2:])
            continue
        for header_prefix in ("--header=", "--proxy-header="):
            if argument.startswith(header_prefix):
                _assert_no_credential_header(argument[len(header_prefix) :])
                break
        candidate = argument.lstrip("-") if argument.startswith("-") else ""
        if candidate:
            name, separator, value = candidate.partition("=")
            if not separator:
                name, separator, value = candidate.partition(":")
            if is_sensitive_field_name(name):
                if separator:
                    assert_no_credentials(value, credential_context=True)
                raise ValueError(_CREDENTIAL_BOUNDARY_ERROR)
        structured_candidates = [argument]
        for separator in ("=", ":"):
            if separator in argument:
                structured_candidates.append(argument.split(separator, 1)[1])
                break
        for structured_candidate in structured_candidates:
            stripped = structured_candidate.strip()
            if not stripped.startswith(("{", "[")):
                continue
            try:
                structured = json.loads(stripped)
            except (TypeError, ValueError):
                continue
            assert_no_credentials(structured)


def _assert_no_credential_header(header: str) -> None:
    assert_no_credentials(header)
    name, separator, value = header.partition(":")
    if not separator or not is_sensitive_header_name(name):
        return
    assert_no_credentials(value, credential_context=True)
    # Header names declare credential semantics even for short or malformed
    # values that generic format and entropy checks cannot classify.
    raise ValueError(_CREDENTIAL_BOUNDARY_ERROR)


def is_sensitive_header_name(value: str) -> bool:
    trimmed = value.strip()
    segments = tuple(
        segment for segment in re.split(r"[^a-z0-9]+", trimmed.casefold()) if segment
    )
    normalized = "".join(
        character for character in trimmed.casefold() if character.isalnum()
    )
    return (
        is_sensitive_field_name(trimmed)
        or normalized in {"authorization", "proxyauthorization", "key", "xkey"}
        or normalized.endswith("auth")
        or normalized.endswith(("appkey", "clientkey", "subscriptionkey"))
        or any(
            segment
            in {"auth", "credential", "credentials", "password", "secret", "token"}
            for segment in segments
        )
    )


def redact_credentials(
    text: str,
    replacement: str = "[REDACTED]",
    *,
    credential_context: bool = False,
) -> str:
    redacted = text
    for pattern in _CREDENTIAL_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    if not credential_context:
        return redacted
    return _OPAQUE_CREDENTIAL_CANDIDATE.sub(
        lambda match: (
            replacement
            if _is_high_confidence_opaque_credential(match.group(0))
            else match.group(0)
        ),
        redacted,
    )


def redact_credentials_in_value(
    value: Any,
    replacement: str = "[REDACTED]",
    *,
    credential_context: bool = False,
) -> Any:
    """Redact credentials in JSON-like data while preserving its visible shape.

    Tool events are diagnostic records: a secret-bearing leaf must not make the
    whole tool call disappear.  Field names stay visible, pagination tokens stay
    usable, and only credential values are replaced.
    """
    if isinstance(value, Mapping):
        projected: dict[Any, Any] = {}
        for key, item in value.items():
            key_text = key if isinstance(key, str) else ""
            child_context = credential_context or (
                bool(key_text)
                and is_sensitive_field_name(key_text)
                and not _is_pagination_token_field_name(key_text)
            )
            safe_key = (
                redact_credentials(key_text, replacement)
                if isinstance(key, str)
                else key
            )
            projected[safe_key] = redact_credentials_in_value(
                item,
                replacement,
                credential_context=child_context,
            )
        return projected
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            redact_credentials_in_value(
                item,
                replacement,
                credential_context=credential_context,
            )
            for item in value
        ]
    if isinstance(value, str):
        if credential_context and value:
            return replacement
        return redact_credentials(value, replacement)
    if credential_context and value is not None:
        return replacement
    return value


def _is_pagination_token_field_name(value: str) -> bool:
    normalized = "".join(character for character in value.casefold() if character.isalnum())
    return normalized in {
        "continuationtoken",
        "nextpagetoken",
        "pagetoken",
        "previouspagetoken",
        "prevpagetoken",
    }


def is_sensitive_field_name(value: str) -> bool:
    normalized = "".join(character for character in value.casefold() if character.isalnum())
    return (
        normalized.endswith("token")
        or normalized.endswith("password")
        or normalized.endswith("secret")
        or normalized.endswith("cookie")
        or normalized.endswith("authorization")
        or normalized.endswith("apikey")
        or normalized.endswith("privatekey")
        or normalized.endswith("accesskey")
        or normalized.endswith("credential")
        or normalized.endswith("credentials")
        or normalized.endswith("signedurl")
        or normalized.endswith("signature")
    )


def contains_local_runtime_leak(text: str) -> bool:
    if any(prefix in text for prefix in forbidden_path_prefixes()):
        return True
    return any(path in text for path in ("/tmp/", "/var/", "/private/var/"))


def contains_forbidden_leak(text: str) -> bool:
    if contains_credential(text):
        return True
    lowered = text.lower()
    if any(marker.lower() in lowered for marker in FORBIDDEN_MARKERS):
        return True
    if "[1]" in text or "【1】" in text:
        return True
    return contains_local_runtime_leak(text)


def redact_forbidden_leak_markers(text: str, replacement: str = "相关内容") -> str:
    redacted = text
    for marker in sorted(FORBIDDEN_MARKERS, key=len, reverse=True):
        redacted = _replace_case_insensitive(redacted, marker, replacement)
    for marker in ("[1]", "【1】", "/tmp/", "/var/", "/private/var/"):
        redacted = redacted.replace(marker, replacement)
    return " ".join(redacted.split())


def _replace_case_insensitive(text: str, target: str, replacement: str) -> str:
    if not target:
        return text
    lowered = text.lower()
    target_lowered = target.lower()
    pieces: list[str] = []
    start = 0
    while True:
        index = lowered.find(target_lowered, start)
        if index < 0:
            pieces.append(text[start:])
            return "".join(pieces)
        pieces.append(text[start:index])
        pieces.append(replacement)
        start = index + len(target)
