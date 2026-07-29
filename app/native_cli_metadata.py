from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.agent_result import EffectKind
from app.history import safe_observability_error
from app.leak_check import contains_credential


class AgentReadOnlyViolationError(RuntimeError):
    pass


@dataclass(frozen=True)
class NativeCliCommand:
    cli: str
    command_path: str
    effect: EffectKind
    command_digest: str
    target_identifiers: dict[str, str]


@lru_cache(maxsize=1)
def _load_reviewed_dws_effects() -> dict[tuple[str, str], EffectKind]:
    effects: dict[tuple[str, str], EffectKind] = {}
    try:
        process = subprocess.run(
            ["dws", "schema", "--all", "--compact", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return effects
    if process.returncode != 0:
        return effects
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError:
        return effects
    products = payload.get("products") if isinstance(payload, dict) else None
    if not isinstance(products, list):
        return effects
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
        process = subprocess.run(
            ["lark-cli", "schema"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return effects
    if process.returncode != 0:
        return effects
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError:
        return effects
    if not isinstance(payload, list):
        return effects
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

    @property
    def cache_keys(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._cache))

    def prewarm(self) -> None:
        if self._prewarmed:
            return
        self._prewarmed = True
        self._cache.update(_load_reviewed_dws_effects())
        self._cache.update(_load_reviewed_lark_effects())

    def classify(self, item: dict[str, object]) -> NativeCliCommand | None:
        argv = native_command_argv(item)
        if argv is None or "--dry-run" in argv:
            return None
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
        argv = native_command_argv(item)
        if argv is None or "--dry-run" in argv:
            return None
        cli = Path(argv[0]).name
        for command_path in _command_path_candidates(argv[1:]):
            effect = self._cache.get((cli, command_path))
            if effect is not None:
                return _classified_native_command(cli, command_path, argv, effect)
        return None

    def _classify_dws(self, argv: tuple[str, ...]) -> NativeCliCommand | None:
        for command_path in _command_path_candidates(argv[1:]):
            try:
                process = subprocess.run(
                    [
                        argv[0],
                        "schema",
                        "--cli-path",
                        command_path,
                        "--compact",
                        "--format",
                        "json",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
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
                process = subprocess.run(
                    [argv[0], *command_path.split(), "--help"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
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
            "id", "conversation", "group", "email", "node", "url"
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
