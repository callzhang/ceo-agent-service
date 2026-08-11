from app.agent_skill_usage import LoadedSkillReceipt, loaded_skill_receipts


SHA_A = "a" * 64
SHA_B = "b" * 64


def _event(
    *,
    path: str = "/Users/derek/.agents/skills/business-review/SKILL.md",
    name: str = "business-review",
    sha256: str = SHA_A,
    event_type: str = "item.completed",
    status: str = "completed",
    server: str = "agent_cli",
    tool: str = "read_skill",
) -> dict[str, object]:
    return {
        "type": event_type,
        "item": {
            "type": "mcp_tool_call",
            "server": server,
            "tool": tool,
            "status": status,
            "metadata": {
                "skill_path": path,
                "skill_name": name,
                "skill_sha256": sha256,
            },
        },
    }


def test_loaded_skill_receipts_use_only_verified_normalized_completed_metadata():
    valid = _event()
    authored_raw_result = {
        "type": "item.completed",
        "item": {
            "type": "mcp_tool_call",
            "server": "agent_cli",
            "tool": "read_skill",
            "status": "completed",
            "arguments": {"path": "/tmp/untrusted/SKILL.md"},
            "result": {"sha256": SHA_B, "content": "agent-authored"},
        },
    }
    malformed = (
        _event(sha256="UPPERCASE"),
        _event(path="relative/SKILL.md"),
        _event(name=""),
        _event(event_type="item.started", status="in_progress"),
        _event(event_type="item.failed", status="failed"),
        _event(server="other"),
        _event(tool="execute_reviewed_read"),
    )

    assert loaded_skill_receipts((valid, authored_raw_result, *malformed)) == (
        LoadedSkillReceipt(
            name="business-review",
            path="/Users/derek/.agents/skills/business-review/SKILL.md",
            sha256=SHA_A,
        ),
    )


def test_loaded_skill_receipts_deduplicate_by_resolved_path_deterministically(
    tmp_path,
):
    first = tmp_path / "first" / "SKILL.md"
    second = tmp_path / "second" / "SKILL.md"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")

    events = (
        _event(path=str(second), name="second", sha256=SHA_B),
        _event(path=str(first), name="first", sha256=SHA_A),
        _event(path=str(first.parent / "." / "SKILL.md"), name="first", sha256=SHA_A),
    )

    assert loaded_skill_receipts(events) == (
        LoadedSkillReceipt(name="first", path=str(first.resolve()), sha256=SHA_A),
        LoadedSkillReceipt(name="second", path=str(second.resolve()), sha256=SHA_B),
    )
