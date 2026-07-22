import json
import logging
import re
from pathlib import Path

from app.codex_decision import (
    extract_codex_audit_events,
    extract_codex_session_id,
    _subprocess_failure_reason,
)
from app.codex_history import (
    count_codex_session_lines,
    extract_codex_audit_events_from_session,
)
from app.codex_runner import (
    CODEX_BYPASS_APPROVALS_AND_SANDBOX,
    CodexRunner,
    _config_string,
    memory_connector_config_options,
)
from app.process_runner import run_process_with_idle_timeout
from app.store import AutoReplyStore, CodexDevTask


CODEX_DEV_TASK_AUDIT_EVENT_LIMIT = 200
logger = logging.getLogger(__name__)
CODEX_DEV_DEVELOPER_INSTRUCTIONS = """You are Codex, executing a development task that Mina explicitly authorized from DingTalk.

Rules:
- Work in the provided repository only.
- Inspect the codebase before editing.
- Do a Context enrichment pass before planning or writing: use the available memory_connector MCP tools, especially memory_recall, to retrieve relevant prior decisions, meeting notes, project history, user preferences, and durable context. Do not pass or invent user_id; the connector uses the authenticated identity.
- When the task is underspecified, infer the intended work from Memory and the task record instead of producing a generic artifact. If Memory is unavailable or insufficient, state that clearly and use the best available local/DingTalk context.
- For meeting, management, OKR, organization-process, HR, policy, or operating-mechanism tasks, search Memory for weekly management meetings, Sunday management issue discussions, recent quarterly OKR/QBR meetings, and Mina's work style before producing the result.
- Make focused code changes and add/adjust tests when behavior changes.
- Run relevant tests before reporting completion.
- Do not push, deploy, create a PR, restart launchd, change production state, send DING notifications, comment on OA approvals, or perform live-send verification.
- Only send DingTalk messages to the originating DingTalk conversation in the task record. Do not send direct messages to Mina and do not post in other chats.
- For completion status, include a concise result and every user-facing artifact link as a raw clickable URL, not only a filename, title, local path, or Markdown link label. If you create or upload a DingTalk document, query/derive and include its `https://alidocs.dingtalk.com/...` or `https://docs.dingtalk.com/...` URL in the final answer. If no clickable URL is available, say that explicitly.
- When the task asks to edit, update, or rewrite an existing DingTalk file/document/report, use the explicit DingTalk URL from the task or conversation context as the target. If no target DingTalk URL is available or writable, stop and report the missing target/permission. Do not treat a local similarly named file as the completed deliverable unless Mina explicitly asked for a local-only file.
- Do not run destructive git commands such as git reset --hard, git clean, or checkout/restore user changes.
- If the worktree has unrelated local changes, preserve them and report the risk.
- Stop and report if there are merge conflicts, failing tests you cannot fix safely, missing credentials, network failures, or ambiguous destructive instructions.
- Final response must be concise: conclusion, files changed, tests run, blockers/risks.
"""
URL_PATTERN = re.compile(r"https?://[^\s<>)\"'，。；;\\]+")
MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]+)]\(([^)]+)\)")
ARTIFACT_COMMAND_PATTERN = re.compile(
    r"\bdws\s+(?:doc\s+create|drive\s+upload|doc\s+upload|"
    r"drive\s+folder\s+create|doc\s+folder\s+create)\b"
)
NON_USER_FACING_URL_HOSTS = {
    "schemas.openxmlformats.org",
    "schemas.microsoft.com",
    "purl.org",
    "www.w3.org",
}


class CodexDevRunner:
    def __init__(
        self,
        workspace: Path,
        codex_bin: str = "codex",
        executor=None,
        timeout_seconds: int = 1800,
        idle_timeout_seconds: int = 600,
    ):
        self.workspace = workspace
        self.codex_bin = codex_bin
        self.executor = executor
        self.timeout_seconds = timeout_seconds
        self.idle_timeout_seconds = idle_timeout_seconds
        self.runner = CodexRunner(workspace=workspace, codex_bin=codex_bin)
        self.last_session_id: str | None = None
        self.last_audit_tool_events: list[dict[str, str]] = []
        self.last_transcript_start_line = 0
        self.last_transcript_end_line = 0

    def build_command(self, prompt: str) -> list[str]:
        del prompt
        return [
            self.codex_bin,
            "exec",
            "--json",
            "-m",
            "gpt-5.5",
            "--ignore-rules",
            "--disable",
            "hooks",
            *memory_connector_config_options(),
            "-c",
            'approval_policy="untrusted"',
            "-c",
            'approvals_reviewer="auto_review"',
            "-c",
            _config_string("developer_instructions", CODEX_DEV_DEVELOPER_INSTRUCTIONS),
            "-c",
            'model_reasoning_summary="concise"',
            "-c",
            "include_permissions_instructions=false",
            "-c",
            "include_apps_instructions=false",
            "-c",
            "include_environment_context=false",
            CODEX_BYPASS_APPROVALS_AND_SANDBOX,
            "--cd",
            str(self.workspace),
            "-",
        ]

    def execute(self, task: CodexDevTask) -> str:
        self.last_transcript_start_line = 0
        prompt = build_codex_dev_task_prompt(task)
        command = self.build_command(prompt)
        if self.executor is not None:
            raw = self.executor(command, prompt)
        else:
            completed = run_process_with_idle_timeout(
                command,
                prompt=prompt,
                env=self.runner.build_env(),
                total_timeout_seconds=self.timeout_seconds,
                idle_timeout_seconds=self.idle_timeout_seconds,
            )
            if completed.timed_out:
                raise RuntimeError(
                    completed.timeout_reason or "codex dev task timed out"
                )
            if completed.returncode != 0:
                raise RuntimeError(
                    _subprocess_failure_reason(completed.stderr, completed.stdout)
                )
            raw = completed.stdout
        self.last_session_id = extract_codex_session_id(raw)
        self.last_transcript_end_line = count_codex_session_lines(self.last_session_id)
        session_events = []
        if self.last_session_id:
            session_events = extract_codex_audit_events_from_session(
                self.last_session_id,
                start_line=self.last_transcript_start_line,
                end_line=self.last_transcript_end_line,
                limit=CODEX_DEV_TASK_AUDIT_EVENT_LIMIT,
            )
        self.last_audit_tool_events = session_events or extract_codex_audit_events(raw)
        return raw


class CodexDevCompletionNotifier:
    def __init__(self, dws):
        self.dws = dws

    def notify_done(self, task: CodexDevTask, result_summary: str) -> None:
        at_open_dingtalk_ids, at_open_dingtalk_names = self._trigger_sender_mentions(
            task
        )
        self.dws.send_message(
            task.conversation_id,
            build_codex_dev_completion_message(task, result_summary),
            at_open_dingtalk_ids=at_open_dingtalk_ids,
            at_open_dingtalk_names=at_open_dingtalk_names,
        )
        self._ding_trigger_sender(task)

    def _ding_trigger_sender(self, task: CodexDevTask) -> None:
        if not task.trigger_sender_user_id:
            return
        ding_user = getattr(self.dws, "ding_user", None)
        if ding_user is None:
            return
        try:
            ding_user(task.trigger_sender_user_id, build_codex_dev_ding_text(task))
        except Exception as exc:
            logger.warning(
                "failed to send codex dev completion DING: %s",
                exc,
                extra={
                    "conversation_id": task.conversation_id,
                    "trigger_message_id": task.trigger_message_id,
                    "trigger_sender_user_id": task.trigger_sender_user_id,
                },
            )

    def _trigger_sender_mentions(
        self,
        task: CodexDevTask,
    ) -> tuple[list[str], list[str]]:
        if not task.trigger_sender_user_id:
            return [], []
        get_user_profile = getattr(self.dws, "get_user_profile", None)
        if get_user_profile is None:
            return [], []
        try:
            profile = get_user_profile(task.trigger_sender_user_id)
        except Exception:
            return [], []
        open_dingtalk_id = str(getattr(profile, "open_dingtalk_id", "") or "")
        if not open_dingtalk_id:
            return [], []
        name = str(getattr(profile, "name", "") or task.trigger_sender or "")
        return [open_dingtalk_id], [name] if name else []


def build_codex_dev_task_prompt(task: CodexDevTask) -> str:
    task_json = json.dumps(task.model_dump(mode="json"), ensure_ascii=False, indent=2)
    return f"""Mina explicitly asked from DingTalk:

Mina Agent，执行

Execute this development task now.

Task record:
{task_json}

Execution boundary:
- You may edit this repository and run local tests.
- Do not push, deploy, create a PR, restart launchd, or perform live-send verification.
- Only send DingTalk messages to the originating DingTalk conversation in this task record. Do not send direct messages to Mina and do not post in other chats.
- For completion status, include a concise result and every user-facing artifact link as a raw clickable URL, not only a filename, title, local path, or Markdown link label. If you create or upload a DingTalk document, query/derive and include its `https://alidocs.dingtalk.com/...` or `https://docs.dingtalk.com/...` URL in the final answer. If no clickable URL is available, say that explicitly.
- If the task asks to edit, update, or rewrite an existing DingTalk file/document/report, first identify the explicit target DingTalk URL from the task record or immediate conversation context. If no target URL is present or DWS cannot read/write it, stop and report that blocker. Editing a local file with a similar title is not completion unless Mina explicitly requested a local-only file.
- If the task asks for other higher-risk actions, stop after preparing local changes and report that Mina confirmation is required.

Context enrichment:
- Before planning or producing the artifact, call memory_recall through the memory_connector MCP when it is available.
- Build one or more focused memory_recall queries from the instruction, trigger_text, conversation_title, and likely domain terms.
- Do not pass user_id, graph_id, or graph_ids to memory tools; the installed connector uses the authenticated identity.
- If the task is short or underspecified, use Memory to recover the missing background, intended standard, prior decisions, and Mina's style instead of writing a generic answer.
- For meeting, management, OKR, organization-process, HR, policy, or operating-mechanism tasks, explicitly look for weekly management meetings, Sunday management issue discussions, recent quarterly OKR/QBR meetings, and related DingTalk document/minutes context.
- Ground the final artifact in retrieved evidence where possible. In the final response, mention the key context sources used or say what could not be retrieved.
"""


def process_codex_dev_tasks(
    store: AutoReplyStore,
    runner: CodexDevRunner,
    *,
    limit: int = 1,
    completion_notifier: CodexDevCompletionNotifier | None = None,
) -> int:
    processed = 0
    for task in store.claim_codex_dev_tasks(limit=limit):
        try:
            raw = runner.execute(task)
            result_summary = _summarize_codex_dev_output(raw)
            result_summary = _augment_summary_with_audit_result_links(
                result_summary,
                runner.last_audit_tool_events,
            )
            store.mark_codex_dev_task_done(
                task.id,
                result_summary=result_summary,
                codex_session_id=runner.last_session_id or "",
                codex_transcript_start_line=runner.last_transcript_start_line,
                codex_transcript_end_line=runner.last_transcript_end_line,
                audit_tool_events_json=json.dumps(
                    runner.last_audit_tool_events,
                    ensure_ascii=False,
                ),
            )
            if completion_notifier is not None:
                try:
                    completion_notifier.notify_done(task, result_summary)
                except Exception as notify_exc:
                    store.record_error(
                        task.conversation_id,
                        task.trigger_message_id,
                        "codex_dev_completion_notify",
                        str(notify_exc),
                    )
            processed += 1
        except Exception as exc:
            store.mark_codex_dev_task_failed(task.id, str(exc))
            store.record_error(
                task.conversation_id,
                task.trigger_message_id,
                "codex_dev_task",
                str(exc),
            )
    return processed


def build_codex_dev_completion_message(
    task: CodexDevTask,
    result_summary: str,
    *,
    max_summary_chars: int = 800,
) -> str:
    directory_links = _extract_directory_links(result_summary)
    links = _extract_result_links(result_summary)
    if directory_links:
        directory_link_set = set(directory_links)
        links = [link for link in links if link not in directory_link_set]
    local_files = _extract_local_file_references(result_summary)
    lines = [
        "已执行完毕。",
        f"任务：{task.instruction.strip() or task.trigger_text.strip()}",
    ]
    summary = _completion_result_summary(
        result_summary,
        links=directory_links + links,
        local_files=local_files,
        max_chars=max_summary_chars,
    )
    if summary and not (directory_links or links):
        lines.extend(["", f"结果：{summary}"])
    if directory_links:
        lines.append("")
        lines.append("目录链接：")
        lines.extend(f"- {link}" for link in directory_links)
    if links:
        lines.append("")
        lines.append("文档链接：" if directory_links else "结果链接：")
        lines.extend(f"- {link}" for link in links)
    if local_files:
        lines.append("")
        lines.append("本地文件：")
        lines.extend(f"- {label}：{path}" for label, path in local_files)
    if task.codex_session_id:
        lines.extend(["", f"执行记录：{task.codex_session_id}"])
    lines.append("")
    lines.append("（by Mina Agent）")
    return "\n".join(lines)


def build_codex_dev_ding_text(task: CodexDevTask, *, max_instruction_chars: int = 80) -> str:
    instruction = (task.instruction.strip() or task.trigger_text.strip()).replace(
        "\n", " "
    )
    instruction = re.sub(r"\s+", " ", instruction).strip()
    if len(instruction) > max_instruction_chars:
        instruction = instruction[: max_instruction_chars - 1].rstrip() + "…"
    return f"Mina Agent 已执行完毕，请回原对话查看：{instruction}"


def _extract_result_links(text: str, *, limit: int = 20) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    text = _preferred_result_link_text(text)
    for match in URL_PATTERN.finditer(text):
        link = _clean_result_link(match.group(0))
        if not _is_user_facing_result_link(link):
            continue
        if link in seen:
            continue
        seen.add(link)
        links.append(link)
        if len(links) >= limit:
            break
    return links


def _extract_directory_links(text: str, *, limit: int = 3) -> list[str]:
    section = _link_section_text(
        text, ("目录链接", "文件夹链接", "目录", "文件夹")
    )
    if section is None:
        return []
    return _extract_links_from_text(section, limit=limit)


def _preferred_result_link_text(text: str) -> str:
    artifact_section = _link_section_text(
        text,
        (
            "Artifact URL",
            "Artifact URLs",
            "Created URL",
            "Created document URL",
            "Created document URLs",
            "Uploaded URL",
            "Uploaded URLs",
            "Result URL",
            "Result URLs",
        ),
    )
    if artifact_section is not None:
        return artifact_section
    section = _link_section_text(text, ("文档链接", "结果链接"))
    if section is not None:
        return section
    return _normalize_summary_newlines(text)


def _link_section_text(text: str, headings: tuple[str, ...]) -> str | None:
    normalized = _normalize_summary_newlines(text)
    for heading in headings:
        match = re.search(
            rf"(?m)^{re.escape(heading)}\s*[:：]\s*(.*)",
            normalized,
            flags=re.S,
        )
        if not match:
            continue
        section = match.group(1)
        stop = re.search(
            r"(?m)^(?:目录链接|文件夹链接|文档链接|结果链接|文件夹|目录|本地文件|"
            r"Files changed|Files changed/generated locally|Tests/verification|"
            r"Context used|Context sources used|Risk|风险|验证|执行记录)\s*[:：]",
            section,
        )
        return section[: stop.start()] if stop else section
    return None


def _extract_links_from_text(text: str, *, limit: int) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    for match in URL_PATTERN.finditer(text):
        link = _clean_result_link(match.group(0))
        if not _is_user_facing_result_link(link):
            continue
        if link in seen:
            continue
        seen.add(link)
        links.append(link)
        if len(links) >= limit:
            break
    return links


def _augment_summary_with_audit_result_links(
    result_summary: str,
    audit_tool_events: list[dict[str, str]],
) -> str:
    links = _extract_audit_artifact_links(audit_tool_events)
    if not links:
        return result_summary
    existing = set(_extract_result_links(result_summary))
    existing.update(_extract_directory_links(result_summary))
    missing = [link for link in links if link not in existing]
    if not missing:
        return result_summary
    suffix = "\n".join(missing)
    return f"{result_summary.rstrip()}\n\nArtifact URL:\n{suffix}"


def _extract_audit_artifact_links(
    audit_tool_events: list[dict[str, str]],
    *,
    limit: int = 20,
) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    expecting_artifact_output = False
    for event in audit_tool_events:
        command = str(event.get("command") or "")
        if command:
            expecting_artifact_output = bool(ARTIFACT_COMMAND_PATTERN.search(command))
            for link in _extract_links_from_text(command, limit=limit):
                if expecting_artifact_output and link not in seen:
                    seen.add(link)
                    links.append(link)
            if len(links) >= limit:
                break
            output = str(event.get("output") or "")
            if output and expecting_artifact_output:
                for link in _extract_links_from_text(output, limit=limit):
                    if link in seen:
                        continue
                    seen.add(link)
                    links.append(link)
                    if len(links) >= limit:
                        break
            continue
        output = str(event.get("output") or "")
        if output and expecting_artifact_output:
            for link in _extract_links_from_text(output, limit=limit):
                if link in seen:
                    continue
                seen.add(link)
                links.append(link)
                if len(links) >= limit:
                    break
            expecting_artifact_output = False
        if len(links) >= limit:
            break
    return links


def _clean_result_link(link: str) -> str:
    return link.strip().rstrip("`'\".,，。；;)")


def _is_user_facing_result_link(link: str) -> bool:
    if not link:
        return False
    if "{" in link or "}" in link:
        return False
    if "..." in link or "…" in link:
        return False
    parsed = re.match(r"https?://([^/]+)", link, flags=re.IGNORECASE)
    if not parsed:
        return False
    host = parsed.group(1).casefold()
    if host in NON_USER_FACING_URL_HOSTS:
        return False
    if host.endswith(".openxmlformats.org"):
        return False
    if host.endswith(".microsoft.com") and host.startswith("schemas."):
        return False
    if link.rstrip("/") in {
        "https://alidocs.dingtalk.com",
        "https://docs.dingtalk.com",
    }:
        return False
    if host == "alidocs.dingtalk.com":
        path_match = re.match(r"https?://[^/]+(/[^?#]*)", link, flags=re.IGNORECASE)
        path = path_match.group(1).rstrip("/") if path_match else ""
        if path in {"", "/i/nodes"}:
            return False
    return True


def _extract_local_file_references(text: str, *, limit: int = 8) -> list[tuple[str, str]]:
    files: list[tuple[str, str]] = []
    seen: set[str] = set()
    for label, target in MARKDOWN_LINK_PATTERN.findall(text):
        target = target.strip()
        if URL_PATTERN.fullmatch(target):
            continue
        if not (target.startswith("/") or target.startswith("./") or target.startswith("../")):
            continue
        if target in seen:
            continue
        seen.add(target)
        files.append((label.strip() or target, target))
        if len(files) >= limit:
            break
    return files


def _compact_result_summary(text: str, *, max_chars: int) -> str:
    text = _normalize_summary_newlines(text)
    text = _replace_local_markdown_links(text)
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return ""
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 1].rstrip() + "…"


def _completion_result_summary(
    text: str,
    *,
    links: list[str],
    local_files: list[tuple[str, str]],
    max_chars: int,
) -> str:
    if _looks_like_inlined_artifact_body(text):
        title = _extract_generated_artifact_title(text)
        if links:
            return "报告已生成，见下方结果链接。"
        if local_files:
            return "报告已生成，见下方本地文件。"
        if title:
            return f"报告已生成，但未返回可点击链接；请在钉钉文档中查看：{title}"
        return "任务已完成，但未返回可点击结果链接。"
    return _compact_result_summary(text, max_chars=max_chars)


def _normalize_summary_newlines(text: str) -> str:
    return text.replace("\\r\\n", "\n").replace("\\n", "\n")


def _looks_like_inlined_artifact_body(text: str) -> bool:
    normalized = _normalize_summary_newlines(text)
    table_lines = sum(1 for line in normalized.splitlines() if line.lstrip().startswith("|"))
    if table_lines >= 2:
        return True
    if re.search(r"(?m)^#{1,3}\s+", normalized) and len(normalized) > 300:
        return True
    if "重点解读：" in normalized and len(normalized) > 300:
        return True
    return False


def _extract_generated_artifact_title(text: str) -> str:
    normalized = _normalize_summary_newlines(text)
    for label, _target in MARKDOWN_LINK_PATTERN.findall(normalized):
        label = label.strip()
        if label:
            return label
    match = re.search(
        r"(?:已(?:在本地)?生成(?:钉钉文档|文档|报告)?|已创建(?:钉钉文档|文档)?)"
        r"[:：]\s*([^\n，。；;]+?\.(?:adoc|md|docx|pdf|xlsx|axls|able))",
        normalized,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()
    return ""


def _replace_local_markdown_links(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        label = match.group(1).strip()
        target = match.group(2).strip()
        if URL_PATTERN.fullmatch(target):
            return match.group(0)
        if target.startswith("/") or target.startswith("./") or target.startswith("../"):
            return f"{label or target}：{target}"
        return match.group(0)

    return MARKDOWN_LINK_PATTERN.sub(replace, text)


def _summarize_codex_dev_output(raw: str, *, limit: int = 4000) -> str:
    final_message = _extract_final_answer_from_codex_jsonl(raw)
    if final_message:
        text = final_message.strip()
        if len(text) <= limit:
            return text
        return text[-limit:]
    text = raw.strip()
    if len(text) <= limit:
        return text
    return text[-limit:]


def _extract_final_answer_from_codex_jsonl(raw: str) -> str:
    final_answer = ""
    task_complete_message = ""
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        payload = event.get("payload")
        if event_type == "response_item" and isinstance(payload, dict):
            if payload.get("type") == "message" and payload.get("role") == "assistant":
                text = _message_payload_text(payload)
                if payload.get("phase") == "final_answer" and text:
                    final_answer = text
                elif text and not final_answer:
                    final_answer = text
        elif event_type == "event_msg" and isinstance(payload, dict):
            if payload.get("type") == "task_complete":
                message = str(payload.get("last_agent_message") or "").strip()
                if message:
                    task_complete_message = message
        elif event_type in {"item.completed", "item.started"}:
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                text = str(item.get("text") or "").strip()
                if text:
                    final_answer = text
    return final_answer or task_complete_message


def _message_payload_text(payload: dict) -> str:
    parts: list[str] = []
    content = payload.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    return "\n".join(parts).strip()
