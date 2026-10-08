"""Pure service-input assembly shared by execution and configuration previews."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Literal


PromptPlacement = Literal["developer", "task"]


@dataclass(frozen=True)
class PromptSection:
    """One named source segment in the service-owned model input."""

    name: str
    source: str
    placement: PromptPlacement
    text: str


@dataclass(frozen=True)
class PromptAssembly:
    """Rendered input and the exact named segments that produced it."""

    text: str
    sections: tuple[PromptSection, ...]


def render_prompt_sections(
    sections: tuple[PromptSection, ...],
    *,
    placement: PromptPlacement,
    omit_empty: bool = True,
) -> str:
    return join_developer_sections(
        *(section.text for section in sections if section.placement == placement),
        omit_empty=omit_empty,
    )


def prompt_section_facts(
    *section_groups: tuple[PromptSection, ...],
) -> list[dict[str, str]]:
    return [
        {
            "name": section.name,
            "source": section.source,
            "placement": section.placement,
            "text": section.text,
        }
        for sections in section_groups
        for section in sections
    ]


def join_developer_sections(*sections: str, omit_empty: bool) -> str:
    return "\n\n".join(section for section in sections if section or not omit_empty)


def append_runtime_context(developer: str, runtime_context: str) -> str:
    return developer + "\n\n" + runtime_context


def assemble_consumer_task(*, task_context: str, scheduled_prompt: str = "", continuation: str = "") -> str:
    return (
        "## Runtime Invariants\nPreserve typed proposal contracts and session boundaries. The proposal must match the supplied JSON Schema exactly.\n\n"
        + ("## Scheduled Consumer Prompt\n" + scheduled_prompt + "\n\n" if scheduled_prompt else "")
        + task_context + continuation
    )


def _task_content_sections(
    *,
    task_context: str,
    scheduled_prompt: str,
    skill_discovery: str,
    skill_source: str,
    continuation: str,
) -> tuple[PromptSection, ...]:
    sections = [
        PromptSection(
            "任务运行约束",
            "服务任务合同",
            "task",
            "## Runtime Invariants\nPreserve typed proposal contracts and session boundaries. "
            "The proposal must match the supplied JSON Schema exactly.",
        )
    ]
    if scheduled_prompt:
        sections.append(
            PromptSection(
                "任务专项指令",
                "已保存任务配置",
                "task",
                "## Scheduled Consumer Prompt\n" + scheduled_prompt,
            )
        )
    sections.append(
        PromptSection(
            "任务 Skill 入口",
            skill_source,
            "task",
            skill_discovery,
        )
    )
    sections.append(
        PromptSection("当前任务事实", "当前任务与候选", "task", task_context)
    )
    if continuation:
        sections.append(
            PromptSection(
                "本轮续接",
                "当前运行修正",
                "task",
                continuation.strip("\n"),
            )
        )
    return tuple(sections)


def compose_consumer_task_assembly(
    configuration: PromptConfiguration,
    *,
    task_context: str,
    scheduled_prompt: str = "",
    continuation: str = "",
    skill_names: tuple[str, ...] = (),
    frozen_skill_names: tuple[str, ...] = (),
    skill_protocol: str = "",
    skill_protocol_is_custom: bool = True,
    skill_catalog=(),
) -> PromptAssembly:
    """Render the Consumer Task with current instructions and Skill discovery."""
    from app.business_skills import render_task_skill_discovery
    from app.developer_prompt import validate_consumer_task_template

    validate_consumer_task_template(configuration.user_template)

    if skill_protocol:
        appended = "\n\n" + skill_protocol
        if scheduled_prompt.endswith(appended):
            scheduled_prompt = scheduled_prompt[: -len(appended)]
    if skill_names:
        skill_discovery = render_task_skill_discovery(
            skill_names,
            catalog=tuple(skill_catalog),
            frozen_names=frozen_skill_names,
        )
        unfrozen = tuple(name for name in skill_names if name not in frozen_skill_names)
        if skill_protocol_is_custom and skill_protocol:
            skill_discovery += "\n\n## Saved Task Skill Instructions\n" + skill_protocol
        if frozen_skill_names and unfrozen:
            skill_source = (
                "任务冻结与当前 Skill 选择 + 已保存任务约定"
                if skill_protocol_is_custom and skill_protocol
                else "任务冻结与当前 Skill 选择"
            )
        elif frozen_skill_names:
            skill_source = (
                "任务冻结 Skill 选择 + 已保存任务约定"
                if skill_protocol_is_custom and skill_protocol
                else "任务冻结 Skill 选择 + 当前 Skill 用途目录"
            )
        else:
            skill_source = (
                "当前选中 Skill 用途目录 + 已保存任务约定"
                if skill_protocol_is_custom and skill_protocol
                else "当前选中 Skill 用途目录"
            )
    elif skill_protocol and skill_protocol_is_custom:
        skill_discovery = skill_protocol
        skill_source = "已保存自定义 Task Skill 约定"
    else:
        skill_discovery = render_task_skill_discovery((), catalog=tuple(skill_catalog))
        skill_source = "当前 Skill 用途目录"
    content_sections = _task_content_sections(
        task_context=task_context,
        scheduled_prompt=scheduled_prompt,
        skill_discovery=skill_discovery,
        skill_source=skill_source,
        continuation=continuation,
    )
    content = render_prompt_sections(content_sections, placement="task")
    prefix, _marker, suffix = configuration.user_template.partition("{{task_context}}")
    sections = (
        PromptSection(
            "用户模板前缀", "已保存 User 模板", "task", prefix.lstrip("\n")
        ),
        *content_sections,
        PromptSection(
            "用户模板后缀", "已保存 User 模板", "task", suffix.rstrip("\n")
        ),
    )
    return PromptAssembly(
        text=(prefix + content + suffix).strip("\n"),
        sections=sections,
    )


def compose_plain_task_assembly(
    *sections: PromptSection,
) -> PromptAssembly:
    task_sections = tuple(section for section in sections if section.placement == "task")
    return PromptAssembly(
        text=render_prompt_sections(task_sections, placement="task"),
        sections=task_sections,
    )


@dataclass(frozen=True)
class PromptConfiguration:
    """The file-backed configuration read once for one role invocation."""

    developer_template: str
    developer_instructions: str
    user_template: str
    work_profile: str
    work_profile_text: str = ""

    def fingerprints(self) -> dict[str, str]:
        return {
            name: sha256(value.encode('utf-8')).hexdigest()
            for name, value in (
                ('developer_template', self.developer_template),
                ('developer_instructions', self.developer_instructions),
                ('user_template', self.user_template),
                ('work_profile_instruction', self.work_profile),
            )
        }


@dataclass(frozen=True)
class RawPromptConfiguration:
    """Saved template text before invocation validation or rendering."""

    developer_template: str
    user_template: str
    work_profile_text: str


def read_prompt_configuration_raw(*, create_missing: bool = True, role: str = "consumer") -> RawPromptConfiguration:
    from app.developer_prompt import (
        read_developer_prompt_template, read_user_prompt_template,
        developer_prompt_template_path, user_prompt_template_path,
        SEED_DEVELOPER_PROMPT_TEMPLATE, SEED_USER_PROMPT_TEMPLATE,
    )
    from app.prompt import read_work_profile

    if create_missing:
        developer = read_developer_prompt_template()
    else:
        path = developer_prompt_template_path()
        developer = (path if path.exists() else SEED_DEVELOPER_PROMPT_TEMPLATE).read_text(encoding="utf-8")
    user = ""
    if role == "consumer":
        if create_missing:
            user = read_user_prompt_template()
        else:
            path = user_prompt_template_path()
            user = (path if path.exists() else SEED_USER_PROMPT_TEMPLATE).read_text(encoding="utf-8")
    profile_text = read_work_profile(create_missing=create_missing)
    return RawPromptConfiguration(developer, user, profile_text)


def load_prompt_configuration(*, create_missing: bool = True, role: str = "consumer") -> PromptConfiguration:
    from app.developer_prompt import render_developer_prompt_template, validate_consumer_task_template
    from app.prompt import work_profile_instruction

    raw = read_prompt_configuration_raw(create_missing=create_missing, role=role)
    if role == "consumer":
        validate_consumer_task_template(raw.user_template)
    return PromptConfiguration(
        developer_template=raw.developer_template,
        developer_instructions=render_developer_prompt_template(raw.developer_template),
        user_template=raw.user_template,
        work_profile=work_profile_instruction(profile_text=raw.work_profile_text),
        work_profile_text=raw.work_profile_text,
    )


def compose_consumer_task(configuration: PromptConfiguration, *, task_context: str,
                          scheduled_prompt: str = '', continuation: str = '',
                          skill_names: tuple[str, ...] = (),
                          frozen_skill_names: tuple[str, ...] = (), skill_protocol: str = '',
                          skill_protocol_is_custom: bool = True,
                          skill_catalog=()) -> str:
    return compose_consumer_task_assembly(
        configuration,
        task_context=task_context,
        scheduled_prompt=scheduled_prompt,
        continuation=continuation,
        skill_names=skill_names,
        frozen_skill_names=frozen_skill_names,
        skill_protocol=skill_protocol,
        skill_protocol_is_custom=skill_protocol_is_custom,
        skill_catalog=skill_catalog,
    ).text


def example_consumer_task(configuration: PromptConfiguration | RawPromptConfiguration) -> str:
    """Render the saved User template using explicit synthetic complete facts."""
    from app.agent_context import AgentTaskContext
    from app.consumer_agent import default_task_skill_catalog

    context = AgentTaskContext(task_id=0, channel="example", conversation_id="example-conversation",
        conversation_title="Synthetic example", single_chat=True, trigger_message_id="fixture-message",
        trigger_sender="Example sender", trigger_text="Review the supplied document.",
        trigger_create_time="2026-01-01T12:00:00+00:00", messages=(), materials=(), prior_receipts=())
    selected = ("ceo-document-review",)
    return compose_consumer_task_assembly(
        configuration,
        task_context=context.render(current_time="2026-01-01T12:00:00+00:00"),
        skill_names=selected,
        skill_catalog=default_task_skill_catalog(selected),
    ).text
