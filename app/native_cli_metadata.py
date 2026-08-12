from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.agent_result import EffectKind
from app.bounded_process import ProcessOutputLimitError, run_bounded_process
from app.history import safe_observability_error
from app.leak_check import contains_credential


_SHELL_CONNECTORS = frozenset({"&&", "||", "|", ";"})
CODEX_CONFIG_PATH_ENV = "CEO_AGENT_CODEX_CONFIG_PATH"
LOCAL_READ_POLICY_TABLE = ("ceo_agent", "local_read_policy")
_SERVICE_READ_ONLY_PYTHON_COMMANDS = frozenset({"read-oa-approval-detail"})
_LOCAL_READ_ONLY_COMMANDS = frozenset(
    {
        "cat",
        "date",
        "file",
        "find",
        "grep",
        "head",
        "ls",
        "pwd",
        "rg",
        "sed",
        "sort",
        "stat",
        "tail",
        "wc",
    }
)
_INTRINSICALLY_BLOCKED_ARGUMENT_PREFIXES = {
    "find": (
        "-delete",
        "-exec",
        "-execdir",
        "-fls",
        "-fprint",
        "-fprint0",
        "-fprintf",
        "-ok",
        "-okdir",
    ),
    "grep": ("--pre", "--generate"),
    "rg": ("--pre", "--generate"),
    "sort": ("-o", "--output"),
    "tail": ("-f", "--follow"),
}
_SAFE_SED_PRINT_SCRIPT = re.compile(r"^(?:[0-9]+|\$)(?:,(?:[0-9]+|\$))?p$")


def service_read_command_contract() -> tuple[str, ...]:
    """Return the registered service-owned read commands for session versioning."""
    return tuple(sorted(_SERVICE_READ_ONLY_PYTHON_COMMANDS))


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


@dataclass(frozen=True)
class LocalReadCommandPolicy:
    """The principal-owned blacklist for local Consumer read commands."""

    blocked_commands: frozenset[str]
    blocked_argument_prefixes: dict[str, tuple[str, ...]]

    def allows(self, argv: tuple[str, ...]) -> bool:
        executable = Path(argv[0]).name.casefold()
        if executable in self.blocked_commands:
            return False
        return not any(
            argument.startswith(prefix)
            for prefix in self.blocked_argument_prefixes.get(executable, ())
            for argument in argv[1:]
        )


def load_local_read_command_policy(
    path: Path | None = None,
) -> LocalReadCommandPolicy | None:
    """Load the local command blacklist from the principal's Codex config."""
    config_path = path or _local_read_policy_config_path()
    try:
        payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    current: object = payload
    for key in LOCAL_READ_POLICY_TABLE:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    if not isinstance(current, dict):
        return None
    raw_commands = current.get("blocked_commands")
    raw_prefixes = current.get("blocked_argument_prefixes", {})
    if (
        not isinstance(raw_commands, list)
        or not all(_valid_policy_token(item) for item in raw_commands)
        or not isinstance(raw_prefixes, dict)
    ):
        return None
    prefixes: dict[str, tuple[str, ...]] = {}
    for command, values in raw_prefixes.items():
        if (
            not _valid_policy_token(command)
            or not isinstance(values, list)
            or not all(_valid_policy_token(value) for value in values)
        ):
            return None
        prefixes[command.casefold()] = tuple(values)
    return LocalReadCommandPolicy(
        blocked_commands=frozenset(item.casefold() for item in raw_commands),
        blocked_argument_prefixes=prefixes,
    )


def _local_read_policy_config_path() -> Path:
    configured = os.environ.get(CODEX_CONFIG_PATH_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser() / "config.toml"


def _valid_policy_token(value: object) -> bool:
    return isinstance(value, str) and bool(value) and "/" not in value


@lru_cache(maxsize=1)
def _load_reviewed_dws_effects() -> dict[tuple[str, str], EffectKind]:
    effects: dict[tuple[str, str], EffectKind] = {}
    try:
        process = run_bounded_process(
            ["dws", "schema", "--all", "--compact", "--format", "json"],
            timeout=30,
        )
    except ProcessOutputLimitError as exc:
        raise NativeCliMetadataUnavailableError(
            cli="dws", code="native_cli_metadata_output_limit"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise NativeCliMetadataUnavailableError(
            cli="dws", code="native_cli_metadata_timeout"
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise NativeCliMetadataUnavailableError(
            cli="dws", code="native_cli_metadata_start"
        ) from exc
    if process.returncode != 0:
        raise NativeCliMetadataUnavailableError(
            cli="dws", code="native_cli_metadata_nonzero"
        )
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise NativeCliMetadataUnavailableError(
            cli="dws", code="native_cli_metadata_invalid_json"
        ) from exc
    products = payload.get("products") if isinstance(payload, dict) else None
    if not isinstance(products, list):
        raise NativeCliMetadataUnavailableError(
            cli="dws", code="native_cli_metadata_invalid_json"
        )
    for product in products:
        tools = product.get("tools") if isinstance(product, dict) else None
        if not isinstance(tools, list):
            continue
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            command_path = tool.get("cli_path")
            effect = tool.get("effect")
            if not isinstance(command_path, str) or not command_path.strip():
                continue
            if effect == "read":
                parsed = EffectKind.READ_ONLY
            elif effect == "write":
                parsed = EffectKind.EFFECTFUL
            else:
                continue
            effects[("dws", command_path.strip())] = parsed
    return effects


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
        for cli, loader in (
            ("dws", _load_reviewed_dws_effects),
            ("lark-cli", _load_reviewed_lark_effects),
        ):
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
    if _is_service_read_only_python_command(argv):
        return True
    executable = Path(argv[0]).name
    # DWS and Lark use installed metadata rather than the local command policy.
    if executable in {"dws", "lark-cli"}:
        return False
    if not _is_intrinsically_read_only_local_command(argv):
        return False
    policy = load_local_read_command_policy()
    return policy is not None and policy.allows(argv)


def _is_intrinsically_read_only_local_command(argv: tuple[str, ...]) -> bool:
    """Allow only commands whose accepted shape cannot create an external effect."""
    executable = Path(argv[0]).name.casefold()
    if executable not in _LOCAL_READ_ONLY_COMMANDS:
        return False
    if any(
        argument.startswith(prefix)
        for prefix in _INTRINSICALLY_BLOCKED_ARGUMENT_PREFIXES.get(executable, ())
        for argument in argv[1:]
    ):
        return False
    if executable == "sed":
        return _is_safe_sed_read(argv[1:])
    if executable == "date":
        return all(
            argument == "-u" or argument.startswith("+") for argument in argv[1:]
        )
    return True


def _is_safe_sed_read(arguments: tuple[str, ...]) -> bool:
    scripts: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in {"-n", "-E"}:
            index += 1
            continue
        if argument == "-e":
            if index + 1 >= len(arguments):
                return False
            scripts.append(arguments[index + 1])
            index += 2
            continue
        if argument.startswith("-"):
            return False
        if not scripts:
            scripts.append(argument)
        break
    return bool(scripts) and all(
        _SAFE_SED_PRINT_SCRIPT.fullmatch(script) for script in scripts
    )


def _is_service_read_only_python_command(argv: tuple[str, ...]) -> bool:
    """Recognize the service's fixed OA adapter before local policy applies."""
    if len(argv) != 6:
        return False
    executable, module_flag, module, command, instance_flag, process_id = argv
    return (
        executable == ".venv/bin/python"
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
    effect: EffectKind,
) -> NativeCliCommand:
    normalized = shlex.join(argv)
    return NativeCliCommand(
        cli=cli,
        command_path=command_path,
        effect=effect,
        command_digest=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        target_identifiers=_argv_target_identifiers(argv),
    )


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
    command_paths = _command_path_candidates(argv[1:])
    if not command_paths:
        return None
    return _classified_native_command(
        Path(argv[0]).name,
        command_paths[0],
        argv,
        None,
    )
