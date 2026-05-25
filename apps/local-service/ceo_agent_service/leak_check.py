from ceo_agent_service.config import forbidden_path_prefixes


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
