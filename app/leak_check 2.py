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


def contains_forbidden_leak(text: str) -> bool:
    lowered = text.lower()
    if any(marker.lower() in lowered for marker in FORBIDDEN_MARKERS):
        return True
    if "[1]" in text or "【1】" in text:
        return True
    return any(path in text for path in ("/tmp/", "/var/", "/private/var/"))


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
