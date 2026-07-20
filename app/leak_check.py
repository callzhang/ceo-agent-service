import re

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
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{6,}\b", re.IGNORECASE),
    re.compile(
        r"\b[A-Za-z0-9_.-]*(?:password|token|api[_-]?key|private[_-]?key|secret)"
        r"\s*[:=]\s*(?:['\"])?[^\s'\"<>]{4,}",
        re.IGNORECASE,
    ),
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", re.IGNORECASE),
)


def contains_credential(text: str) -> bool:
    return any(pattern.search(text) for pattern in _CREDENTIAL_PATTERNS)


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
