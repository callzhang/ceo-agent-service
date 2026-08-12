from __future__ import annotations

import hashlib
import errno
import json
import os
import shutil
import stat
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from app.agent_result import EffectKind
from app.bounded_process import (
    MAX_PROCESS_OUTPUT_BYTES,
    ProcessOutputLimitError,
    run_bounded_process,
)
from app.channel_gate import classify_cli_read_failure, classify_cli_write_failure
from app.leak_check import contains_credential, is_sensitive_field_name
from app.native_cli_metadata import (
    AgentReadOnlyViolationError,
    NativeCliMetadataClassifier,
    NativeCliMetadataUnavailableError,
    describe_native_command,
    has_noninteractive_confirmation,
)

MAX_CLI_OUTPUT_BYTES = MAX_PROCESS_OUTPUT_BYTES
MAX_SKILL_BYTES = 256 * 1024
CLI_TIMEOUT_SECONDS = 15 * 60
RECOVERY_WRITE_ALLOWLIST_ENV = "CEO_AGENT_RECOVERY_WRITE_ALLOWLIST"
CliOutputLimitError = ProcessOutputLimitError
AGENT_SKILL_ROOTS = (
    Path.home() / ".agents" / "skills",
    Path.home() / ".codex" / "skills",
    Path.home() / ".codex" / "plugins",
)


@dataclass(frozen=True, slots=True)
class ReviewedWriteAuthorization:
    authorization_id: str
    action_index: int
    capability: str
    operation: str
    operation_digest: str
    target_identifiers: tuple[str, ...]
    arguments_digest: str


def _process_failure_receipt(
    command, *, code: str, retryable: bool
) -> dict[str, object]:
    return {
        "cli": command.cli,
        "operation": command.command_path,
        "operation_digest": command.command_digest,
        "target_identifiers": command.target_identifiers,
        "result_digest": hashlib.sha256(b"").hexdigest(),
        "stdout": "",
        "error": {
            "channel": command.cli,
            "code": code,
            "retryable": retryable,
            "gate_state": "unavailable",
        },
    }


def execute_reviewed_read(
    argv: Sequence[str],
    *,
    classifier: NativeCliMetadataClassifier | None = None,
    process_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, object]:
    return _execute_reviewed(
        argv,
        expected_effect=EffectKind.READ_ONLY,
        classifier=classifier,
        process_runner=process_runner,
    )


def execute_reviewed_write(
    argv: Sequence[str],
    *,
    authorization_id: str | None = None,
    action_index: int | None = None,
    authorization: ReviewedWriteAuthorization | None = None,
    authorization_consumer: Callable[[ReviewedWriteAuthorization], object] | None = None,
    classifier: NativeCliMetadataClassifier | None = None,
    process_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, object]:
    return _execute_reviewed(
        argv,
        expected_effect=EffectKind.EFFECTFUL,
        authorization_id=authorization_id,
        action_index=action_index,
        reviewed_authorization=authorization,
        authorization_consumer=authorization_consumer,
        classifier=classifier,
        process_runner=process_runner,
    )


def _json_digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _domain_json_digest(value: object, *, domain: str) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(domain.encode("ascii") + b"\0" + encoded).hexdigest()


def _validate_reviewed_argv(argv: Sequence[str]) -> tuple[str, ...]:
    if (
        isinstance(argv, (str, bytes))
        or not argv
        or any(
            not isinstance(argument, str) or not argument or "\0" in argument
            for argument in argv
        )
    ):
        raise AgentReadOnlyViolationError("agent_cli_command_invalid")
    if any(
        argument.startswith("--")
        and is_sensitive_field_name(argument[2:].partition("=")[0])
        for argument in argv
    ) or any(contains_credential(argument) for argument in argv):
        raise AgentReadOnlyViolationError("agent_cli_sensitive_argument")
    return tuple(argv)


def _classify_reviewed_write(
    argv: Sequence[str],
    *,
    classifier: NativeCliMetadataClassifier | None,
):
    canonical_argv = _validate_reviewed_argv(argv)
    reviewed = classifier or NativeCliMetadataClassifier()
    item = {"type": "command_execution", "argv": list(canonical_argv)}
    descriptor = describe_native_command(item)
    if descriptor is None or descriptor.cli == "local-shell":
        command = reviewed.classify(item)
    else:
        reviewed.prewarm()
        command = reviewed.classify(item)
    if command is None:
        raise AgentReadOnlyViolationError("agent_cli_command_unreviewed")
    if command.effect is not EffectKind.EFFECTFUL:
        raise AgentReadOnlyViolationError("reviewed_cli_effect_mismatch")
    if command.cli == "dws" and not has_noninteractive_confirmation(canonical_argv):
        raise AgentReadOnlyViolationError("agent_cli_confirmation_required")
    return canonical_argv, command


def review_write_authorization(
    argv: Sequence[str],
    authorization_id: str,
    action_index: int,
    classifier: NativeCliMetadataClassifier | None = None,
) -> ReviewedWriteAuthorization:
    if (
        not isinstance(authorization_id, str)
        or not authorization_id
        or authorization_id != authorization_id.strip()
    ):
        raise AgentReadOnlyViolationError("reviewed_write_authorization_invalid")
    if (
        isinstance(action_index, bool)
        or not isinstance(action_index, int)
        or action_index < 0
    ):
        raise AgentReadOnlyViolationError("reviewed_write_authorization_invalid")
    canonical_argv, command = _classify_reviewed_write(argv, classifier=classifier)
    capability = f"agent_cli.{command.cli}"
    targets = tuple(
        f"{key}={value}" for key, value in sorted(command.target_identifiers.items())
    )
    return ReviewedWriteAuthorization(
        authorization_id=authorization_id,
        action_index=action_index,
        capability=capability,
        operation=command.command_path,
        operation_digest=_domain_json_digest(
            {"capability": capability, "operation": command.command_path},
            domain="agent-cli-operation-v1",
        ),
        target_identifiers=targets,
        arguments_digest=_domain_json_digest(
            {"argv": list(canonical_argv)}, domain="agent-cli-arguments-v1"
        ),
    )


def _recovery_write_authorization(
    command,
    argv: Sequence[str],
    *,
    authorization_id: str | None,
) -> dict[str, object] | None:
    raw_allowlist = os.environ.get(RECOVERY_WRITE_ALLOWLIST_ENV, "")
    if not raw_allowlist:
        return None
    try:
        allowlist = json.loads(raw_allowlist)
    except json.JSONDecodeError as exc:
        raise AgentReadOnlyViolationError("recovery_write_allowlist_invalid") from exc
    if not isinstance(allowlist, list) or not isinstance(authorization_id, str):
        raise AgentReadOnlyViolationError("recovery_write_not_authorized")
    actual = {
        "authorization_id": authorization_id,
        "capability": f"agent_cli.{command.cli}",
        "operation": command.command_path,
        "operation_digest": command.command_digest,
        "target_identifiers": command.target_identifiers,
        "arguments_digest": _json_digest({"argv": list(argv)}),
    }
    match = next(
        (
            entry
            for entry in allowlist
            if isinstance(entry, dict)
            and all(entry.get(key) == value for key, value in actual.items())
            and isinstance(entry.get("action_index"), int)
        ),
        None,
    )
    if match is None:
        raise AgentReadOnlyViolationError("recovery_write_not_authorized")
    return match


def read_skill(path: str) -> dict[str, str]:
    skill_path = Path(path).expanduser().resolve(strict=True)
    roots = tuple(root.expanduser().resolve() for root in AGENT_SKILL_ROOTS)
    if not _is_authorized_skill_markdown(skill_path, roots):
        raise AgentReadOnlyViolationError("skill_path_forbidden")
    flags = os.O_RDONLY | os.O_NONBLOCK
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(skill_path, flags), "rb") as skill_file:
        file_stat = os.fstat(skill_file.fileno())
        if not stat.S_ISREG(file_stat.st_mode):
            raise AgentReadOnlyViolationError("skill_file_not_regular")
        if file_stat.st_size > MAX_SKILL_BYTES:
            raise AgentReadOnlyViolationError("skill_content_too_large")
        content_bytes = skill_file.read(MAX_SKILL_BYTES + 1)
        if len(content_bytes) > MAX_SKILL_BYTES:
            raise AgentReadOnlyViolationError("skill_content_too_large")
    content = content_bytes.decode("utf-8")
    return {
        "content": content,
        "sha256": hashlib.sha256(content_bytes).hexdigest(),
    }


def _is_authorized_skill_markdown(skill_path: Path, roots: Sequence[Path]) -> bool:
    if skill_path.suffix.casefold() != ".md":
        return False
    for root in roots:
        if not skill_path.is_relative_to(root):
            continue
        parent = skill_path.parent
        while parent != root:
            if (parent / "SKILL.md").is_file():
                return True
            parent = parent.parent
    return False


def _execute_reviewed(
    argv: Sequence[str],
    *,
    expected_effect: EffectKind,
    classifier: NativeCliMetadataClassifier | None,
    process_runner: Callable[..., subprocess.CompletedProcess[str]] | None,
    authorization_id: str | None = None,
    action_index: int | None = None,
    reviewed_authorization: ReviewedWriteAuthorization | None = None,
    authorization_consumer: Callable[[ReviewedWriteAuthorization], object] | None = None,
) -> dict[str, object]:
    argv = _validate_reviewed_argv(argv)
    reviewed = classifier or NativeCliMetadataClassifier()
    item = {"type": "command_execution", "argv": list(argv)}
    descriptor = describe_native_command(item)
    if descriptor is None or descriptor.cli == "local-shell":
        command = reviewed.classify(item)
    else:
        try:
            reviewed.prewarm()
            command = reviewed.classify(item)
        except NativeCliMetadataUnavailableError as exc:
            return _process_failure_receipt(
                descriptor,
                code=exc.code,
                retryable=exc.retryable,
            )
    if command is None:
        raise AgentReadOnlyViolationError("agent_cli_command_unreviewed")
    if command.effect is not expected_effect:
        raise AgentReadOnlyViolationError("reviewed_cli_effect_mismatch")
    if (
        expected_effect is EffectKind.EFFECTFUL
        and command.cli == "dws"
        and not has_noninteractive_confirmation(tuple(argv))
    ):
        raise AgentReadOnlyViolationError("agent_cli_confirmation_required")
    authorization: dict[str, object] | ReviewedWriteAuthorization | None = None
    if expected_effect is EffectKind.EFFECTFUL:
        if reviewed_authorization is not None:
            if authorization_id is None or action_index is None:
                raise AgentReadOnlyViolationError(
                    "reviewed_write_authorization_mismatch"
                )
            actual = review_write_authorization(
                argv,
                authorization_id=authorization_id,
                action_index=action_index,
                classifier=reviewed,
            )
            if actual != reviewed_authorization:
                raise AgentReadOnlyViolationError(
                    "reviewed_write_authorization_mismatch"
                )
            if authorization_consumer is None:
                raise AgentReadOnlyViolationError(
                    "reviewed_write_authorization_consumer_required"
                )
            authorization = actual
        else:
            authorization = _recovery_write_authorization(
                command,
                argv,
                authorization_id=authorization_id,
            )
    executable_name = argv[0] if command.cli == "local-shell" else command.cli
    executable = shutil.which(executable_name)
    if executable is None:
        return _process_failure_receipt(
            command,
            code="agent_cli_start_unavailable",
            retryable=True,
        )
    if isinstance(authorization, ReviewedWriteAuthorization):
        authorization_consumer(authorization)
    reviewed_argv = [executable, *argv[1:]]
    try:
        process = (
            run_bounded_process(reviewed_argv, timeout=CLI_TIMEOUT_SECONDS)
            if process_runner is None
            else process_runner(
                reviewed_argv,
                capture_output=True,
                text=True,
                timeout=CLI_TIMEOUT_SECONDS,
                check=False,
            )
        )
        if (
            len(process.stdout.encode("utf-8")) > MAX_CLI_OUTPUT_BYTES
            or len(process.stderr.encode("utf-8")) > MAX_CLI_OUTPUT_BYTES
        ):
            raise CliOutputLimitError(stdout_bytes=0, stderr_bytes=0)
    except CliOutputLimitError:
        return {
            "cli": command.cli,
            "operation": command.command_path,
            "operation_digest": command.command_digest,
            "target_identifiers": command.target_identifiers,
            "result_digest": hashlib.sha256(b"").hexdigest(),
            "stdout": "",
            "error": {
                "channel": command.cli,
                "code": "agent_cli_output_limit_exceeded",
                "retryable": False,
                "gate_state": "blocked",
            },
        }
    except subprocess.TimeoutExpired:
        return _process_failure_receipt(
            command,
            code="agent_cli_timeout",
            retryable=True,
        )
    except OSError as exc:
        retryable = exc.errno not in {errno.EINVAL, errno.EACCES, errno.ENOEXEC}
        return _process_failure_receipt(
            command,
            code=(
                "agent_cli_start_unavailable"
                if retryable
                else "agent_cli_start_invalid"
            ),
            retryable=retryable,
        )
    receipt: dict[str, object] = {
        "cli": command.cli,
        "operation": command.command_path,
        "operation_digest": command.command_digest,
        "target_identifiers": command.target_identifiers,
        "result_digest": hashlib.sha256(process.stdout.encode("utf-8")).hexdigest(),
        "stdout": process.stdout,
    }
    if authorization is not None:
        if isinstance(authorization, ReviewedWriteAuthorization):
            receipt["authorization_id"] = authorization.authorization_id
            receipt["action_index"] = authorization.action_index
        else:
            receipt["authorization_id"] = authorization["authorization_id"]
            receipt["action_index"] = authorization["action_index"]
    if process.returncode != 0:
        failure = (
            classify_cli_read_failure(command.cli, process)
            if expected_effect is EffectKind.READ_ONLY
            else classify_cli_write_failure(command.cli, process)
        )
        receipt["error"] = {
            "channel": failure.channel,
            "code": failure.code,
            "retryable": failure.retryable,
            "gate_state": failure.gate_state.value,
            "detail": failure.detail,
        }
    return receipt


server = FastMCP(
    "agent_cli",
    instructions=(
        "Read installed Agent skills and run DWS, Lark, or local read-only "
        "commands only after reviewing installed effect metadata."
    ),
)


@server.tool(
    name="read_skill",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
def read_skill_tool(path: str) -> dict[str, str]:
    """Read an installed Agent skill or its referenced Markdown safely.

    Use this before following product-specific DWS procedures. The path must
    identify a Markdown file inside an installed skill directory.
    """
    return read_skill(path)


@server.tool(
    name="execute_reviewed_read",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
def execute_reviewed_read_tool(argv: list[str]) -> dict[str, object]:
    """Run one reviewed DWS, Lark, or local read command and return a receipt.

    Use the provided argv for live enterprise evidence such as a message,
    calendar event, document, file, approval, person, mail, or meeting. DWS
    and Lark use published effect metadata. Local commands use the principal's
    `ceo_agent.local_read_policy` blacklist, so Python is available unless the
    principal has explicitly blocked it.
    """
    return execute_reviewed_read(argv)


@server.tool(
    name="execute_reviewed_write",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
def execute_reviewed_write_tool(
    argv: list[str], authorization_id: str | None = None
) -> dict[str, object]:
    """Execute an Audit-approved external write and return its receipt.

    This tool accepts only reviewed commands with a matching authorization.
    Consumer Agents must describe writes as proposal data and never call it.
    """
    return execute_reviewed_write(argv, authorization_id=authorization_id)


if __name__ == "__main__":
    server.run(transport="stdio")
