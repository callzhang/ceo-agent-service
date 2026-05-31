import importlib
import importlib.util
import os
import re
import sys
from pathlib import Path
from types import ModuleType

from ceo_agent_service.config import (
    memory_connector_user_id,
    repo_root,
    user_alias,
    write_env_values,
)
from ceo_agent_service.leak_check import FORBIDDEN_MARKERS


TAG_RE = re.compile(r"<(file|code|var):\s*([^>]+?)\s*>")
CODE_RE = re.compile(
    r"^([A-Za-z0-9_./-]+(?:\.[A-Za-z_][A-Za-z0-9_]*)?):"
    r"([A-Za-z_][A-Za-z0-9_]*)\(\)$"
)
VARIABLE_BLOCK_RE = re.compile(r"\A\s*<vars>\s*\n(?P<body>.*?)\n</vars>\s*", re.DOTALL)
VARIABLE_DEFINITION_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
DEFAULT_DEVELOPER_PROMPT_TEMPLATE = repo_root() / "prompts" / "developer_prompt.md"
DEFAULT_USER_PROMPT_TEMPLATE = repo_root() / "prompts" / "user_prompt.md"
PROMPT_VARIABLE_ENV_PREFIX = "CEO_PROMPT_VAR_"
CONFIGURABLE_PROMPT_VARIABLE_DEFAULTS = {
    "responsibility_summary": (
        "Use the configured organization responsibility rules to decide whether "
        "the principal should reply."
    ),
    "forbidden_reply_text_terms": "",
    "oa_approval_rules": "management/OA/钉钉审批审阅原则.md",
    "calendar_rules_path": "management/OA/日历规则.md",
}


class DeveloperPromptTemplateError(ValueError):
    pass


def developer_prompt_template_path() -> Path:
    return Path(
        os.getenv(
            "CEO_DEVELOPER_PROMPT_TEMPLATE_PATH",
            str(DEFAULT_DEVELOPER_PROMPT_TEMPLATE),
        )
    )


def user_prompt_template_path() -> Path:
    return Path(
        os.getenv(
            "CEO_USER_PROMPT_TEMPLATE_PATH",
            str(DEFAULT_USER_PROMPT_TEMPLATE),
        )
    )


def read_developer_prompt_template(path: Path | None = None) -> str:
    template_path = path or developer_prompt_template_path()
    return template_path.read_text(encoding="utf-8")


def read_user_prompt_template(path: Path | None = None) -> str:
    template_path = path or user_prompt_template_path()
    return template_path.read_text(encoding="utf-8")


def write_developer_prompt_template(text: str, path: Path | None = None) -> Path:
    template_path = path or developer_prompt_template_path()
    template_path.parent.mkdir(parents=True, exist_ok=True)
    template_path.write_text(text, encoding="utf-8")
    return template_path


def write_user_prompt_template(text: str, path: Path | None = None) -> Path:
    template_path = path or user_prompt_template_path()
    template_path.parent.mkdir(parents=True, exist_ok=True)
    template_path.write_text(text, encoding="utf-8")
    return template_path


def render_developer_prompt(path: Path | None = None) -> str:
    return render_developer_prompt_template(read_developer_prompt_template(path))


def render_user_prompt(
    variables: dict[str, str],
    path: Path | None = None,
) -> str:
    return render_user_prompt_template(read_user_prompt_template(path), variables)


def render_developer_prompt_template(template: str) -> str:
    variable_definitions, body = split_developer_prompt_template(template)
    variables = prompt_template_variables()
    variables.update(parse_developer_prompt_variables(variable_definitions))

    return _render_template_tags(body, variables)


def render_user_prompt_template(
    template: str,
    runtime_variables: dict[str, str],
) -> str:
    from ceo_agent_service.user_prompt_blocks import user_prompt_block_context

    variable_definitions, body = split_developer_prompt_template(template)
    variables = prompt_template_variables()
    variables.update(parse_developer_prompt_variables(variable_definitions))
    variables.update(runtime_variables)

    if not runtime_variables:
        return _render_template_tags(body, variables)
    with user_prompt_block_context(runtime_variables):
        return _render_template_tags(body, variables)


def split_developer_prompt_template(template: str) -> tuple[str, str]:
    match = VARIABLE_BLOCK_RE.match(template)
    if not match:
        return "", template
    return match.group("body").strip(), template[match.end() :].lstrip("\n")


def merge_developer_prompt_template(variable_definitions: str, body: str) -> str:
    variable_text = variable_definitions.strip()
    body_text = body.strip()
    if not variable_text:
        return body_text
    return f"<vars>\n{variable_text}\n</vars>\n\n{body_text}"


def developer_prompt_variable_pairs(variable_definitions: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for line_number, raw_line in enumerate(variable_definitions.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = VARIABLE_DEFINITION_RE.match(line)
        if not match:
            raise DeveloperPromptTemplateError(
                f"invalid variable definition on line {line_number}: {raw_line}"
            )
        pairs.append(match.groups())
    return pairs


def format_developer_prompt_variables(pairs: list[tuple[str, str]]) -> str:
    lines: list[str] = []
    for key, value in pairs:
        name = key.strip()
        text = value.strip()
        if not name and not text:
            continue
        if not VARIABLE_DEFINITION_RE.match(f"{name} = {text}"):
            raise DeveloperPromptTemplateError(f"invalid variable name: {name}")
        lines.append(f"{name} = {text}")
    return "\n".join(lines)


def configurable_prompt_variable_pairs() -> list[tuple[str, str]]:
    variables = prompt_template_variables()
    return [
        (key, variables.get(key, ""))
        for key in CONFIGURABLE_PROMPT_VARIABLE_DEFAULTS
    ]


def prompt_variable_env_key(name: str) -> str:
    return f"{PROMPT_VARIABLE_ENV_PREFIX}{name.upper()}"


def write_configurable_prompt_variables(pairs: list[tuple[str, str]]) -> None:
    updates: dict[str, str] = {}
    allowed_keys = set(CONFIGURABLE_PROMPT_VARIABLE_DEFAULTS)
    env_key_to_template_key = {
        prompt_variable_env_key(key): key for key in allowed_keys
    }
    for key, value in pairs:
        name = key.strip()
        if not name and not value.strip():
            continue
        name = env_key_to_template_key.get(name, name)
        if name not in allowed_keys:
            raise DeveloperPromptTemplateError(
                f"unsupported config variable: {name}"
            )
        updates[prompt_variable_env_key(name)] = value.strip()
    write_env_values(updates)


def prompt_template_variables() -> dict[str, str]:
    variables = {
        "principal": user_alias(),
        "handoff_name": user_alias(),
        "memory_user_id": memory_connector_user_id(),
    }
    for key, default in CONFIGURABLE_PROMPT_VARIABLE_DEFAULTS.items():
        variables[key] = os.getenv(prompt_variable_env_key(key), default)
    if not variables["forbidden_reply_text_terms"]:
        variables["forbidden_reply_text_terms"] = forbidden_reply_text_terms()
    return variables


def parse_developer_prompt_variables(variable_definitions: str) -> dict[str, str]:
    variables: dict[str, str] = {}
    for name, value in developer_prompt_variable_pairs(variable_definitions):
        variables[name] = _render_template_tags(value, {}, allow_variables=False)
    return variables


def _render_template_tags(
    template: str,
    variables: dict[str, str],
    *,
    allow_variables: bool = True,
) -> str:
    def replace(match: re.Match[str]) -> str:
        kind = match.group(1)
        expression = match.group(2).strip()
        if kind == "var":
            if not allow_variables:
                raise DeveloperPromptTemplateError(
                    "variable definitions cannot reference <var: ...> tags"
                )
            if expression not in variables:
                raise DeveloperPromptTemplateError(f"unknown template variable: {expression}")
            return variables[expression]
        if kind == "file":
            return _read_template_file(expression)
        if kind == "code":
            return _run_template_code(expression)
        raise DeveloperPromptTemplateError(f"unsupported template tag: {kind}")

    return TAG_RE.sub(replace, template)


def forbidden_reply_text_terms() -> str:
    return "、".join(f"`{marker}`" for marker in FORBIDDEN_MARKERS)


def _read_template_file(expression: str) -> str:
    path = _resolve_repo_path(expression)
    try:
        return path.read_text(encoding="utf-8").rstrip()
    except OSError as exc:
        raise DeveloperPromptTemplateError(f"cannot read template file {path}: {exc}") from exc


def _run_template_code(expression: str) -> str:
    match = CODE_RE.match(expression)
    if not match:
        raise DeveloperPromptTemplateError(
            "code tag must look like <code: ceo_agent_service.module:function()> "
            "or <code: scripts/file.py:function()>"
        )
    target, function_name = match.groups()
    module = _load_template_module(target)
    function = getattr(module, function_name, None)
    if not callable(function):
        raise DeveloperPromptTemplateError(f"template code is not callable: {expression}")
    result = function()
    return "" if result is None else str(result).rstrip()


def _load_template_module(target: str) -> ModuleType:
    if target.endswith(".py") or "/" in target:
        return _load_template_file_module(target)
    if not target.startswith("ceo_agent_service."):
        raise DeveloperPromptTemplateError(
            "module code tags are restricted to ceo_agent_service.* modules"
        )
    return importlib.import_module(target)


def _load_template_file_module(target: str) -> ModuleType:
    path = _resolve_repo_path(target)
    if path.suffix != ".py":
        raise DeveloperPromptTemplateError("file code tags must point to a .py file")
    module_name = f"_ceo_developer_prompt_{abs(hash(path))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise DeveloperPromptTemplateError(f"cannot load template code file: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise DeveloperPromptTemplateError(f"template code failed in {path}: {exc}") from exc
    return module


def _resolve_repo_path(value: str) -> Path:
    raw_path = Path(value)
    path = raw_path if raw_path.is_absolute() else repo_root() / raw_path
    resolved = path.resolve()
    root = repo_root().resolve()
    if not _is_relative_to(resolved, root):
        raise DeveloperPromptTemplateError(f"template path must stay inside repo: {value}")
    return resolved


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
