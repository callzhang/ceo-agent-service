from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path

import pytest

import app.agent_cron.options as options_module
from app.agent_cron.commands import ServiceCommandOption
from app.agent_cron.options import (
    DownstreamSkill,
    ScheduledTaskOptionService,
    ScheduledTaskOptionUnavailableError,
)
from app.agent_runtime_contracts import (
    PROBE_VERIFIED_RUNTIME_CAPABILITIES,
    RuntimeCapabilitySnapshot,
    RuntimeFailure,
    RuntimeFailureClass,
)
from app.audit_agent import AuditAgentRunner
from app.business_skills import installed_business_skill_catalog
from app.codex_decision import DECISION_RUNTIME_CAPABILITIES
from app.consumer_agent import (
    CONSUMER_BASE_RUNTIME_CAPABILITIES,
    CONSUMER_ROLE_BOUNDARY,
    ConsumerAgentRunner,
)
from app.managed_skills import RuntimeSkillSnapshot
from app.skill_files import SkillFileService
from app.store import AutoReplyStore
from app.wechat.decision_runner import WechatDecisionRunner
from app.wechat.prompt import WECHAT_TURN_INSTRUCTIONS


NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
MANAGED_V1 = """---
name: ceo-test
description: First revision
metadata:
  managed_by: ceo-agent-service
---

# First
"""
MANAGED_V2 = MANAGED_V1.replace("First revision", "Second revision").replace(
    "# First", "# Second"
)


def _snapshot(
    route_name: str,
    *,
    healthy: bool,
    failure: RuntimeFailure | None = None,
    capabilities: frozenset[str] = PROBE_VERIFIED_RUNTIME_CAPABILITIES,
    checked_at: datetime = NOW,
    expires_at: datetime | None = None,
) -> RuntimeCapabilitySnapshot:
    return RuntimeCapabilitySnapshot(
        route_name=route_name,
        capabilities=capabilities,
        healthy=healthy,
        checked_at=checked_at.isoformat(),
        expires_at=(expires_at or NOW + timedelta(minutes=5)).isoformat(),
        failure=failure,
    )


def _runtime_environment() -> dict[str, str]:
    return {
        "CEO_AGENT_RUNTIME_ROUTES": "codex_oauth,claude_api",
        "CEO_CLAUDE_API_KEY": "secret",
        "CEO_CODEX_MODEL": "gpt-5.6-sol",
        "CEO_CLAUDE_MODEL": "sonnet",
    }


def _service(
    tmp_path: Path,
    *,
    snapshots: dict[str, RuntimeCapabilitySnapshot] | None = None,
    operation_root: Path | None = None,
    runtime_skill_snapshot: RuntimeSkillSnapshot | None = None,
    store: AutoReplyStore | None = None,
) -> ScheduledTaskOptionService:
    return ScheduledTaskOptionService(
        store=store or AutoReplyStore(tmp_path / "options.sqlite3"),
        environment=_runtime_environment(),
        runtime_snapshots=snapshots or {},
        operation_skill_files=SkillFileService(
            operation_root or tmp_path / "operation-skills"
        ),
        runtime_skill_snapshot=runtime_skill_snapshot,
        now=lambda: NOW,
    )


def test_runtime_options_include_only_configured_routes_and_keep_unhealthy_reason(
    tmp_path: Path,
) -> None:
    service = _service(
        tmp_path,
        snapshots={
            "codex_oauth": _snapshot("codex_oauth", healthy=True),
            "claude_api": _snapshot(
                "claude_api",
                healthy=False,
                failure=RuntimeFailure(
                    failure_class=RuntimeFailureClass.AUTHENTICATION,
                    code="credential_rejected",
                    detail="provider detail must not become an option reason",
                ),
            ),
            "friday_runtime": _snapshot("friday_runtime", healthy=True),
        },
    )

    options = service.list_runtime_options()

    assert [option.route_name for option in options] == ["codex_oauth", "claude_api"]
    assert options[0].available is True
    assert options[0].unavailable_reason is None
    assert options[0].runtime_kind == "codex_cli"
    assert options[0].model == "gpt-5.6-sol"
    assert options[1].available is False
    assert options[1].unavailable_reason == "snapshot_unhealthy"


def test_runtime_resolution_uses_saved_route_name_without_fallback(
    tmp_path: Path,
) -> None:
    service = _service(
        tmp_path,
        snapshots={
            "codex_oauth": _snapshot("codex_oauth", healthy=True),
            "claude_api": _snapshot("claude_api", healthy=False),
        },
    )

    assert service.resolve_runtime_route("codex_oauth").name == "codex_oauth"
    with pytest.raises(
        ScheduledTaskOptionUnavailableError,
        match="claude_api: snapshot_unhealthy",
    ):
        service.resolve_runtime_route("claude_api")
    with pytest.raises(
        ScheduledTaskOptionUnavailableError,
        match="friday_runtime: runtime_not_configured",
    ):
        service.resolve_runtime_route("friday_runtime")


@pytest.mark.parametrize(
    ("snapshot", "reason"),
    [
        (
            _snapshot(
                "codex_oauth",
                healthy=True,
                capabilities=frozenset({"structured_output"}),
            ),
            "missing_capabilities:local_schema_validation",
        ),
        (
            _snapshot(
                "codex_oauth",
                healthy=True,
                expires_at=NOW,
            ),
            "snapshot_expired",
        ),
        (
            _snapshot(
                "codex_oauth",
                healthy=True,
                checked_at=NOW + timedelta(seconds=1),
            ),
            "snapshot_invalid",
        ),
    ],
)
def test_runtime_options_require_scheduled_agent_capabilities_and_current_health(
    tmp_path: Path,
    snapshot: RuntimeCapabilitySnapshot,
    reason: str,
) -> None:
    service = _service(tmp_path, snapshots={"codex_oauth": snapshot})

    option = service.list_runtime_options()[0]

    assert option.available is False
    assert option.unavailable_reason == reason


def test_runtime_options_preserve_route_pause_reason(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "options.sqlite3")
    store.open_runtime_route_pause("codex_oauth", "capacity_exhausted", "2099-01-01T00:00:00Z")
    service = _service(
        tmp_path,
        store=store,
        snapshots={"codex_oauth": _snapshot("codex_oauth", healthy=True)},
    )

    option = service.list_runtime_options()[0]

    assert option.available is False
    assert option.unavailable_reason == "paused:capacity_exhausted"


def test_managed_options_list_every_immutable_revision_and_exact_load_state(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "options.sqlite3")
    enabled_skill = store.create_managed_skill("ceo-test", "CEO Test")
    first = store.create_managed_skill_revision(
        enabled_skill.id, MANAGED_V1, source="settings"
    )
    second = store.create_managed_skill_revision(
        enabled_skill.id, MANAGED_V2, source="settings"
    )
    disabled_skill = store.create_managed_skill("ceo-disabled", "Disabled")
    disabled_revision = store.create_managed_skill_revision(
        disabled_skill.id,
        MANAGED_V1.replace("ceo-test", "ceo-disabled"),
        source="settings",
    )
    config = store.create_runtime_skill_config(
        [
            {
                "skill_id": enabled_skill.id,
                "revision_id": first.id,
                "enabled": True,
                "load_order": 0,
                "purpose": "test",
            },
            {
                "skill_id": disabled_skill.id,
                "revision_id": disabled_revision.id,
                "enabled": False,
                "load_order": 1,
                "purpose": "test",
            },
        ],
        expected_parent_id=None,
    )
    store.record_runtime_skill_load(
        config.id,
        pid=123,
        loaded={enabled_skill.id: first.sha256},
    )
    service = ScheduledTaskOptionService(
        store=store,
        environment=_runtime_environment(),
        runtime_snapshots={},
        operation_skill_files=SkillFileService(tmp_path / "operation-skills"),
        runtime_skill_snapshot=RuntimeSkillSnapshot(
            config_id=config.id,
            revisions=(first,),
        ),
        now=lambda: NOW,
    )

    options = service.list_managed_skill_options()

    assert [option.name for option in options] == ["ceo-test", "ceo-disabled"]
    revisions = options[0].revisions
    assert [(item.revision_id, item.revision_number) for item in revisions] == [
        (first.id, 1),
        (second.id, 2),
    ]
    assert revisions[0].available is True
    assert revisions[0].unavailable_reason is None
    assert revisions[0].sha256 == first.sha256
    assert revisions[0].source == "settings"
    assert revisions[1].available is False
    assert revisions[1].unavailable_reason == "managed_revision_not_loaded"
    assert options[1].revisions[0].available is False
    assert options[1].revisions[0].unavailable_reason == "managed_skill_disabled"
    with pytest.raises(
        ScheduledTaskOptionUnavailableError,
        match=f"managed revision {second.id}: managed_revision_not_loaded",
    ):
        service.resolve_managed_skill_revision(
            skill_id=enabled_skill.id,
            revision_id=second.id,
            skill_name=enabled_skill.name,
        )
    with pytest.raises(
        ScheduledTaskOptionUnavailableError,
        match=f"managed revision {disabled_revision.id}: managed_skill_disabled",
    ):
        service.resolve_managed_skill_revision(
            skill_id=disabled_skill.id,
            revision_id=disabled_revision.id,
            skill_name=disabled_skill.name,
        )


def test_managed_availability_uses_injected_process_snapshot_not_database_active(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "options.sqlite3")
    skill = store.create_managed_skill("ceo-test", "CEO Test")
    first = store.create_managed_skill_revision(skill.id, MANAGED_V1, source="settings")
    second = store.create_managed_skill_revision(skill.id, MANAGED_V2, source="settings")
    first_config = store.create_runtime_skill_config(
        {skill.id: first.id}, expected_parent_id=None
    )
    store.record_runtime_skill_load(
        first_config.id, pid=123, loaded={skill.id: first.sha256}
    )
    process_snapshot = RuntimeSkillSnapshot(first_config.id, (first,))
    second_config = store.create_runtime_skill_config(
        {skill.id: second.id}, expected_parent_id=first_config.id
    )
    store.record_runtime_skill_load(
        second_config.id, pid=456, loaded={skill.id: second.sha256}
    )
    service = _service(
        tmp_path,
        store=store,
        runtime_skill_snapshot=process_snapshot,
    )

    revisions = service.list_managed_skill_options()[0].revisions

    assert revisions[0].available is True
    assert revisions[1].available is False
    assert revisions[1].unavailable_reason == "managed_revision_not_loaded"
    assert service.resolve_managed_skill_revision(
        skill_id=skill.id,
        revision_id=first.id,
        skill_name=skill.name,
    ) == first


def test_managed_revision_is_unavailable_without_current_process_snapshot(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "options.sqlite3")
    skill = store.create_managed_skill("ceo-test", "CEO Test")
    revision = store.create_managed_skill_revision(skill.id, MANAGED_V1, source="settings")
    config = store.create_runtime_skill_config({skill.id: revision.id}, expected_parent_id=None)
    store.record_runtime_skill_load(config.id, pid=123, loaded={skill.id: revision.sha256})
    service = _service(tmp_path, store=store, runtime_skill_snapshot=None)

    option = service.list_managed_skill_options()[0].revisions[0]

    assert option.available is False
    assert option.unavailable_reason == "runtime_skill_snapshot_missing"
    with pytest.raises(
        ScheduledTaskOptionUnavailableError,
        match=f"managed revision {revision.id}: runtime_skill_snapshot_missing",
    ):
        service.resolve_managed_skill_revision(
            skill_id=skill.id,
            revision_id=revision.id,
            skill_name=skill.name,
        )


def test_managed_revision_resolution_rejects_missing_or_disabled_exact_revision(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    with pytest.raises(
        ScheduledTaskOptionUnavailableError,
        match="managed revision 88: managed_revision_missing",
    ):
        service.resolve_managed_skill_revision(
            skill_id=77,
            revision_id=88,
            skill_name="ceo-missing",
        )


def test_operation_options_report_source_summary_hash_and_invalid_availability(
    tmp_path: Path,
) -> None:
    root = tmp_path / "operation-skills"
    valid = root / "dingtalk-chat" / "SKILL.md"
    valid.parent.mkdir(parents=True)
    content = """---
name: dingtalk-chat
description: >-
  Read and send
  DingTalk messages
metadata:
  category: product
  requires:
    bins:
      - dws
---

# DingTalk Chat
"""
    valid.write_text(content, encoding="utf-8")
    invalid = root / "broken" / "SKILL.md"
    invalid.parent.mkdir(parents=True)
    invalid.write_text("not frontmatter", encoding="utf-8")
    lark = root / "lark-im" / "SKILL.md"
    lark.parent.mkdir(parents=True)
    lark.write_text(
        """---
name: lark-im
description: |
  Read Lark messages.
  Send a reply.
metadata:
  requires:
    apps:
      - lark
---
# Lark IM
""",
        encoding="utf-8",
    )
    service = _service(tmp_path, operation_root=root)

    options = service.list_operation_skill_options()

    assert [option.name for option in options] == ["broken", "dingtalk-chat", "lark-im"]
    assert options[0].available is False
    assert options[0].unavailable_reason == "operation_skill_invalid"
    assert options[1].available is True
    assert options[1].unavailable_reason is None
    assert options[1].content_summary == "Read and send DingTalk messages"
    assert options[1].source == str(valid)
    assert options[1].sha256 == hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert service.resolve_operation_skill("dingtalk-chat").sha256 == options[1].sha256
    assert options[2].content_summary == "Read Lark messages.\nSend a reply."


def test_operation_catalog_excludes_service_managed_skill_files(tmp_path: Path) -> None:
    root = tmp_path / "operation-skills"
    managed = root / "ceo-test" / "SKILL.md"
    managed.parent.mkdir(parents=True)
    managed.write_text(MANAGED_V1, encoding="utf-8")
    operation = root / "dingtalk-minutes" / "SKILL.md"
    operation.parent.mkdir(parents=True)
    operation.write_text(
        """---
name: dingtalk-minutes
description: Read meeting minutes
metadata:
  category: product
---
# Minutes
""",
        encoding="utf-8",
    )
    service = _service(tmp_path, operation_root=root)

    options = service.list_operation_skill_options()

    assert [option.name for option in options] == ["dingtalk-minutes"]
    with pytest.raises(
        ScheduledTaskOptionUnavailableError,
        match="operation_skill_unavailable",
    ):
        service.resolve_operation_skill("ceo-test")


def test_operation_skill_accepts_standard_frontmatter_without_metadata(
    tmp_path: Path,
) -> None:
    root = tmp_path / "operation-skills"
    path = root / "agent-browser-core" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        """---
name: agent-browser-core
description: OpenClaw skill for browser automation.
---
# Agent Browser Skill
""",
        encoding="utf-8",
    )
    service = _service(tmp_path, operation_root=root)

    option = service.list_operation_skill_options()[0]

    assert option.name == "agent-browser-core"
    assert option.available is True
    assert option.content_summary == "OpenClaw skill for browser automation."


@pytest.mark.parametrize(
    ("directory_name", "public_name"),
    (
        ("nuwa", "huashu-nuwa"),
        ("stardust-sre", "production-devops-sre"),
    ),
)
def test_operation_catalog_uses_frontmatter_name_as_public_identity(
    tmp_path: Path,
    directory_name: str,
    public_name: str,
) -> None:
    root = tmp_path / "operation-skills"
    path = root / directory_name / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        f"---\nname: {public_name}\ndescription: Aliased operation Skill\n---\n# Skill\n",
        encoding="utf-8",
    )
    service = _service(tmp_path, operation_root=root)

    option = service.list_operation_skill_options()[0]

    assert option.name == public_name
    assert option.source == str(path)
    assert service.resolve_operation_skill(public_name).path == path
    with pytest.raises(
        ScheduledTaskOptionUnavailableError,
        match="operation_skill_unavailable",
    ):
        service.resolve_operation_skill(directory_name)


def test_duplicate_operation_public_name_is_one_stable_unavailable_choice(
    tmp_path: Path,
) -> None:
    root = tmp_path / "operation-skills"
    paths = []
    for directory_name in ("first", "second"):
        path = root / directory_name / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(
            "---\nname: shared-name\ndescription: Duplicate\n---\n# Skill\n",
            encoding="utf-8",
        )
        paths.append(path)
    service = _service(tmp_path, operation_root=root)

    options = service.list_operation_skill_options()

    assert len(options) == 1
    assert options[0].name == "shared-name"
    assert options[0].available is False
    assert options[0].unavailable_reason == "operation_skill_name_conflict"
    assert options[0].source == ";".join(str(path) for path in paths)
    with pytest.raises(
        ScheduledTaskOptionUnavailableError,
        match="operation_skill_name_conflict",
    ):
        service.resolve_operation_skill("shared-name")


def test_operation_catalog_does_not_follow_symlinks_or_treat_names_as_paths(
    tmp_path: Path,
) -> None:
    root = tmp_path / "operation-skills"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text(
        "---\nname: escaped\ndescription: Outside\n---\n# Skill\n",
        encoding="utf-8",
    )
    root.mkdir()
    (root / "alias").symlink_to(outside, target_is_directory=True)
    service = _service(tmp_path, operation_root=root)

    assert service.list_operation_skill_options() == ()
    for supplied_name in ("escaped", "alias", "../outside"):
        with pytest.raises(
            ScheduledTaskOptionUnavailableError,
            match="operation_skill_unavailable",
        ):
            service.resolve_operation_skill(supplied_name)


@pytest.mark.parametrize(
    "frontmatter",
    (
        "- name\n- description\n",
        "name: broken\ndescription: Broken\nmetadata: scalar\n",
        "name: true\ndescription: Broken\nmetadata: {}\n",
        "name: broken\ndescription: 42\nmetadata: {}\n",
    ),
)
def test_operation_skill_requires_typed_standard_frontmatter(
    tmp_path: Path, frontmatter: str
) -> None:
    root = tmp_path / "operation-skills"
    path = root / "broken" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(f"---\n{frontmatter}---\n# Broken\n", encoding="utf-8")

    option = _service(tmp_path, operation_root=root).list_operation_skill_options()[0]

    assert option.name == "broken"
    assert option.available is False
    assert option.unavailable_reason == "operation_skill_invalid"


def test_service_command_downstream_reports_real_channel_consumer_and_routes(
    tmp_path: Path,
) -> None:
    service = _service(
        tmp_path, snapshots={"codex_oauth": _snapshot("codex_oauth", healthy=True)}
    )

    (
        dingtalk, wechat, meeting, oa, work_sources, minutes, okr, recovery
    ) = service.list_service_command_options()

    assert (dingtalk.name, dingtalk.channel) == ("produce-once", "dingtalk")
    assert (wechat.name, wechat.channel) == ("wechat-produce-once", "wechat")
    assert (meeting.name, meeting.channel) == ("scan-meetings-once", "meeting")
    assert (oa.name, oa.channel) == ("scan-oa-approvals", "dingtalk")
    assert (work_sources.name, work_sources.channel) == (
        "scan-work-sources-once",
        "work_summary",
    )
    assert (minutes.name, minutes.channel) == ("sync-minutes-once", "work_summary")
    assert (recovery.name, recovery.channel) == (
        "recover-recent-messages",
        "dingtalk",
    )
    assert (okr.name, okr.channel) == ("weekly-okr-report", "dingtalk")
    assert dingtalk.downstream.channel == "dingtalk"
    assert dingtalk.downstream.consumer_runners == (
        ConsumerAgentRunner.__name__,
        AuditAgentRunner.__name__,
    )
    assert dingtalk.downstream.instructions == CONSUMER_ROLE_BOUNDARY
    assert dingtalk.downstream.required_capabilities == tuple(
        sorted(CONSUMER_BASE_RUNTIME_CAPABILITIES)
    )
    assert dingtalk.downstream.loads_skills is True
    assert wechat.downstream.channel == "wechat"
    assert wechat.downstream.consumer_runners == (WechatDecisionRunner.__name__,)
    assert wechat.downstream.instructions == WECHAT_TURN_INSTRUCTIONS
    assert wechat.downstream.required_capabilities == tuple(
        sorted(DECISION_RUNTIME_CAPABILITIES)
    )
    assert wechat.downstream.loads_skills is False
    assert wechat.downstream.skills == ()
    assert wechat.downstream.skills_from_runtime_snapshot is False
    assert meeting.downstream.channel == "meeting"
    assert meeting.downstream.consumer_runners == ("MeetingAlignmentCodexRunner",)
    assert meeting.downstream.loads_skills is False
    assert meeting.downstream.skills == ()
    assert meeting.downstream.skills_from_runtime_snapshot is False
    assert meeting.downstream.instructions is None
    assert meeting.downstream.instructions is None
    assert oa.downstream.channel == "dingtalk"
    assert oa.downstream.consumer_runners == (
        ConsumerAgentRunner.__name__,
        AuditAgentRunner.__name__,
    )
    assert work_sources.downstream.channel == "work_summary"
    assert work_sources.downstream.consumer_runners == ("TaskAgentRunner",)
    assert work_sources.downstream.loads_skills is False
    assert work_sources.downstream.skills == ()
    assert work_sources.downstream.instructions is None
    for listing, required in (
        (dingtalk, CONSUMER_BASE_RUNTIME_CAPABILITIES),
        (wechat, DECISION_RUNTIME_CAPABILITIES),
        (meeting, frozenset({"structured_output", "local_schema_validation"})),
        (oa, CONSUMER_BASE_RUNTIME_CAPABILITIES),
        (work_sources, frozenset({"structured_output", "local_schema_validation"})),
    ):
        expected_routes = [
            (option.route_name, option.model, option.available, option.unavailable_reason)
            for option in service.list_runtime_options(required_capabilities=required)
        ]
        assert expected_routes == [
            ("codex_oauth", "gpt-5.6-sol", True, None),
            ("claude_api", "sonnet", False, "snapshot_missing"),
        ]
        assert [
            (route.route_name, route.model, route.available, route.unavailable_reason)
            for route in listing.downstream.runtime_routes
        ] == expected_routes


def test_service_command_downstream_routes_follow_the_consumer_requirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        options_module,
        "DECISION_RUNTIME_CAPABILITIES",
        DECISION_RUNTIME_CAPABILITIES | {"image_input"},
    )
    service = _service(
        tmp_path, snapshots={"codex_oauth": _snapshot("codex_oauth", healthy=True)}
    )

    dingtalk, wechat, *_rest = service.list_service_command_options()

    assert wechat.downstream.required_capabilities == tuple(
        sorted(DECISION_RUNTIME_CAPABILITIES | {"image_input"})
    )
    assert [
        (route.route_name, route.available, route.unavailable_reason)
        for route in wechat.downstream.runtime_routes
    ] == [
        ("codex_oauth", False, "missing_capabilities:image_input"),
        ("claude_api", False, "snapshot_missing"),
    ]
    assert dingtalk.downstream.runtime_routes[0].available is True


def test_service_command_downstream_rejects_an_unsupported_channel(
    tmp_path: Path,
) -> None:
    option = ServiceCommandOption(
        name="email-produce-once",
        display_name="Email",
        description="unsupported",
        channel="email",  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="unsupported service command channel: email"):
        _service(tmp_path).describe_service_command_downstream(option)


def test_dingtalk_downstream_skills_follow_process_snapshot_then_installed_catalog(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "options.sqlite3")
    skill = store.create_managed_skill("ceo-test", "CEO Test")
    revision = store.create_managed_skill_revision(
        skill.id, MANAGED_V1, source="settings"
    )
    config = store.create_runtime_skill_config(
        {skill.id: revision.id}, expected_parent_id=None
    )

    with_snapshot = _service(
        tmp_path,
        store=store,
        runtime_skill_snapshot=RuntimeSkillSnapshot(config.id, (revision,)),
    ).list_service_command_options()[0].downstream
    without_snapshot = _service(tmp_path, store=store).list_service_command_options()[
        0
    ].downstream

    assert with_snapshot.skills == (
        DownstreamSkill(
            name="ceo-test", revision_id=revision.id, revision_number=1
        ),
    )
    assert with_snapshot.loads_skills is True
    assert with_snapshot.skills_from_runtime_snapshot is True
    assert without_snapshot.skills == tuple(
        DownstreamSkill(name=entry.name, revision_id=None, revision_number=None)
        for entry in installed_business_skill_catalog()
    )
    assert without_snapshot.skills
    assert without_snapshot.loads_skills is True
    assert without_snapshot.skills_from_runtime_snapshot is False
