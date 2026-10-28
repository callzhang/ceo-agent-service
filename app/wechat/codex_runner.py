"""Drive WeChat through Codex computer use (a virtual pointer).

WeChat's chat list only answers real pointer clicks, and a real click takes the
user's mouse. Codex ships a computer-use tool that operates an app in the
background without moving the user's pointer or bringing the app forward, so
one Codex turn opens the chat, types the reply key by key and presses Return.
Nothing here claims the message was delivered: the caller reads WeChat's own
message database afterwards (Derek, 2026-10-28).
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from app.agent_result import parse_typed_agent_result
from app.agent_runtime_contracts import DESKTOP_COMPUTER_USE
from app.agent_runtime_router import (
    CodexCommandFactory,
    RoutedCodexExecutionError,
    RoutedResultCodec,
    RoutedResultValidationError,
)
from app.wechat.accessibility import AccessibilityResult, SenderExecutionError

WECHAT_BUNDLE_ID = "com.tencent.xinWeChat"
WECHAT_UI_CAPABILITIES = frozenset({"structured_output", DESKTOP_COMPUTER_USE})
_RESULT_CODEC = RoutedResultCodec.text(schema_id="wechat_ui.result.v1")

_DEVELOPER_INSTRUCTIONS = """\
You operate the user's WeChat desktop app (bundle id com.tencent.xinWeChat) with
your computer-use tool. Work in the background with the virtual pointer: never
move the user's real mouse and never bring WeChat or any other app to the front.
Act like a careful person: one step at a time, never repeat an action that may
already have taken effect. Touch only the chat you are asked about. Do not open,
read aloud or change any other chat, contact, setting or file.

Pause between steps like a person (WeChat logs the account out when UI events
arrive back to back). In your computer-use JavaScript define and use:
  const pause = (lo, hi) => new Promise(r => setTimeout(r, (lo + Math.random() * (hi - lo)) * 1000));
and await, with a fresh random value every time:
- pause(0.6, 1.4) after getting the WeChat app and before the first action;
- pause(0.25, 0.7) before every click and pause(0.4, 1.0) after it;
- pause(0.06, 0.2) after every typed letter, digit or symbol;
- pause(0.3, 0.8) after every typed chunk of Chinese or other non-ASCII text;
- pause(0.5, 1.2) after checking the input box and before pressing Return.
Never batch several clicks or keystrokes without these pauses.

End your turn with exactly one JSON object and nothing else.
"""

_OPEN_STEPS = """\
1. Open the chat named exactly «{target}».
{locate}
2. Read the title shown above the message input box of the opened chat."""

_LOCATE_BY_RECENT = """\
   Pick it from the recent-chat list: the row named «{target}» whose latest
   message preview contains «{recent}». If no single row matches both, stop and
   report failure; do not use search."""

_LOCATE_BY_SEARCH = """\
   Prefer its row in the recent-chat list; otherwise search for «{query}» and
   open the result named exactly «{target}»."""

_SEND_PROMPT = """\
Send one message in WeChat.

{open_steps}
3. If that title is not exactly «{target}», stop without typing anything.
4. Click into the message input box and type the message below exactly as
   written with your keyboard-typing action. Type letters, digits and symbols
   one character at a time; type Chinese and other non-ASCII text in short
   chunks of 2 to 4 characters, the way an input method commits a word. Never
   paste it and never set the input box value directly. A bare Return sends in WeChat, so
   insert a line break with Shift+Return; after the first one, check that
   nothing was sent and the input box still holds the text.
5. Check that the input box now contains exactly the message and nothing else.
   If it does not, clear the input box and stop without sending.
6. Press Return once to send. Do not press it again and do not retry.

Message (between the markers, markers excluded):
<<<MESSAGE
{text}
MESSAGE>>>

Reply with one JSON object:
{{"opened_title": "<title read in step 2, or empty>",
  "pressed_return": <true only if you pressed Return in step 6>,
  "failure_reason": "<empty, or a short snake_case reason you stopped>"}}
"""

_OPEN_PROMPT = """\
Open one chat in WeChat and report its title. Do not type or send anything.

{open_steps}

Reply with one JSON object:
{{"opened_title": "<title read in step 2, or empty>",
  "pressed_return": false,
  "failure_reason": "<empty, or a short snake_case reason>"}}
"""


class WechatUiResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opened_title: str = ""
    pressed_return: bool = False
    failure_reason: str = ""


def _parse_result(raw: str) -> str:
    try:
        return parse_typed_agent_result(raw, WechatUiResult).model_dump_json()
    except ValueError as exc:
        raise RoutedResultValidationError(
            "WeChat UI turn returned no result object", raw_output=raw,
        ) from exc


def _open_steps(target: str, *, search_query: str | None,
                expected_recent_text: str | None) -> str:
    if expected_recent_text:
        locate = _LOCATE_BY_RECENT.format(target=target, recent=expected_recent_text)
    else:
        locate = _LOCATE_BY_SEARCH.format(target=target, query=search_query or target)
    return _OPEN_STEPS.format(target=target, locate=locate)


def _workload_key(operation: str, *parts: str) -> str:
    digest = hashlib.sha256("\0".join((operation, *parts)).encode()).hexdigest()
    return f"wechat-ui:{operation}:{digest}"


def _wechat_pid() -> int | None:
    # The service's Python has no AppKit bridge; ask Launch Services directly.
    try:
        output = subprocess.run(
            ["/usr/bin/lsappinfo", "info", "-only", "pid", "-app", WECHAT_BUNDLE_ID],
            check=False, capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    _, _, value = output.partition("=")
    return int(value) if value.strip().isdigit() and int(value) > 0 else None


def _screen_locked() -> bool:
    try:
        import Quartz
    except Exception:
        return False
    session = Quartz.CGSessionCopyCurrentDictionary() or {}
    return bool(session.get("CGSSessionScreenIsLocked", False))


class CodexWechatRunner:
    """The WeChat runner interface (``send``/``open_and_identify``) over Codex."""

    def __init__(self, *, store=None, routed_execution=None, workspace: Path | None = None,
                 total_timeout_seconds: float = 600.0,
                 idle_timeout_seconds: float = 300.0):
        self._store = store
        self._routed_execution = routed_execution
        self._workspace = workspace
        self._total_timeout_seconds = total_timeout_seconds
        self._idle_timeout_seconds = idle_timeout_seconds

    def _execution(self):
        if self._routed_execution is None:
            if self._store is None:
                raise SenderExecutionError(
                    "WeChat UI turn needs the service store",
                    action_may_have_started=False,
                )
            from app import config
            from app.agent_runtime_production import (
                build_production_routed_codex_execution,
            )

            self._routed_execution = build_production_routed_codex_execution(
                store=self._store,
                workspace=self._workspace or config.workspace_path(),
                total_timeout_seconds=self._total_timeout_seconds,
                idle_timeout_seconds=self._idle_timeout_seconds,
            )
        return self._routed_execution

    def _run(self, workload_key: str, prompt: str) -> WechatUiResult:
        try:
            result = self._execution().execute(
                workload_kind="wechat_ui",
                workload_key=workload_key,
                prompt=prompt,
                command_factory=CodexCommandFactory.standard(
                    developer_instructions=_DEVELOPER_INSTRUCTIONS,
                    use_output_schema=False,
                    skill_names=(),
                    project_doc_max_bytes=0,
                    wechat_ui_desktop_control=True,
                ),
                parser=_parse_result,
                result_codec=_RESULT_CODEC,
                conversation_id=None,
                required_capabilities=WECHAT_UI_CAPABILITIES,
            )
        except RoutedCodexExecutionError as exc:
            # Without a route nothing reached WeChat; the delivery waits for one.
            raise SenderExecutionError(
                f"WeChat UI turn did not run: {exc.code} {exc.reason}".strip(),
                action_may_have_started=not exc.runtime_unavailable,
            ) from exc
        return WechatUiResult.model_validate_json(result.value)

    def check_readiness(self) -> str:
        if _screen_locked():
            return "screen_locked"
        if not _wechat_pid():
            return "wechat_not_running"
        return "ready"

    def health(self) -> dict:
        return {"status": self.check_readiness()}

    def request_accessibility(self) -> str:
        # Codex computer use holds its own macOS grants.
        return self.check_readiness()

    def send(
        self, target_label: str, reply_text: str, *, search_query: str | None = None,
        expected_recent_text: str | None = None, skip_idle_wait: bool = False,
    ) -> AccessibilityResult:
        del skip_idle_wait  # The virtual pointer never interrupts the user.
        readiness = self.check_readiness()
        if readiness != "ready":
            return AccessibilityResult(False, False, failure_reason=readiness)
        prompt = _SEND_PROMPT.format(
            open_steps=_open_steps(
                target_label, search_query=search_query,
                expected_recent_text=expected_recent_text,
            ),
            target=target_label,
            text=reply_text,
        )
        outcome = self._run(_workload_key("send", target_label, reply_text), prompt)
        if not outcome.pressed_return:
            reason = outcome.failure_reason or "codex_did_not_send"
            if outcome.opened_title and outcome.opened_title != target_label:
                reason = "target_binding_mismatch"
            return AccessibilityResult(False, False, failure_reason=reason)
        if outcome.opened_title != target_label:
            # Return was pressed in a chat that is not the target: uncertain.
            return AccessibilityResult(
                True, False, failure_reason="target_binding_mismatch",
            )
        # Delivery is confirmed only by WeChat's own message database.
        return AccessibilityResult(True, False)

    def open_and_identify(
        self, target_label: str, *, search_query: str | None = None,
        expected_recent_text: str | None = None, keep_foreground: bool = False,
    ) -> str:
        if self.check_readiness() != "ready":
            return ""
        prompt = _OPEN_PROMPT.format(open_steps=_open_steps(
            target_label, search_query=search_query,
            expected_recent_text=expected_recent_text,
        ))
        try:
            outcome = self._run(
                _workload_key("open", target_label, expected_recent_text or ""), prompt,
            )
        except SenderExecutionError:
            return ""
        if keep_foreground and outcome.opened_title:
            # The user asked to look at the chat: show WeChat.
            subprocess.run(["/usr/bin/open", "-b", WECHAT_BUNDLE_ID], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=5)
        return outcome.opened_title
