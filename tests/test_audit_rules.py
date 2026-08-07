from pathlib import Path

import pytest

from app.audit_rules import (
    read_audit_rules_template,
    render_audit_rules,
    write_audit_rules_template,
)
from app.developer_prompt import DeveloperPromptTemplateError
from app.store import AgentRole


def test_same_saved_rules_render_under_fixed_role_wrappers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    path = tmp_path / "audit_rules.md"
    path.write_text("Check publication authority.", encoding="utf-8")
    monkeypatch.setenv("CEO_AUDIT_RULES_TEMPLATE_PATH", str(path))

    consumer = render_audit_rules(AgentRole.CONSUMER)
    audit = render_audit_rules(AgentRole.AUDIT)

    assert "Check publication authority." in consumer
    assert "Check publication authority." in audit
    assert "do not execute" in consumer
    assert "do not rewrite the candidate" in audit


def test_empty_custom_body_keeps_fixed_role_wrappers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    path = tmp_path / "audit_rules.md"
    path.write_text("", encoding="utf-8")
    monkeypatch.setenv("CEO_AUDIT_RULES_TEMPLATE_PATH", str(path))

    consumer = render_audit_rules(AgentRole.CONSUMER)
    audit = render_audit_rules(AgentRole.AUDIT)

    assert "No additional configurable Audit Rules." in consumer
    assert "do not execute" in consumer
    assert "do not rewrite" in audit


def test_write_rejects_invalid_template_before_replacing_saved_rules(
    tmp_path: Path,
):
    path = tmp_path / "audit_rules.md"
    path.write_text("Keep this rule.", encoding="utf-8")

    with pytest.raises(DeveloperPromptTemplateError, match="plain text"):
        write_audit_rules_template("<var: missing_rule>", path)

    assert read_audit_rules_template(path) == "Keep this rule."


@pytest.mark.parametrize(
    "tag",
    (
        "<code: app.config:user_alias()>",
        "<file: management/rules.md>",
        "<var: principal>",
    ),
)
def test_audit_rules_reject_template_tags_as_plain_text(
    tmp_path: Path,
    tag: str,
):
    path = tmp_path / "audit_rules.md"
    path.write_text("Keep this rule.", encoding="utf-8")

    with pytest.raises(DeveloperPromptTemplateError, match="plain text"):
        write_audit_rules_template(tag, path)

    assert path.read_text(encoding="utf-8") == "Keep this rule."


def test_atomic_save_preserves_valid_file_when_temp_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    path = tmp_path / "audit_rules.md"
    path.write_text("Keep this rule.", encoding="utf-8")
    original_write_text = Path.write_text

    def fail_temp_write(self: Path, *args, **kwargs):
        if self != path:
            raise OSError("temp write failed")
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_temp_write)

    with pytest.raises(OSError, match="temp write failed"):
        write_audit_rules_template("Replacement rule.", path)

    assert path.read_text(encoding="utf-8") == "Keep this rule."


def test_atomic_save_preserves_valid_file_when_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    path = tmp_path / "audit_rules.md"
    path.write_text("Keep this rule.", encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr("app.audit_rules.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        write_audit_rules_template("Replacement rule.", path)

    assert path.read_text(encoding="utf-8") == "Keep this rule."
    assert list(tmp_path.glob("*.tmp")) == []
