from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.agent_cron.options import ScheduledTaskOptionService
from app.agent_cron.seeds import seed_scheduled_tasks
from app.agent_runtime_contracts import (
    PROBE_VERIFIED_RUNTIME_CAPABILITIES,
    RuntimeCapabilitySnapshot,
)
from app.managed_skills import (
    REPOSITORY_IMPORT_SOURCE,
    RuntimeSkillSnapshot,
    import_repository_managed_skills,
)
from app.skill_files import SkillFileService
from app.store import AutoReplyStore


NOW = datetime(2026, 9, 8, 19, 0, tzinfo=UTC)


def _snapshot(route_name: str, *, healthy: bool) -> RuntimeCapabilitySnapshot:
    return RuntimeCapabilitySnapshot(
        route_name=route_name,
        capabilities=PROBE_VERIFIED_RUNTIME_CAPABILITIES,
        healthy=healthy,
        checked_at=NOW.isoformat(),
        expires_at=(NOW + timedelta(minutes=5)).isoformat(),
    )


def _options(
    tmp_path: Path,
    store: AutoReplyStore,
    *,
    healthy_routes: set[str],
) -> ScheduledTaskOptionService:
    import_repository_managed_skills(store)
    skill = store.get_managed_skill_by_name("ceo-minutes-sync")
    assert skill is not None
    revision = store.list_managed_skill_revisions(skill.id)[0]
    assert revision.source == REPOSITORY_IMPORT_SOURCE
    config = store.get_pending_or_active_runtime_skill_config()
    assert config is not None
    return ScheduledTaskOptionService(
        store=store,
        environment={
            "CEO_AGENT_RUNTIME_ROUTES": "claude_api,codex_oauth",
            "CEO_CLAUDE_API_KEY": "test-secret",
            "CEO_CLAUDE_MODEL": "sonnet",
            "CEO_CODEX_MODEL": "gpt-5.6-sol",
        },
        runtime_snapshots={
            route: _snapshot(route, healthy=route in healthy_routes)
            for route in ("claude_api", "codex_oauth")
        },
        operation_skill_files=SkillFileService(tmp_path / "operation-skills"),
        runtime_skill_snapshot=RuntimeSkillSnapshot(config.id, (revision,)),
        now=lambda: NOW,
    )


def test_seed_creates_daily_minutes_task_with_healthy_runtime_and_exact_revision(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "seed.sqlite3")
    options = _options(tmp_path, store, healthy_routes={"codex_oauth"})

    seeded = seed_scheduled_tasks(
        store=store,
        options=options,
        working_directory=tmp_path,
        now=NOW,
    )

    assert seeded == store.list_scheduled_tasks()
    assert len(seeded) == 1
    task = seeded[0]
    assert task.migration_key == "ceo-minutes-sync-daily-v1"
    assert task.name == "每天同步 AI 听记"
    assert task.cron_expression == "0 0 20 * * *"
    assert task.timezone_name == "Asia/Shanghai"
    assert task.runtime_id == "codex_oauth"
    assert task.runtime_options == {"model": "gpt-5.6-sol"}
    assert task.working_directory == str(tmp_path.resolve())
    assert task.enabled is True
    assert len(task.skill_refs) == 1
    ref = task.skill_refs[0]
    assert ref.skill_source == "managed"
    assert ref.skill_name == "ceo-minutes-sync"
    revision = store.get_managed_skill_revision(ref.managed_revision_id)
    assert revision is not None
    assert revision.source == REPOSITORY_IMPORT_SOURCE


def test_seed_is_idempotent_and_preserves_user_edits(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "idempotent.sqlite3")
    options = _options(tmp_path, store, healthy_routes={"codex_oauth"})
    original = seed_scheduled_tasks(
        store=store, options=options, working_directory=tmp_path, now=NOW
    )[0]
    edited = store.update_scheduled_task(
        original.id,
        expected_version=original.version,
        name="我修改后的听记同步",
        cron_expression="0 30 21 * * *",
        now=NOW + timedelta(minutes=1),
    )

    repeated = seed_scheduled_tasks(
        store=store,
        options=options,
        working_directory=tmp_path / "different",
        now=NOW + timedelta(minutes=2),
    )

    assert repeated == (edited,)
    assert store.list_scheduled_tasks() == (edited,)


def test_seed_without_healthy_runtime_is_disabled_with_visible_reason(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "disabled.sqlite3")
    options = _options(tmp_path, store, healthy_routes=set())

    task = seed_scheduled_tasks(
        store=store, options=options, working_directory=tmp_path, now=NOW
    )[0]

    assert task.enabled is False
    assert task.runtime_id == "claude_api"
    assert "未启用" in task.prompt
    assert "没有健康且已配置的 Runtime" in task.prompt
    assert "snapshot_unhealthy" in task.prompt
    assert task.runtime_id in {
        option.route_name for option in options.list_runtime_options()
    }
    assert store.list_scheduled_task_runs(task.id) == ()


def test_seed_is_disabled_when_exact_repository_revision_is_not_loaded(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "revision-not-loaded.sqlite3")
    existing_skill = store.create_managed_skill("ceo-existing", "Existing Skill")
    existing_revision = store.create_managed_skill_revision(
        existing_skill.id,
        "---\nname: ceo-existing\ndescription: Existing\n"
        "metadata:\n  managed_by: ceo-agent-service\n---\n\n# Existing\n",
        source="settings",
    )
    existing_config = store.create_runtime_skill_config(
        {existing_skill.id: existing_revision.id}, expected_parent_id=None
    )
    import_repository_managed_skills(store)
    bindings_before = store.list_runtime_skill_bindings(existing_config.id)
    options = ScheduledTaskOptionService(
        store=store,
        environment={
            "CEO_AGENT_RUNTIME_ROUTES": "claude_api,codex_oauth",
            "CEO_CLAUDE_API_KEY": "test-secret",
            "CEO_CLAUDE_MODEL": "sonnet",
            "CEO_CODEX_MODEL": "gpt-5.6-sol",
        },
        runtime_snapshots={
            route: _snapshot(route, healthy=route == "codex_oauth")
            for route in ("claude_api", "codex_oauth")
        },
        operation_skill_files=SkillFileService(tmp_path / "operation-skills"),
        runtime_skill_snapshot=RuntimeSkillSnapshot(
            existing_config.id, (existing_revision,)
        ),
        now=lambda: NOW,
    )

    task = seed_scheduled_tasks(
        store=store, options=options, working_directory=tmp_path, now=NOW
    )[0]

    assert task.enabled is False
    assert task.runtime_id == "codex_oauth"
    assert "managed_revision_not_loaded" in task.prompt
    assert store.get_pending_or_active_runtime_skill_config() == existing_config
    assert store.list_runtime_skill_bindings(existing_config.id) == bindings_before
    ref = task.skill_refs[0]
    revision = store.get_managed_skill_revision(ref.managed_revision_id)
    assert revision is not None
    assert revision.source == REPOSITORY_IMPORT_SOURCE
