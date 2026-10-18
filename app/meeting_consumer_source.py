"""Replace completed meeting consumer originals with exact source references."""

import hashlib
import json

from app.agent_cron.commands import ServiceCommandConsumerContext
from app.native_trajectory import (
    NativeSourceMissing,
    NativeSourceVersionChanged,
    locate_native_source_body,
    read_native_source_body,
)


def compact_meeting_consumer(source_json, attempts, *, source_reader=None):
    payload = json.loads(source_json)
    context = ServiceCommandConsumerContext.from_payload(payload.get("scheduled_consumer"))
    if context is None:
        return None
    original = dict(payload["scheduled_consumer"])
    encoded = json.dumps(original, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")

    def finish(fields):
        reference = {key: value for key, value in original.items()
                     if key not in {"prompt", "skill_protocol", "skill_materials"}}
        reference.update(fields)
        reference["input_sha256"] = hashlib.sha256(encoded).hexdigest()
        reference["input_bytes"] = len(encoded)
        compact = dict(payload)
        del compact["scheduled_consumer"]
        compact["scheduled_consumer_reference"] = reference
        return json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    for attempt in attempts:
        if attempt["runtime_kind"] not in ("codex_cli", "claude_cli"):
            continue
        if not attempt["session_id"] or attempt["transcript_end"] <= attempt["transcript_start"]:
            continue

        def locate(body):
            ref = locate_native_source_body(
                body, runtime_kind=attempt["runtime_kind"],
                session_uuid=attempt["session_id"], start_line=attempt["transcript_start"],
                end_line=attempt["transcript_end"], literal_encoding_mode="plain_utf8",
            )
            if read_native_source_body(ref, literal_encoding_mode="plain_utf8") != body:
                raise NativeSourceVersionChanged("meeting consumer source differs")
            return ref

        reference = {}
        try:
            reference["prompt"] = locate(context.prompt)
            if context.skill_materials:
                # This small discovery configuration is not the materialized
                # Skill text injected into the analysis prompt.
                reference["skill_protocol"] = context.skill_protocol
                reference["skill_materials"] = [
                    {"name": item.name, "native_provenance": locate(item.content)}
                    for item in context.skill_materials
                ]
            else:
                reference["skill_protocol"] = locate(context.skill_protocol)
        except (NativeSourceMissing, NativeSourceVersionChanged, OSError, UnicodeError):
            # A terminal status alone does not establish an original's source.
            # Retain the input unless one actual attempt proves every field.
            continue
        return finish(reference)
    if source_reader is not None:
        reference = source_reader(context)
        if reference is not None:
            return finish(reference)
    return None


def scheduled_consumer_source_fields(store, context, *, skill_files=None):
    """Prove exact existing configuration originals after native lookup failed."""
    from pathlib import Path
    from types import SimpleNamespace
    from app.agent_cron.context import ScheduledAgentContextBuilder
    from app.skill_files import SkillFileError, SkillFileService

    run = store.get_scheduled_task_run(context.scheduled_task_run_id)
    if run is None:
        return None
    snapshot = run.snapshot
    if (snapshot.task_id != context.scheduled_task_id or snapshot.prompt != context.prompt
            or tuple(ref.skill_name for ref in snapshot.skill_refs) != context.skill_names):
        return None
    files = skill_files or SkillFileService(Path.home() / ".agents" / "skills")

    def managed(*, skill_id, revision_id, skill_name):
        skill = store.get_managed_skill(skill_id)
        revision = store.get_managed_skill_revision(revision_id)
        if (skill is None or revision is None or revision.skill_id != skill_id
                or skill.name != skill_name
                or hashlib.sha256(revision.content.encode()).hexdigest() != revision.sha256):
            raise NativeSourceMissing("exact managed Skill source unavailable")
        return revision

    def operation(name):
        matches = [entry for entry in files.list_skills() if entry.name == name]
        if len(matches) != 1:
            raise NativeSourceMissing("exact operation Skill source unavailable")
        document = files.read_operation_skill(matches[0])
        if document.name != name:
            raise NativeSourceMissing("operation Skill source identity differs")
        return document

    options = SimpleNamespace(resolve_managed_skill_revision=managed,
                              resolve_operation_skill=operation)
    try:
        protocols, facts = ScheduledAgentContextBuilder(options)._materialize_skills(snapshot.skill_refs)
    except (NativeSourceMissing, SkillFileError, OSError, UnicodeError):
        return None

    def reference(body, **locator):
        return {**locator, "sha256": hashlib.sha256(body.encode()).hexdigest(),
                "bytes": len(body.encode())}

    fields = {"prompt": reference(context.prompt,
        scheduled_task_run_id=context.scheduled_task_run_id, field="prompt")}
    if context.skill_materials:
        if tuple(item.content for item in context.skill_materials) != tuple(protocols):
            return None
        fields["skill_protocol"] = context.skill_protocol
        fields["skill_materials"] = [
            {"name": item.name, "source": fact,
             "content_sha256": hashlib.sha256(item.content.encode()).hexdigest(),
             "content_bytes": len(item.content.encode())}
            for item, fact in zip(context.skill_materials, facts, strict=True)
        ]
    else:
        protocol = "\n\n".join(protocols)
        if protocol != context.skill_protocol:
            return None
        fields["skill_protocol"] = reference(protocol,
            scheduled_task_run_id=context.scheduled_task_run_id, skills=facts)
    return fields
