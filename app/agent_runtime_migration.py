"""Move the retired built-in API route settings onto added-route settings.

Until 2026-09-24 `codex_api` and `claude_api` were built-in routes with fixed
settings (`CEO_CODEX_API_*`, `CEO_CLAUDE_API_*`). They are now ordinary added
routes (`CEO_RUNTIME_<NAME>_*`) that can be renamed. This one-time migration
rewrites an `.env` that still carries the old keys; the service supervisor
runs it in its own process before it starts the worker, web and email
children, so every child reads the migrated file.

A route keeps its name (`codex_api`, `claude_api`), so scheduled tasks,
resumable sessions and route pauses that name it stay valid. A route that is
not in `CEO_AGENT_RUNTIME_ROUTES` is not migrated: an added route exists only
while it is listed, so its old settings are dropped (they remain in the
backup). The retired names leave `CEO_AGENT_RUNTIME_HIDDEN_ROUTES`, which only
records deleted built-in cards.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.agent_runtime_config import (
    DEFAULT_CEO_CLAUDE_MODEL,
    DEFAULT_CODEX_API_BASE_URL,
    added_route_settings_prefix,
)
from app.config import (
    DEFAULT_CEO_CODEX_MODEL,
    env_file_path,
    read_env_file,
    write_env_values,
)


BACKUP_SUFFIX = ".runtime-routes-migration.bak"
RETIRED_CODEX_API_KEYS = (
    "CEO_CODEX_API_BASE_URL",
    "CEO_CODEX_API_MODEL",
    "CEO_CODEX_API_KEY",
)
RETIRED_CLAUDE_API_KEYS = ("CEO_CLAUDE_API_MODEL", "CEO_CLAUDE_API_KEY")


def migrate_retired_api_routes(path: Path | None = None) -> dict[str, list[str]]:
    """Rewrite the env file once; return what moved, by key name only.

    Returns an empty report when the file carries none of the retired keys
    and no retired name in the hidden list, so running it again is a no-op.
    """

    env_path = path or env_file_path()
    persisted = read_env_file(env_path)
    retired = [
        key
        for key in (*RETIRED_CODEX_API_KEYS, *RETIRED_CLAUDE_API_KEYS)
        if key in persisted
    ]
    routes = _names(persisted.get("CEO_AGENT_RUNTIME_ROUTES", ""))
    hidden = _names(persisted.get("CEO_AGENT_RUNTIME_HIDDEN_ROUTES", ""))
    retired_names = ("codex_api", "claude_api")
    kept_hidden = [name for name in hidden if name not in retired_names]
    if not retired and kept_hidden == hidden:
        return {}

    def value(key: str, default: str) -> str:
        return persisted.get(key, "").strip() or default

    moved = {
        "codex_api": {
            "KIND": "codex_api",
            "BASE_URL": value("CEO_CODEX_API_BASE_URL", DEFAULT_CODEX_API_BASE_URL),
            "MODEL": value(
                "CEO_CODEX_API_MODEL",
                value("CEO_CODEX_MODEL", DEFAULT_CEO_CODEX_MODEL),
            ),
            "API_KEY": value("CEO_CODEX_API_KEY", ""),
        },
        "claude_api": {
            "KIND": "claude_api",
            "BASE_URL": "",
            "MODEL": value(
                "CEO_CLAUDE_API_MODEL",
                value("CEO_CLAUDE_MODEL", DEFAULT_CEO_CLAUDE_MODEL),
            ),
            "API_KEY": value("CEO_CLAUDE_API_KEY", ""),
        },
    }
    updates: dict[str, str] = {}
    report: dict[str, list[str]] = {"removed": list(retired), "written": []}
    for name in retired_names:
        if name not in routes:
            continue
        prefix = added_route_settings_prefix(name)
        for suffix, setting in moved[name].items():
            updates[f"{prefix}{suffix}"] = setting
            report["written"].append(f"{prefix}{suffix}")
    if kept_hidden != hidden:
        updates["CEO_AGENT_RUNTIME_HIDDEN_ROUTES"] = ",".join(kept_hidden)
        report["written"].append("CEO_AGENT_RUNTIME_HIDDEN_ROUTES")
    backup = env_path.with_name(env_path.name + BACKUP_SUFFIX)
    # One fixed backup name keeps only the newest copy; it is verified before
    # the env file is touched.
    shutil.copy2(env_path, backup)
    if backup.read_bytes() != env_path.read_bytes():
        raise RuntimeError(f"env backup {backup} does not match {env_path}")
    report["backup"] = [str(backup)]
    write_env_values(updates, env_path, remove=retired)
    return report


def _names(value: str) -> list[str]:
    return [name.strip() for name in value.split(",") if name.strip()]


def main() -> int:
    report = migrate_retired_api_routes()
    if report:
        print(
            "runtime route settings migrated: "
            f"removed={','.join(report['removed'])} "
            f"written={','.join(report['written'])} "
            f"backup={report['backup'][0]}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
