from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.agent_result import EffectKind
from app.bounded_process import ProcessOutputLimitError, run_bounded_process
from app.history import safe_observability_error
from app.leak_check import contains_credential
from app.runtime_environment import central_python


_SHELL_CONNECTORS = frozenset({"&&", "||", "|", ";"})
_SERVICE_READ_ONLY_PYTHON_COMMANDS = frozenset({"read-oa-approval-detail"})
MATERIAL_OUTPUT_ROOT = Path("/tmp").resolve() / "ceo-agent-service-materials"
_LOCAL_OUTPUT_FLAGS = frozenset(
    {
        "-o",
        "--destination",
        "--download-to",
        "--output",
        "--output-file",
        "--output-path",
        "--save-to",
        "--target-file",
    }
)
_LOCAL_OUTPUT_VERBS = frozenset(
    {"copy", "download", "download-media", "export", "fetch", "install", "save"}
)
_REJECTED_LOCAL_OUTPUT_COMMANDS = frozenset(
    {("dws", "skill get"), ("dws", "skill install")}
)
_ALLOWED_MATERIAL_DOWNLOAD_COMMANDS = frozenset(
    {
        ("dws", "chat message download-media"),
        ("dws", "doc download"),
        ("dws", "drive download"),
        ("dws", "oa attachment download"),
    }
)
DINGTALK_OUTGOING_MESSAGE_COMMAND_PATHS = frozenset(
    {
        "chat +dm",
        "chat +messages-reply",
        "chat +messages-send",
        "chat +send-to-group",
        "chat message reply",
        "chat message send",
    }
)
_DWS_BOOLEAN_FLAGS = frozenset(
    {
        "--ai-tag",
        "--at-all",
        "--debug",
        "--mock",
        "--verbose",
        "--yes",
        "-y",
    }
)


def service_read_command_contract() -> tuple[str, ...]:
    """Return the registered service-owned read commands for session versioning."""
    return tuple(sorted(_SERVICE_READ_ONLY_PYTHON_COMMANDS))


def prepare_material_output_root() -> Path:
    """Create the only directory where reviewed reads may download a file."""
    root = MATERIAL_OUTPUT_ROOT
    if root.is_symlink():
        raise AgentReadOnlyViolationError("material_output_root_unsafe")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not root.is_dir() or root.is_symlink():
        raise AgentReadOnlyViolationError("material_output_root_unsafe")
    root.chmod(0o700)
    return root.resolve()


class AgentReadOnlyViolationError(RuntimeError):
    pass


class NativeCliMetadataUnavailableError(RuntimeError):
    def __init__(self, *, cli: str, code: str, retryable: bool = True) -> None:
        super().__init__(code)
        self.cli = cli
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class NativeCliCommand:
    cli: str
    command_path: str
    effect: EffectKind | None
    command_digest: str
    target_identifiers: dict[str, str]


@lru_cache(maxsize=1)
def _load_reviewed_lark_effects() -> dict[tuple[str, str], EffectKind]:
    effects: dict[tuple[str, str], EffectKind] = {}
    try:
        process = run_bounded_process(
            ["lark-cli", "schema"],
            timeout=30,
        )
    except ProcessOutputLimitError as exc:
        raise NativeCliMetadataUnavailableError(
            cli="lark-cli", code="native_cli_metadata_output_limit"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise NativeCliMetadataUnavailableError(
            cli="lark-cli", code="native_cli_metadata_timeout"
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise NativeCliMetadataUnavailableError(
            cli="lark-cli", code="native_cli_metadata_start"
        ) from exc
    if process.returncode != 0:
        raise NativeCliMetadataUnavailableError(
            cli="lark-cli", code="native_cli_metadata_nonzero"
        )
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise NativeCliMetadataUnavailableError(
            cli="lark-cli", code="native_cli_metadata_invalid_json"
        ) from exc
    if not isinstance(payload, list):
        raise NativeCliMetadataUnavailableError(
            cli="lark-cli", code="native_cli_metadata_invalid_json"
        )
    for tool in payload:
        if not isinstance(tool, dict):
            continue
        command_path = tool.get("name")
        metadata = tool.get("_meta")
        risk = metadata.get("risk") if isinstance(metadata, dict) else None
        if not isinstance(command_path, str) or not command_path.strip():
            continue
        if risk == "read":
            effect = EffectKind.READ_ONLY
        elif risk in {"write", "high-risk-write"}:
            effect = EffectKind.EFFECTFUL
        else:
            continue
        effects[("lark-cli", command_path.strip())] = effect
    return effects


class NativeCliMetadataClassifier:
    """Classify native CLI commands from their installed reviewed metadata."""

    def __init__(
        self,
        *,
        reviewed_effects: dict[tuple[str, str], EffectKind] | None = None,
    ) -> None:
        self._cache: dict[tuple[str, str], EffectKind | None] = dict(
            reviewed_effects or {}
        )
        self._prewarmed = reviewed_effects is not None
        self._discovery_errors: dict[str, NativeCliMetadataUnavailableError] = {}

    @property
    def cache_keys(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._cache))

    def prewarm(self) -> None:
        if self._prewarmed and not self._discovery_errors:
            return
        retrying = self._prewarmed
        self._prewarmed = True
        # DWS is always classified from the exact `schema --cli-path` command.
        # Its complete schema is both unnecessary for an individual command
        # decision and too large to use as a health or authorization signal.
        for cli, loader in (("lark-cli", _load_reviewed_lark_effects),):
            if retrying and cli not in self._discovery_errors:
                continue
            try:
                self._cache.update(loader())
            except NativeCliMetadataUnavailableError as exc:
                self._discovery_errors[cli] = exc
            else:
                self._discovery_errors.pop(cli, None)

    def classify(self, item: dict[str, object]) -> NativeCliCommand | None:
        local_read = _classify_local_read_only_command(item)
        if local_read is not None:
            return local_read
        argv = native_command_argv(item)
        if argv is None or "--dry-run" in argv:
            return None
        help_command = _classify_cli_help(argv)
        if help_command is not None:
            return help_command
        cli = Path(argv[0]).name
        for command_path in _command_path_candidates(argv[1:]):
            cache_key = (cli, command_path)
            if cache_key in self._cache:
                effect = self._cache[cache_key]
                return (
                    _classified_native_command(cli, command_path, argv, effect)
                    if effect is not None
                    else None
                )
        if cli == "dws":
            return self._classify_dws(argv)
        if cli == "lark-cli":
            return self._classify_lark(argv)
        return None

    def classify_cached(self, item: dict[str, object]) -> NativeCliCommand | None:
        local_read = _classify_local_read_only_command(item)
        if local_read is not None:
            return local_read
        argv = native_command_argv(item)
        if argv is None or "--dry-run" in argv:
            return None
        help_command = _classify_cli_help(argv)
        if help_command is not None:
            return help_command
        cli = Path(argv[0]).name
        for command_path in _command_path_candidates(argv[1:]):
            effect = self._cache.get((cli, command_path))
            if effect is not None:
                return _classified_native_command(cli, command_path, argv, effect)
        if cli in self._discovery_errors:
            raise self._discovery_errors[cli]
        return None

    def _classify_dws(self, argv: tuple[str, ...]) -> NativeCliCommand | None:
        # Runtime Schema is DWS's own local command-contract reader. Skills use
        # it to choose and validate later commands, so it is read-only even
        # though it is not listed as one business command in the schema output.
        if len(argv) > 1 and argv[1] == "schema":
            return _classified_native_command(
                "dws", "schema", argv, EffectKind.READ_ONLY
            )
        for command_path in _command_path_candidates(argv[1:]):
            try:
                process = run_bounded_process(
                    [
                        argv[0],
                        "schema",
                        "--cli-path",
                        command_path,
                        "--compact",
                        "--format",
                        "json",
                    ],
                    timeout=10,
                )
            except ProcessOutputLimitError as exc:
                raise NativeCliMetadataUnavailableError(
                    cli="dws", code="native_cli_metadata_output_limit"
                ) from exc
            except (OSError, subprocess.SubprocessError):
                return None
            if process.returncode != 0:
                continue
            try:
                metadata = json.loads(process.stdout)
            except json.JSONDecodeError:
                continue
            effect = metadata.get("effect") if isinstance(metadata, dict) else None
            if effect not in {"read", "write"}:
                continue
            parsed = EffectKind.READ_ONLY if effect == "read" else EffectKind.EFFECTFUL
            self._cache[("dws", command_path)] = parsed
            return _classified_native_command(
                "dws", command_path, argv, parsed
            )
        return None

    def _classify_lark(self, argv: tuple[str, ...]) -> NativeCliCommand | None:
        for command_path in _command_path_candidates(argv[1:]):
            try:
                process = run_bounded_process(
                    [argv[0], *command_path.split(), "--help"],
                    timeout=10,
                )
            except ProcessOutputLimitError as exc:
                raise NativeCliMetadataUnavailableError(
                    cli="lark-cli", code="native_cli_metadata_output_limit"
                ) from exc
            except (OSError, subprocess.SubprocessError):
                return None
            if process.returncode != 0:
                continue
            risk = ""
            for line in (process.stdout + "\n" + process.stderr).splitlines():
                if line.strip().casefold().startswith("risk:"):
                    risk = line.split(":", 1)[1].strip().casefold()
                    break
            if risk not in {"read", "write", "high-risk-write"}:
                continue
            parsed = EffectKind.READ_ONLY if risk == "read" else EffectKind.EFFECTFUL
            self._cache[("lark-cli", command_path)] = parsed
            return _classified_native_command(
                "lark-cli", command_path, argv, parsed
            )
        return None


def native_command_argv(item: dict[str, object]) -> tuple[str, ...] | None:
    raw_command = item.get("argv") or item.get("command")
    if isinstance(raw_command, list) and all(
        isinstance(part, str) for part in raw_command
    ):
        argv = tuple(raw_command)
    elif isinstance(raw_command, str):
        if _contains_shell_command_substitution(raw_command):
            return None
        try:
            lexer = shlex.shlex(
                raw_command, posix=True, punctuation_chars="|&;<>\n"
            )
            lexer.whitespace = " \t\r"
            lexer.whitespace_split = True
            lexer.commenters = ""
            argv = tuple(lexer)
        except ValueError:
            return None
        shell_punctuation = frozenset("|&;<>\n")
        if any(
            token and all(character in shell_punctuation for character in token)
            for token in argv
        ):
            return None
    else:
        return None
    if not argv:
        return None
    executable = Path(argv[0]).name
    if executable in {"bash", "sh", "zsh"}:
        for flag in ("-lc", "-c"):
            if flag in argv:
                index = argv.index(flag)
                if index + 1 < len(argv):
                    return native_command_argv({"command": argv[index + 1]})
        return None
    return argv if executable in {"dws", "lark-cli"} else None


def has_noninteractive_confirmation(argv: tuple[str, ...]) -> bool:
    return any(
        argument == "-y"
        or argument == "--yes"
        or argument.startswith("--yes=")
        for argument in argv
    )


def _classify_local_read_only_command(
    item: dict[str, object],
) -> NativeCliCommand | None:
    segments = _local_read_only_segments(item)
    if segments is None:
        return None
    service_read = _service_read_only_descriptor(segments)
    if service_read is not None:
        return service_read
    command_path = " ; ".join(Path(segment[0]).name for segment in segments)
    normalized = "\x1e".join(shlex.join(segment) for segment in segments)
    return NativeCliCommand(
        cli="local-shell",
        command_path=command_path,
        effect=EffectKind.READ_ONLY,
        command_digest=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        target_identifiers=_local_read_target_identifiers(segments),
    )


def _service_read_only_descriptor(
    segments: tuple[tuple[str, ...], ...],
) -> NativeCliCommand | None:
    """Preserve the stable target of a registered service-owned read command."""
    if len(segments) != 1:
        return None
    argv = segments[0]
    if not _is_service_read_only_python_command(argv):
        return None
    return NativeCliCommand(
        cli="local-shell",
        command_path=f"app.cli {argv[3]}",
        effect=EffectKind.READ_ONLY,
        command_digest=hashlib.sha256(shlex.join(argv).encode("utf-8")).hexdigest(),
        target_identifiers={"instance-id": argv[5]},
    )


def _local_read_target_identifiers(
    segments: tuple[tuple[str, ...], ...],
) -> dict[str, str]:
    """Keep stable identifiers from a reviewed local read for readback matching."""
    identifiers: dict[str, str] = {}
    for segment in segments:
        for key, value in _argv_target_identifiers(segment).items():
            prior = identifiers.get(key)
            if prior is not None and prior != value:
                return {}
            identifiers[key] = value
    return identifiers


def _local_read_only_segments(
    item: dict[str, object],
) -> tuple[tuple[str, ...], ...] | None:
    raw_command = item.get("argv") or item.get("command")
    if isinstance(raw_command, list) and all(
        isinstance(part, str) for part in raw_command
    ):
        argv = tuple(raw_command)
        if not argv:
            return None
        executable = Path(argv[0]).name
        if executable in {"bash", "sh", "zsh"}:
            for flag in ("-lc", "-c"):
                if flag in argv:
                    index = argv.index(flag)
                    if index + 1 < len(argv):
                        return _local_read_only_segments(
                            {"command": argv[index + 1]}
                        )
            return None
        return (argv,) if _is_local_read_only_segment(argv) else None
    if not isinstance(raw_command, str) or _contains_shell_command_substitution(
        raw_command
    ):
        return None
    try:
        lexer = shlex.shlex(
            raw_command,
            posix=True,
            punctuation_chars="|&;<>\n",
        )
        lexer.whitespace = " \t\r\n"
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = tuple(lexer)
    except ValueError:
        return None
    if not tokens:
        return None
    segments: list[tuple[str, ...]] = []
    current: list[str] = []
    for token in tokens:
        if token in _SHELL_CONNECTORS:
            if not current:
                return None
            segment = tuple(current)
            if not _is_local_read_only_segment(segment):
                return None
            segments.append(segment)
            current = []
            continue
        if token and all(character in "|&;<>\n" for character in token):
            return None
        current.append(token)
    if not current:
        return None
    segment = tuple(current)
    if not _is_local_read_only_segment(segment):
        return None
    segments.append(segment)
    return tuple(segments)


def _is_local_read_only_segment(argv: tuple[str, ...]) -> bool:
    if not argv:
        return False
    return _is_service_read_only_python_command(argv) or _is_agent_instruction_read(
        argv
    )


def _is_agent_instruction_read(argv: tuple[str, ...]) -> bool:
    """Recognize the bounded local reads required before an agent may decide."""
    if len(argv) != 4 or Path(argv[0]).name != "sed" or argv[1] != "-n":
        return False
    expression, raw_path = argv[2:]
    if not expression.endswith("p") or not expression[:-1].replace(",", "").isdigit():
        return False
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute() or candidate.name not in {"AGENT.md", "SKILL.md"}:
        return False
    trusted_roots = (Path.home() / ".agents", Path.home() / ".codex" / "skills")
    return any(candidate.is_relative_to(root) for root in trusted_roots)


def _is_service_read_only_python_command(argv: tuple[str, ...]) -> bool:
    """Recognize the service's fixed OA adapter before local policy applies."""
    if len(argv) != 6:
        return False
    executable, module_flag, module, command, instance_flag, process_id = argv
    return (
        executable == str(central_python())
        and module_flag == "-m"
        and module == "app.cli"
        and command in _SERVICE_READ_ONLY_PYTHON_COMMANDS
        and instance_flag == "--instance-id"
        and bool(process_id)
        and not process_id.startswith("-")
    )


def structured_target_identifiers(value: object) -> dict[str, str]:
    identifiers: dict[str, str] = {}
    stack: list[object] = [value]
    while stack and len(identifiers) < 32:
        current = stack.pop()
        if isinstance(current, list):
            stack.extend(current[:64])
            continue
        if not isinstance(current, dict):
            continue
        for key, item in current.items():
            normalized = str(key).replace("_", "").replace("-", "").casefold()
            if isinstance(item, str) and (
                normalized == "id"
                or normalized.endswith("id")
                or normalized.endswith("url")
            ):
                if item and not contains_credential(item):
                    identifiers[str(key)] = safe_observability_error(item, limit=500)
            elif isinstance(item, dict | list):
                stack.append(item)
    return identifiers


def _contains_shell_command_substitution(command: str) -> bool:
    escaped = False
    for index, character in enumerate(command):
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if character == "`":
            return True
        if character == "$" and command[index + 1 : index + 2] == "(":
            return True
    return False


def _command_path_candidates(argv: tuple[str, ...]) -> tuple[str, ...]:
    command_tokens: list[str] = []
    for token in argv:
        if token.startswith("-"):
            break
        command_tokens.append(token)
    return tuple(
        " ".join(command_tokens[:length])
        for length in range(len(command_tokens), 0, -1)
    )


def _argv_target_identifiers(argv: tuple[str, ...]) -> dict[str, str]:
    identifiers: dict[str, str] = {}
    index = 1
    while index < len(argv):
        token = argv[index]
        if not token.startswith("--"):
            index += 1
            continue
        flag, separator, inline_value = token[2:].partition("=")
        normalized = flag.replace("_", "-").casefold()
        is_target = normalized.endswith("-id") or normalized in {
            "id",
            "conversation",
            "group",
            "email",
            "from",
            "node",
            "url",
            "user",
            "uuid",
        }
        if separator:
            value = inline_value
        elif index + 1 < len(argv) and not argv[index + 1].startswith("-"):
            value = argv[index + 1]
            index += 1
        else:
            value = ""
        if is_target and value and not contains_credential(value):
            identifiers[normalized] = safe_observability_error(value, limit=500)
        index += 1
    return identifiers


def _classified_native_command(
    cli: str,
    command_path: str,
    argv: tuple[str, ...],
    effect: EffectKind | None,
) -> NativeCliCommand | None:
    if effect is EffectKind.READ_ONLY and not _safe_read_local_output(
        command_path, argv
    ):
        return None
    normalized = shlex.join(argv)
    return NativeCliCommand(
        cli=cli,
        command_path=command_path,
        effect=effect,
        command_digest=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        target_identifiers=_argv_target_identifiers(argv),
    )


def _safe_read_local_output(command_path: str, argv: tuple[str, ...]) -> bool:
    destinations: list[str] = []
    index = 1
    while index < len(argv):
        argument = argv[index]
        matched_inline = False
        for flag in _LOCAL_OUTPUT_FLAGS:
            prefix = f"{flag}="
            if argument.startswith(prefix):
                destinations.append(argument[len(prefix) :])
                matched_inline = True
                break
        if matched_inline:
            index += 1
            continue
        if argument in _LOCAL_OUTPUT_FLAGS:
            if index + 1 >= len(argv):
                return False
            destinations.append(argv[index + 1])
            index += 2
            continue
        index += 1
    cli = Path(argv[0]).name
    command_key = (cli, command_path)
    has_output_verb = any(
        part.casefold() in _LOCAL_OUTPUT_VERBS for part in command_path.split()
    )
    if (
        command_key in _REJECTED_LOCAL_OUTPUT_COMMANDS
        or has_output_verb
        and command_key not in _ALLOWED_MATERIAL_DOWNLOAD_COMMANDS
    ):
        return False
    if command_key in _ALLOWED_MATERIAL_DOWNLOAD_COMMANDS:
        return len(destinations) == 1 and _safe_material_destination(destinations[0])
    return not destinations


def _safe_material_destination(value: str) -> bool:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute() or candidate.name in {"", ".", ".."}:
        return False
    root = MATERIAL_OUTPUT_ROOT.resolve(strict=False)
    resolved = candidate.resolve(strict=False)
    return resolved.parent == root and not resolved.exists()


def _classify_cli_help(argv: tuple[str, ...]) -> NativeCliCommand | None:
    cli = Path(argv[0]).name
    if cli not in {"dws", "lark-cli"} or argv[-1] not in {"--help", "-h"}:
        return None
    command_paths = _command_path_candidates(argv[1:])
    if not command_paths:
        return None
    return _classified_native_command(
        cli,
        command_paths[0],
        argv,
        EffectKind.READ_ONLY,
    )


def describe_native_command(item: dict[str, object]) -> NativeCliCommand | None:
    local_read = _classify_local_read_only_command(item)
    if local_read is not None:
        return local_read
    argv = native_command_argv(item)
    if argv is None or "--dry-run" in argv:
        return None
    outgoing_message_path = dingtalk_outgoing_message_command_path(argv)
    if outgoing_message_path is not None:
        return _classified_native_command(
            "dws",
            outgoing_message_path,
            argv,
            None,
        )
    command_paths = _command_path_candidates(argv[1:])
    if not command_paths:
        return None
    return _classified_native_command(
        Path(argv[0]).name,
        command_paths[0],
        argv,
        None,
    )


def dingtalk_outgoing_message_command_path(
    argv: tuple[str, ...] | None,
) -> str | None:
    if argv is None or not argv or Path(argv[0]).name != "dws":
        return None
    for command_path in DINGTALK_OUTGOING_MESSAGE_COMMAND_PATHS:
        command_parts = tuple(command_path.split())
        if argv[1 : 1 + len(command_parts)] == command_parts:
            return command_path
    return None


def dingtalk_message_text(argv: tuple[str, ...] | None) -> str:
    location = _dingtalk_message_text_location(argv)
    if argv is None or location is None:
        return ""
    index, inline_prefix = location
    return argv[index][len(inline_prefix) :] if inline_prefix else argv[index]


def replace_dingtalk_message_text(
    argv: tuple[str, ...],
    replacement: str,
) -> tuple[str, ...]:
    location = _dingtalk_message_text_location(argv)
    if location is None:
        return argv
    index, inline_prefix = location
    prepared = list(argv)
    prepared[index] = f"{inline_prefix}{replacement}"
    return tuple(prepared)


def _dingtalk_message_text_location(
    argv: tuple[str, ...] | None,
) -> tuple[int, str] | None:
    command_path = dingtalk_outgoing_message_command_path(argv)
    if argv is None or command_path is None:
        return None
    for index, value in enumerate(argv):
        if value in {"--text", "--content", "--markdown"} and index + 1 < len(argv):
            candidate = argv[index + 1]
            return (index + 1, "") if candidate else None
        if value.startswith(("--text=", "--content=", "--markdown=")):
            return index, value.partition("=")[0] + "="
    if command_path != "chat message send":
        return None
    index = 1 + len(command_path.split())
    while index < len(argv):
        value = argv[index]
        if not value.startswith("-"):
            return index, ""
        if (
            "=" not in value
            and value not in _DWS_BOOLEAN_FLAGS
            and index + 1 < len(argv)
            and not argv[index + 1].startswith("-")
        ):
            index += 1
        index += 1
    return None
