"""Pure service-input assembly shared by execution and configuration previews."""


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
