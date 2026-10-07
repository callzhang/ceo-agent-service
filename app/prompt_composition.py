"""Pure service-input assembly shared by execution and configuration previews."""

from dataclasses import dataclass
from hashlib import sha256


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
                          scheduled_prompt: str = '', continuation: str = '') -> str:
    from app.developer_prompt import render_consumer_task_template

    complete = assemble_consumer_task(task_context=task_context,
                                     scheduled_prompt=scheduled_prompt, continuation=continuation)
    return render_consumer_task_template(configuration.user_template, complete).strip('\n')


def example_consumer_task(configuration: PromptConfiguration | RawPromptConfiguration) -> str:
    """Render the saved User template using explicit synthetic complete facts."""
    from app.agent_context import AgentTaskContext
    from app.developer_prompt import render_consumer_task_template

    context = AgentTaskContext(task_id=0, channel="example", conversation_id="example-conversation",
        conversation_title="Synthetic example", single_chat=True, trigger_message_id="fixture-message",
        trigger_sender="Example sender", trigger_text="Review the supplied document.",
        trigger_create_time="2026-01-01T12:00:00+00:00", messages=(), materials=(), prior_receipts=())
    complete = assemble_consumer_task(task_context=context.render(current_time="2026-01-01T12:00:00+00:00"))
    return render_consumer_task_template(configuration.user_template, complete).strip('\n')
