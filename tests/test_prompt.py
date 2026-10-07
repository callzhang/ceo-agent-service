import hashlib
from pathlib import Path

import pytest

from app.dingtalk_models import DingTalkMessage
from app.config import env_file_path
from app.config import profile_evidence_dir
from app.config import repo_root
from app.config import work_profile_path
from app.developer_prompt import (
    CONFIGURABLE_PROMPT_VARIABLE_DEFAULTS,
    DeveloperPromptTemplateError,
    SEED_DEVELOPER_PROMPT_TEMPLATE,
    SEED_USER_PROMPT_TEMPLATE,
    render_consumer_task_template,
    write_user_prompt_template,
    configurable_prompt_variable_pairs,
    developer_prompt_template_path,
    prompt_template_variables,
    read_developer_prompt_template,
    read_user_prompt_template,
    render_developer_prompt_template,
    user_prompt_template_path,
    write_configurable_prompt_variables,
)
from app.prompt import (
    LinkedDocumentContext,
    MaterialReferenceContext,
    linked_document_lines,
    material_reference_lines,
    message_lines,
    runtime_context_instruction,
    sanitize_dingtalk_prompt_text,
    write_work_profile,
    work_profile_instruction,
)
from app.consumer_agent import (
    AGENT_CAPABILITY_INSTRUCTIONS,
)
from app.business_skills import bundled_business_skills_root
from app.user_prompt_blocks import USER_PROMPT_BLOCKS


def test_runtime_context_injects_deployment_values_without_skill_literals(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    skills = tmp_path / "installed-skills"
    monkeypatch.setenv("CEO_WORKSPACE", str(workspace))
    monkeypatch.setenv("CEO_SKILLS_ROOT", str(skills))
    monkeypatch.setenv("USER_ALIAS", "Configured Principal")

    rendered = runtime_context_instruction()

    assert "Configured Principal" in rendered
    assert str(workspace) in rendered
    assert str(skills) in rendered
    assert "CEO_WORKSPACE" not in rendered
    assert "CEO_SKILLS_ROOT" not in rendered

SKILLS_ROOT = bundled_business_skills_root()


def test_consumer_oa_work_uses_live_identity_and_autonomous_business_rules():
    instructions = " ".join(AGENT_CAPABILITY_INSTRUCTIONS.split())
    assert "Use originatorUserid/originatorOpenDingTalkId" in instructions
    assert "A low-consequence operating choice is autonomous" in instructions
    assert "A real provider read outage stays failed" in instructions
    assert "Ordinary work does not require a controlled-action proposal" in instructions
    assert "Only the registered reviewed system actions" in instructions
    assert "after whole-candidate approval" in instructions


CARD_CONTENT = """@Alex Chen(明哥) 明哥，董事会报告根据昨天的会议进行了修改，您是否已完成审核？是否可以定稿了？
  引用: 26年董事会报告
![image](https://gw.alicdn.com/imgextra/i4/O1CN019r2O9o1mRbjrcNMe5_!!6000000004951-2-tps-96-54.png)
![image](https://gw.alicdn.com/imgextra/i4/O1CN01DXenu91IyBR0wQXk9_!!6000000000961-2-tps-148-72.png)
![image](https://gw.alicdn.com/imgextra/i4/O1CN01DXenu91IyBR0wQXk9_!!6000000000961-2-tps-148-72.png)
[https://alidocs.dingtalk.com/i/nodes/vy20BglGWOKXmP5zs0OGQn6DWA7depqY?corpId=ding8ffc70a4ef94915f35c2f4657eb6378f&utm_medium=im_card&utm_source=im](https://alidocs.dingtalk.com/i/nodes/vy20BglGWOKXmP5zs0OGQn6DWA7depqY?corpId=ding8ffc70a4ef94915f35c2f4657eb6378f&utm_medium=im_card&utm_source=im)"""
PERSONNEL_SKILL_PATH = (
    SKILLS_ROOT
    / "ceo-personnel-communication"
    / "SKILL.md"
)


def _personnel_skill_prose() -> str:
    return " ".join(PERSONNEL_SKILL_PATH.read_text(encoding="utf-8").split())


@pytest.fixture(autouse=True)
def _render_prompt_tests_from_canonical_seed(monkeypatch):
    # Installation-local prompt state is synchronized and read back at deployment.
    monkeypatch.setenv(
        "CEO_DEVELOPER_PROMPT_TEMPLATE_PATH",
        str(SEED_DEVELOPER_PROMPT_TEMPLATE),
    )


def test_developer_prompt_template_path_can_be_overridden(tmp_path, monkeypatch):
    template_path = tmp_path / "developer.md"
    monkeypatch.setenv("CEO_DEVELOPER_PROMPT_TEMPLATE_PATH", str(template_path))

    assert developer_prompt_template_path() == template_path


def test_prompt_template_paths_default_to_ignored_data_files(monkeypatch):
    monkeypatch.delenv("CEO_DEVELOPER_PROMPT_TEMPLATE_PATH", raising=False)
    monkeypatch.delenv("CEO_USER_PROMPT_TEMPLATE_PATH", raising=False)

    assert developer_prompt_template_path() == repo_root() / "data" / "prompts" / "developer_prompt.md"
    assert user_prompt_template_path() == repo_root() / "data" / "prompts" / "user_prompt.md"


def test_prompt_template_paths_expand_home(monkeypatch):
    monkeypatch.setenv("CEO_DEVELOPER_PROMPT_TEMPLATE_PATH", "~/developer.md")
    monkeypatch.setenv("CEO_USER_PROMPT_TEMPLATE_PATH", "~/user.md")

    assert developer_prompt_template_path() == Path.home() / "developer.md"
    assert user_prompt_template_path() == Path.home() / "user.md"


def test_read_prompt_templates_seed_missing_configured_files(tmp_path, monkeypatch):
    developer_path = tmp_path / "data" / "prompts" / "developer_prompt.md"
    user_path = tmp_path / "data" / "prompts" / "user_prompt.md"
    monkeypatch.setenv("CEO_DEVELOPER_PROMPT_TEMPLATE_PATH", str(developer_path))
    monkeypatch.setenv("CEO_USER_PROMPT_TEMPLATE_PATH", str(user_path))

    developer_template = read_developer_prompt_template()
    user_template = read_user_prompt_template()

    assert developer_path.exists()
    assert user_path.exists()
    assert developer_template.startswith("## Working principles\n")
    assert user_template.strip() == "{{task_context}}"
    assert "CEO Agent Prompt" not in user_template


def test_unmodified_legacy_developer_prompt_is_not_migrated_by_read(tmp_path, monkeypatch):
    developer_path = tmp_path / "data" / "prompts" / "developer_prompt.md"
    developer_path.parent.mkdir(parents=True)
    legacy = "legacy default"
    developer_path.write_text(legacy, encoding="utf-8")
    monkeypatch.setenv("CEO_DEVELOPER_PROMPT_TEMPLATE_PATH", str(developer_path))
    monkeypatch.setattr(
        "app.developer_prompt.LEGACY_UNCUSTOMIZED_DEVELOPER_PROMPT_SHA256S",
        {_sha256_for_test(legacy)},
    )

    assert read_developer_prompt_template() == legacy
    assert developer_path.read_text(encoding="utf-8") == legacy


def test_customized_developer_prompt_is_preserved(tmp_path, monkeypatch):
    developer_path = tmp_path / "data" / "prompts" / "developer_prompt.md"
    developer_path.parent.mkdir(parents=True)
    developer_path.write_text("custom instructions", encoding="utf-8")
    monkeypatch.setenv("CEO_DEVELOPER_PROMPT_TEMPLATE_PATH", str(developer_path))

    assert read_developer_prompt_template() == "custom instructions"


def test_seed_marker_does_not_trigger_a_developer_migration_during_read(tmp_path, monkeypatch):
    developer_path = tmp_path / "developer.md"
    original = "previous default principles\n"
    developer_path.write_text(original, encoding="utf-8")
    marker = tmp_path / ".developer_prompt.seed.sha256"
    original_marker = _sha256_for_test(original) + "\n"
    marker.write_text(original_marker, encoding="ascii")
    monkeypatch.setenv("CEO_DEVELOPER_PROMPT_TEMPLATE_PATH", str(developer_path))

    assert read_developer_prompt_template() == original
    assert developer_path.read_text(encoding="utf-8") == original
    assert marker.read_text(encoding="ascii") == original_marker


def test_default_developer_template_contains_shared_principles_without_role_schema():
    template = read_developer_prompt_template()
    assert template.startswith("## Working principles\n")
    assert "verified facts, inference and missing information" in template
    assert "business and operation Skills" in template
    assert "timezone" in template
    assert "credentials and internal runtime details" in template
    assert "Consumer Agent A" not in template
    assert "Pydantic Wire/Result Contract" not in template
    assert "{{task_context}}" not in template


def _sha256_for_test(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_calendar_rules_path_is_not_an_effective_prompt_variable(monkeypatch):
    monkeypatch.setenv(
        "CEO_PROMPT_VAR_CALENDAR_RULES_PATH",
        "custom/calendar-rules.md",
    )

    assert "calendar_rules_path" not in CONFIGURABLE_PROMPT_VARIABLE_DEFAULTS
    assert "calendar_rules_path" not in prompt_template_variables()
    assert "calendar_rules_path" not in dict(configurable_prompt_variable_pairs())
    assert "CEO_PROMPT_VAR_CALENDAR_RULES_PATH" not in (
        Path(".env.example").read_text(encoding="utf-8")
    )
    with pytest.raises(DeveloperPromptTemplateError, match="unsupported"):
        write_configurable_prompt_variables(
            [("CEO_PROMPT_VAR_CALENDAR_RULES_PATH", "custom/calendar-rules.md")]
        )


def test_developer_prompt_template_renders_vars_files_and_code(tmp_path, monkeypatch):
    del tmp_path
    profile = repo_root() / ".developer_prompt_test_profile.md"
    profile.write_text("- profile line\n", encoding="utf-8")
    script = repo_root() / ".developer_prompt_test_script.py"
    script.write_text(
        "def dynamic_rule():\n"
        "    return 'runtime rule from code'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("USER_ALIAS", "Alex")
    try:
        rendered = render_developer_prompt_template(
            "\n".join(
                [
                    "<vars>",
                    "principal = <code: app.config:user_alias()>",
                    "handoff = <code: app.config:user_alias()>",
                    "</vars>",
                    "",
                    "principal=<var: principal>",
                    f"profile=<file: {profile}>",
                    "code=<code: .developer_prompt_test_script.py:dynamic_rule()>",
                    "handoff=<var: handoff>",
                ]
            )
        )
    finally:
        script.unlink(missing_ok=True)
        profile.unlink(missing_ok=True)

    assert "principal=Alex" in rendered
    assert "- profile line" in rendered
    assert "code=runtime rule from code" in rendered
    assert "handoff=Alex" in rendered


def test_default_developer_prompt_template_is_a_separate_file():
    template = read_developer_prompt_template()

    assert not template.startswith("<vars>")
    assert "principal = 明哥" not in template
    assert "handoff_name = Alex" not in template
    assert "<vars>" not in template
    assert "<code: app.prompt:work_profile_instruction()>" not in template
    assert "work_profile_path" not in template
    assert "Alex 工作人格 Profile:" not in template


def test_personnel_skill_keeps_business_facts_out_of_personnel_sensitivity():
    skill = _personnel_skill_prose()

    assert "A person's name alone does not make a business fact personnel information" in skill
    assert "Ownership, delivery, revenue, customer progress, project risk" in skill


def test_work_profile_path_default_is_not_user_specific(monkeypatch):
    monkeypatch.delenv("CEO_WORK_PROFILE_PATH", raising=False)

    assert work_profile_path() == repo_root() / "data" / "work-profile" / "work_profile.md"


def test_config_paths_expand_home(monkeypatch):
    monkeypatch.setenv("CEO_ENV_FILE", "~/.ceo-agent-test.env")
    monkeypatch.setenv("CEO_WORK_PROFILE_PATH", "~/profile.md")
    monkeypatch.setenv("CEO_PROFILE_EVIDENCE_DIR", "$HOME/profile-evidence")

    assert env_file_path() == Path.home() / ".ceo-agent-test.env"
    assert work_profile_path() == Path.home() / "profile.md"
    assert profile_evidence_dir() == Path.home() / "profile-evidence"


def test_work_profile_instruction_uses_configured_principal_name(
    tmp_path, monkeypatch
):
    profile = tmp_path / "profile.md"
    profile.write_text("# Generic Profile\n\n- Keep replies concise.", encoding="utf-8")
    monkeypatch.setenv("CEO_WORK_PROFILE_PATH", str(profile))
    monkeypatch.setenv("USER_ALIAS", "Alex")

    instruction = work_profile_instruction()

    assert "Alex 工作人格 Profile" in instruction
    assert "the principal 工作人格 Profile" not in instruction
    assert "更接近 Alex 的判断顺序" in instruction
    assert "更接近 the principal 的判断顺序" not in instruction


def test_work_profile_instruction_seeds_missing_configured_profile(
    tmp_path,
    monkeypatch,
):
    profile = tmp_path / "data" / "work-profile" / "work_profile.md"
    monkeypatch.setenv("CEO_WORK_PROFILE_PATH", str(profile))
    monkeypatch.setenv("USER_ALIAS", "Alex")

    instruction = work_profile_instruction()

    assert profile.exists()
    assert "Alex 工作人格 Profile" in instruction
    assert "No distilled work profile has been generated yet." in profile.read_text(
        encoding="utf-8"
    )


def test_write_work_profile_updates_the_next_runtime_instruction(tmp_path, monkeypatch):
    profile = tmp_path / "data" / "work-profile" / "work_profile.md"
    monkeypatch.setenv("CEO_WORK_PROFILE_PATH", str(profile))

    written = write_work_profile("# Work Profile\n\nPrefer concrete evidence.")

    assert written == profile
    assert profile.read_text(encoding="utf-8") == "# Work Profile\n\nPrefer concrete evidence.\n"
    assert "Prefer concrete evidence." in work_profile_instruction()


def test_user_prompt_template_path_can_be_overridden(tmp_path, monkeypatch):
    template_path = tmp_path / "user.md"
    monkeypatch.setenv("CEO_USER_PROMPT_TEMPLATE_PATH", str(template_path))

    assert user_prompt_template_path() == template_path


def test_default_user_prompt_template_is_a_separate_file():
    template = read_user_prompt_template(SEED_USER_PROMPT_TEMPLATE)
    assert template.strip() == "{{task_context}}"
    assert "{{current_message}}" not in template
    assert "CEO Agent Prompt" not in template


def test_custom_user_template_is_read_without_automatic_migration(tmp_path, monkeypatch):
    path = tmp_path / "user.md"
    original = "Custom legacy {{current_message}} template\n"
    path.write_text(original, encoding="utf-8")
    monkeypatch.setenv("CEO_USER_PROMPT_TEMPLATE_PATH", str(path))

    assert read_user_prompt_template() == original
    assert path.read_text(encoding="utf-8") == original


def test_user_template_writer_preserves_complete_task_slot_and_raw_context(tmp_path):
    path = tmp_path / "user.md"
    template = "Inspect the complete task:\n{{task_context}}\nRespond with the required result."
    context = 'Task evidence\nFeedback and prior receipts\nLiteral source {{not_a_template}}'

    assert write_user_prompt_template(template, path) == path
    assert path.read_text(encoding="utf-8") == template
    assert render_consumer_task_template(template, context) == (
        "Inspect the complete task:\n" + context + "\nRespond with the required result."
    )


@pytest.mark.parametrize("template", ["no complete task slot", "{{task_context}} {{task_context}}", "{{task_context}} {{current_message}}"])
def test_invalid_consumer_user_template_does_not_overwrite_saved_content(tmp_path, template):
    path = tmp_path / "user.md"
    original = "Saved task:\n{{task_context}}"
    path.write_text(original, encoding="utf-8")

    with pytest.raises(DeveloperPromptTemplateError, match="exactly one"):
        write_user_prompt_template(template, path)
    assert path.read_text(encoding="utf-8") == original


def test_user_prompt_block_registry_orders_material_references_before_assets():
    expressions = [block.expression for block in USER_PROMPT_BLOCKS]

    assert expressions[
        expressions.index("app.user_prompt_blocks:context_messages_block()") :
        expressions.index("app.user_prompt_blocks:image_download_block()") + 1
    ] == [
        "app.user_prompt_blocks:context_messages_block()",
        "app.user_prompt_blocks:material_references_block()",
        "app.user_prompt_blocks:linked_documents_block()",
        "app.user_prompt_blocks:image_download_block()",
    ]


def test_message_lines_remove_repeated_card_images_and_shorten_links():
    lines = message_lines(
        DingTalkMessage(
            open_conversation_id="cid-1",
            open_message_id="msg-1",
            conversation_title="26年董事会筹备组",
            single_chat=False,
            sender_name="Riley",
            sender_user_id="lily-user-1",
            create_time="2026-05-14 15:04:04",
            content=CARD_CONTENT,
        )
    )
    rendered = "\n".join(lines)

    assert "董事会报告根据昨天的会议进行了修改" in rendered
    assert "Riley sender_user_id=lily-user-1 2026-05-14" in rendered
    assert "26年董事会报告" in rendered
    assert "![image]" not in rendered
    assert "utm_medium" not in rendered
    assert "corpId" not in rendered
    assert (
        "https://alidocs.dingtalk.com/i/nodes/vy20BglGWOKXmP5zs0OGQn6DWA7depqY"
        in rendered
    )


def test_linked_document_lines_preserve_readable_material_without_card_assets():
    document = LinkedDocumentContext(
        url="https://alidocs.dingtalk.com/i/nodes/doc123?utm_source=im",
        title="Decision evidence",
        markdown='<span style="color: red;">Actual conclusion</span>\nVerified source facts.',
    )
    rendered = "\n".join(linked_document_lines(1, document))
    assert "Decision evidence" in rendered
    assert "https://alidocs.dingtalk.com/i/nodes/doc123" in rendered
    assert "Actual conclusion" in rendered
    assert "Verified source facts." in rendered
    assert "utm_source" not in rendered
    assert "<span" not in rendered


def test_material_reference_lines_preserve_source_identity_and_read_command():
    material = MaterialReferenceContext(
        kind="dingtalk_doc", reference="https://alidocs.dingtalk.com/i/nodes/doc123?utm_source=im",
        source_message_id="source-1", source_sender="Origin", source_time="2026-10-06T12:00:00Z",
        read_command="dws doc read --node https://alidocs.dingtalk.com/i/nodes/doc123 --format json",
    )
    rendered = "\n".join(material_reference_lines(1, material))
    assert "dingtalk_doc" in rendered
    assert "source-1" in rendered
    assert "Origin" in rendered
    assert "2026-10-06T12:00:00Z" in rendered
    assert material.read_command in rendered
    assert "utm_source" not in rendered


def test_message_lines_include_existing_reactions():
    lines = message_lines(
        DingTalkMessage(
            open_conversation_id="cid-1",
            open_message_id="msg-1",
            conversation_title="产品群",
            single_chat=False,
            sender_name="Avery",
            create_time="2026-05-15 13:00:00",
            content="@Alex Chen(明哥) 看下",
            raw_payload={
                "emotionReplyList": [
                    {"emoji": "OK", "replyUsers": ["明哥"]},
                    {"emoji": "👍", "replyUsers": ["Avery", "Alex"]},
                ]
            },
        )
    )

    rendered = "\n".join(lines)

    assert "已有 reaction: OK（明哥）；👍（Avery, Alex）" in rendered


def test_sanitize_dingtalk_prompt_text_keeps_malformed_url_text():
    rendered = sanitize_dingtalk_prompt_text(
        "@Alex Chen(明哥) 看下这个链接 https://[not-a-valid-ipv6/link?x=1"
    )

    assert "@Alex Chen(明哥) 看下这个链接" in rendered
    assert "https://[not-a-valid-ipv6/link?x=1" in rendered


def test_sanitize_dingtalk_prompt_text_keeps_url_with_nfkc_unsafe_host_text():
    rendered = sanitize_dingtalk_prompt_text(
        "@Alex Chen(明哥) 看下这个服务 http://stardust-gpu4:8787？"
    )

    assert "@Alex Chen(明哥) 看下这个服务" in rendered
    assert "http://stardust-gpu4:8787？" in rendered


def test_personnel_skill_explains_first_person_single_chat_subject():
    skill = _personnel_skill_prose()

    assert "When the recipient asks about their own personnel information" in skill
    assert "the subject and recipient are the same person" in skill


def test_personnel_skill_delegates_candidate_evidence_to_specialist():
    skill = _personnel_skill_prose()

    assert "Load `stardust-interview` for candidate evaluation" in skill
    assert "follow its evidence, role-fit, and interview workflow" in skill
    assert "Do not reproduce or replace those specialist workflows here" in skill
    assert "resume, role requirements, and interview records" not in skill
    assert "Ask for specifically missing candidate or role material" not in skill
