"""Claim Feishu tasks, run Codex, and prepare audited outbound work.

This module has no reference to a channel client or send API.  The consumer's
strong invariant is that it can only create durable delivery/action rows; a
separate runtime worker owns every Feishu network side effect.  Human handoff
also creates a durable local-notification fallback in the same transaction as
the validated decision; an offline-only worker owns the eventual OS effect.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from app.dingtalk_models import CodexAction
from app.feishu.actions import normalize_reaction_emoji
from app.feishu.delivery import delivery_idempotency_key
from app.feishu.media import DEFAULT_MAX_RESOURCE_BYTES, MEDIA_ROOT_PARTS
from app.feishu.models import FeishuInboundMessage
from app.feishu.payloads import choose_reply_payload
from app.feishu.prompt import build_feishu_turn_prompt
from app.leak_check import contains_forbidden_leak


class UnsafeFeishuReply(ValueError):
    pass


MIN_PROCESSING_STALE_SECONDS = 30 * 60
MAX_CONTEXT_LOOKBACK_SECONDS = 30 * 24 * 60 * 60
MAX_CONTEXT_MESSAGES = 100
_IMAGE_MIME_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/webp"}
)
_SHA256_HEX = frozenset("0123456789abcdef")
_OPEN_ID_RE = re.compile(r"^ou_[A-Za-z0-9_-]+$")
_REACTION_ACTION_FIELDS = frozenset(
    {
        "type",
        "reaction_type",
        "emoji",
        "text",
        "emotion_id",
        "emotion_name",
        "background_id",
    }
)


def _action_value(action: Any) -> str:
    return str(getattr(action, "value", action))


def _sensitivity_value(decision: Any) -> str:
    value = getattr(decision, "sensitivity_kind", "general")
    return str(getattr(value, "value", value) or "general")


def _contains_forbidden_side_effect(decision: Any) -> bool:
    calendar = getattr(decision, "calendar_response_status", "")
    calendar_value = str(getattr(calendar, "value", calendar) or "").lower()
    return bool(
        getattr(decision, "system_actions", None)
        or getattr(decision, "ding_self", False)
        or calendar_value not in {"", "none"}
    )


def _contains_forbidden_non_system_side_effect(decision: Any) -> bool:
    calendar = getattr(decision, "calendar_response_status", "")
    calendar_value = str(getattr(calendar, "value", calendar) or "").lower()
    return bool(
        getattr(decision, "ding_self", False)
        or calendar_value not in {"", "none"}
    )


def _reaction_contract(decision: Any) -> tuple[str, str]:
    """Return a closed reaction decision as ``(state, emoji_type)``.

    ``state`` is one of ``none``, ``emoji``, ``text_emotion``, or ``invalid``.
    No message or identity target is accepted from model output.
    """
    actions = getattr(decision, "system_actions", None) or []
    if not isinstance(actions, list) or len(actions) > 1:
        return "invalid", ""
    if not actions:
        return "none", ""
    action = actions[0]
    if not isinstance(action, dict) or set(action) - _REACTION_ACTION_FIELDS:
        return "invalid", ""
    if str(action.get("type") or "").strip() != "dws_message_reaction":
        return "invalid", ""
    reaction_type = str(action.get("reaction_type") or "").strip()
    if reaction_type == "text_emotion":
        return "text_emotion", ""
    if reaction_type != "emoji":
        return "invalid", ""
    if any(
        str(action.get(field) or "").strip()
        for field in ("text", "emotion_id", "emotion_name", "background_id")
    ):
        return "invalid", ""
    try:
        emoji_type = normalize_reaction_emoji(
            str(action.get("emoji") or "")
        )
    except ValueError:
        return "invalid", ""
    return "emoji", emoji_type


def _audit_with_status(summary: str, status: str) -> str:
    cleaned = str(summary or "").strip()
    return f"{cleaned};{status}" if cleaned else status


def _bounded_handoff_text(trigger: FeishuInboundMessage) -> str:
    def safe(value: str, *, maximum: int) -> str:
        cleaned = "".join(
            character
            for character in str(value or "")[:maximum]
            if ord(character) >= 32 or character in "\n\t"
        )
        return " ".join(cleaned.split())

    chat = safe(trigger.chat_title, maximum=200) or "未命名会话"
    sender = safe(trigger.sender_name, maximum=100) or "未知用户"
    message_id = safe(trigger.message_id, maximum=256)
    return (
        f"飞书会话需要人工接管：{chat}；发起人：{sender}；"
        f"消息 ID：{message_id}"
    )[:2000]


def _default_leak_check(text: str) -> str:
    if contains_forbidden_leak(text):
        raise UnsafeFeishuReply("reply_failed_leak_check")
    return text


class FeishuReplyConsumer:
    """Channel-isolated Codex consumer that never possesses a sender."""

    def __init__(
        self,
        store,
        runner,
        *,
        app_id: str,
        context_limit: int = 20,
        context_lookback_seconds: int = 24 * 60 * 60,
        leak_check: Callable[[str], str] | None = None,
        media_enabled: bool = False,
        media_workspace: Path | None = None,
        media_max_assets: int = 8,
        media_max_bytes: int = DEFAULT_MAX_RESOURCE_BYTES,
        reaction_enabled: bool = False,
        handoff_enabled: bool = False,
        handoff_open_ids: tuple[str, ...] = (),
        reply_mention_sender: bool = False,
    ):
        normalized_app_id = str(app_id or "").strip()
        if not normalized_app_id:
            raise ValueError("Feishu consumer requires app_id")
        if context_limit <= 0 or context_limit > MAX_CONTEXT_MESSAGES:
            raise ValueError("context_limit must be between 1 and 100")
        if (
            context_lookback_seconds <= 0
            or context_lookback_seconds > MAX_CONTEXT_LOOKBACK_SECONDS
        ):
            raise ValueError(
                "context_lookback_seconds must be between 1 and 2592000"
            )
        if getattr(runner, "tool_mode", None) != "none":
            raise ValueError(
                "Feishu consumer requires hard Codex tool isolation"
            )
        if media_max_assets <= 0 or media_max_assets > 8:
            raise ValueError("media_max_assets must be between 1 and 8")
        if media_max_bytes <= 0:
            raise ValueError("media_max_bytes must be positive")
        self.store = store
        self.runner = runner
        self.app_id = normalized_app_id
        self.context_limit = context_limit
        self.context_lookback_seconds = context_lookback_seconds
        self.leak_check = leak_check or _default_leak_check
        self.media_enabled = bool(media_enabled)
        self.media_max_assets = media_max_assets
        self.media_max_bytes = media_max_bytes
        self.reaction_enabled = bool(reaction_enabled)
        self.handoff_enabled = bool(handoff_enabled)
        if isinstance(handoff_open_ids, str):
            raise ValueError("Feishu handoff allowlist must be a local sequence")
        normalized_handoff_open_ids: list[str] = []
        for target in handoff_open_ids:
            if (
                not isinstance(target, str)
                or target != target.strip()
                or not _OPEN_ID_RE.fullmatch(target)
            ):
                raise ValueError("Feishu handoff allowlist contains invalid target")
            if target not in normalized_handoff_open_ids:
                normalized_handoff_open_ids.append(target)
        if len(normalized_handoff_open_ids) > 20:
            raise ValueError("Feishu handoff allowlist must contain at most 20 IDs")
        self.handoff_open_ids = tuple(normalized_handoff_open_ids)
        self.reply_mention_sender = bool(reply_mention_sender)
        self.media_workspace: Path | None = None
        self.media_root: Path | None = None
        if media_workspace is not None:
            candidate = Path(media_workspace)
        else:
            store_path = getattr(store, "path", None)
            candidate = Path(store_path).parent if store_path is not None else None
        if candidate is not None:
            if not candidate.exists() or not candidate.is_dir():
                raise ValueError(
                    "Feishu media workspace must be an existing directory"
                )
            self.media_workspace = candidate.resolve(strict=True)
            self.media_root = self.media_workspace.joinpath(*MEDIA_ROOT_PARTS)
        elif self.media_enabled:
            raise ValueError(
                "Feishu media requires an explicit workspace or store DB path"
            )
        try:
            runner_timeout = int(getattr(runner, "timeout_seconds", 0) or 0)
        except (TypeError, ValueError):
            runner_timeout = 0
        self.processing_stale_seconds = max(
            MIN_PROCESSING_STALE_SECONDS,
            # CodexDecisionRunner may consume two full executor windows while
            # repairing malformed JSON, plus bounded session-grace reads.
            # Reclaiming after only one timeout can duplicate model execution.
            (runner_timeout * 2) + (3 * 60),
        )

    def run_once(self, limit: int = 50) -> int:
        if limit <= 0:
            return 0
        # A standalone Feishu consumer must recover its own crash residue; the
        # main DingTalk worker is not guaranteed to be running.  Scope the
        # reset so one channel cannot steal another channel's task.
        self.store.reset_stale_processing_reply_tasks(
            self.processing_stale_seconds,
            channel="feishu",
            feishu_app_id=self.app_id,
        )
        processed = 0
        for _ in range(limit):
            claimed = self.store.claim_reply_tasks(
                1, channel="feishu", feishu_app_id=self.app_id
            )
            if not claimed:
                break
            task = claimed[0]
            try:
                self.process(task)
            except Exception as exc:
                # Keep one corrupt task or a stale-lease CAS miss from
                # terminating the long-running consumer component.
                self._fail_claimed_task(
                    task,
                    f"feishu_consumer_failed:{type(exc).__name__}",
                )
            processed += 1
        return processed

    def _fail_claimed_task(self, task, error: str) -> bool:
        return self.store.fail_processing_reply_task(
            task.id,
            error,
            channel="feishu",
            lease_token=task.lease_token,
            feishu_app_id=self.app_id,
        )

    @staticmethod
    def _trigger(task) -> FeishuInboundMessage:
        raw = str(getattr(task, "trigger_message_json", "") or "")
        if not raw or raw == "{}":
            raise ValueError("missing normalized Feishu trigger")
        return FeishuInboundMessage.model_validate_json(raw)

    @staticmethod
    def _unavailable_summary() -> str:
        return "附件不可用；不可猜测。"

    @staticmethod
    def _ready_non_image_summary(resource_type: str) -> str:
        return {
            "file": "文件附件已接收，但内容未提供给模型，不可猜测。",
            "audio": "音频附件已接收，但没有转写内容，不可猜测。",
            "video": "视频附件已接收，但内容未解析，不可猜测。",
        }.get(resource_type, FeishuReplyConsumer._unavailable_summary())

    def _verified_image_path(
        self,
        asset,
        *,
        event_record_id: int,
        trigger: FeishuInboundMessage,
        snapshot_dir: Path,
    ) -> Path | None:
        """Copy one verified inode into a private per-decision snapshot.

        The runner must never reopen the mutable retention path that was
        checked here.  Reading, hashing, and copying all use the same source
        descriptor; the returned file lives in a process-private directory
        until the synchronous decision call has completed.
        """
        if (
            not self.media_enabled
            or self.media_workspace is None
            or self.media_root is None
            or asset.status != "ready"
            or asset.event_record_id != event_record_id
            or asset.app_id != trigger.app_id
            or asset.message_id != trigger.message_id
            or asset.resource_type != "image"
            or asset.mime_type not in _IMAGE_MIME_TYPES
            or asset.size_bytes <= 0
            or asset.size_bytes > self.media_max_bytes
        ):
            return None

        digest = str(asset.sha256 or "").strip().lower()
        if len(digest) != 64 or any(char not in _SHA256_HEX for char in digest):
            return None
        relative_value = str(asset.relative_path or "").strip()
        if (
            not relative_value
            or "\\" in relative_value
            or any(ord(char) < 32 or ord(char) == 127 for char in relative_value)
        ):
            return None
        relative = PurePosixPath(relative_value)
        app_digest = hashlib.sha256(
            trigger.app_id.encode("utf-8")
        ).hexdigest()
        expected_parts = (
            *MEDIA_ROOT_PARTS,
            app_digest,
            digest[:2],
            digest,
        )
        if relative.is_absolute() or relative.parts != expected_parts:
            return None
        source_fd = -1
        directory_fd = -1
        snapshot_directory_fd = -1
        snapshot_fd = -1
        snapshot_path: Path | None = None
        snapshot_complete = False
        try:
            directory_flags = os.O_RDONLY
            if hasattr(os, "O_DIRECTORY"):
                directory_flags |= os.O_DIRECTORY
            if hasattr(os, "O_NOFOLLOW"):
                directory_flags |= os.O_NOFOLLOW
            directory_fd = os.open(self.media_workspace, directory_flags)
            for part in relative.parts[:-1]:
                next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = next_fd

            source_flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                source_flags |= os.O_NOFOLLOW
            source_fd = os.open(
                relative.parts[-1], source_flags, dir_fd=directory_fd
            )
            opened = os.fstat(source_fd)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_size != asset.size_bytes
            ):
                return None

            snapshot_directory_fd = os.open(snapshot_dir, directory_flags)
            extension = {
                "image/png": "png",
                "image/jpeg": "jpg",
                "image/webp": "webp",
            }[asset.mime_type]
            snapshot_name = f"{int(asset.ordinal):02d}-{digest}.{extension}"
            snapshot_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                snapshot_flags |= os.O_NOFOLLOW
            snapshot_fd = os.open(
                snapshot_name,
                snapshot_flags,
                0o600,
                dir_fd=snapshot_directory_fd,
            )
            snapshot_path = snapshot_dir / snapshot_name
            hasher = hashlib.sha256()
            copied = 0
            while True:
                chunk = os.read(source_fd, 1024 * 1024)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > asset.size_bytes:
                    return None
                hasher.update(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(snapshot_fd, view)
                    if written <= 0:
                        raise OSError("short Feishu image snapshot write")
                    view = view[written:]
            closed_state = os.fstat(source_fd)
            if (
                copied != asset.size_bytes
                or closed_state.st_size != opened.st_size
                or closed_state.st_ino != opened.st_ino
                or closed_state.st_dev != opened.st_dev
                or hasher.hexdigest() != digest
            ):
                return None
            os.fchmod(snapshot_fd, 0o600)
            os.fsync(snapshot_fd)
            snapshot_complete = True
        except (FileNotFoundError, NotADirectoryError, OSError, ValueError):
            return None
        finally:
            for descriptor in (
                snapshot_fd,
                source_fd,
                directory_fd,
                snapshot_directory_fd,
            ):
                if descriptor >= 0:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
            if snapshot_path is not None and not snapshot_complete:
                try:
                    snapshot_path.unlink()
                except FileNotFoundError:
                    pass
        return snapshot_path

    def _media_context(
        self, trigger: FeishuInboundMessage, *, snapshot_dir: Path
    ) -> tuple[list[Path], list[str]]:
        event = self.store.get_feishu_event_for_message(
            trigger.app_id, trigger.message_id
        )
        if (
            event is None
            or event.app_id != trigger.app_id
            or event.message_id != trigger.message_id
        ):
            return (
                [],
                [self._unavailable_summary()]
                if trigger.message_type
                in {"image", "file", "audio", "media", "sticker"}
                else [],
            )
        assets = self.store.list_feishu_media_assets(
            event_record_id=event.id,
            app_id=trigger.app_id,
            message_id=trigger.message_id,
            limit=self.media_max_assets + 1,
        )
        if not assets:
            return (
                [],
                [self._unavailable_summary()]
                if trigger.message_type
                in {"image", "file", "audio", "media", "sticker"}
                else [],
            )
        image_paths: list[Path] = []
        summaries: list[str] = []
        overflow = len(assets) > self.media_max_assets
        selected_assets = assets[
            : self.media_max_assets - 1 if overflow else self.media_max_assets
        ]
        for asset in selected_assets:
            if (
                asset.event_record_id != event.id
                or asset.app_id != trigger.app_id
                or asset.message_id != trigger.message_id
            ):
                summaries.append(self._unavailable_summary())
                continue
            if asset.status != "ready":
                summaries.append(self._unavailable_summary())
                continue
            if asset.resource_type == "image":
                image_path = self._verified_image_path(
                    asset,
                    event_record_id=event.id,
                    trigger=trigger,
                    snapshot_dir=snapshot_dir,
                )
                if image_path is None:
                    summaries.append(self._unavailable_summary())
                else:
                    image_paths.append(image_path)
                    summaries.append("图片附件已安全验证，可用于图像理解。")
                continue
            if asset.resource_type == "sticker":
                summaries.append(self._unavailable_summary())
                continue
            summaries.append(
                self._ready_non_image_summary(asset.resource_type)
            )
        expected_content_type = {
            "image": "image",
            "file": "file",
            "audio": "audio",
            "media": "video",
            "sticker": "sticker",
        }.get(trigger.message_type)
        if expected_content_type and not any(
            asset.event_record_id == event.id
            and asset.app_id == trigger.app_id
            and asset.message_id == trigger.message_id
            and asset.role == "content"
            and asset.resource_type == expected_content_type
            for asset in selected_assets
        ):
            summaries.append(self._unavailable_summary())
        if overflow:
            summaries.append(self._unavailable_summary())
        return image_paths, summaries

    def process(self, task) -> None:
        snapshot_owner: tempfile.TemporaryDirectory[str] | None = None
        try:
            trigger = self._trigger(task)
        except Exception:
            self._fail_claimed_task(task, "invalid_feishu_trigger")
            return
        if trigger.app_id != self.app_id:
            self._fail_claimed_task(task, "feishu_consumer_app_mismatch")
            return

        try:
            if self.store.recover_feishu_reply_task(
                task.id, app_id=self.app_id, lease_token=task.lease_token
            ):
                return
        except Exception:
            self._fail_claimed_task(task, "feishu_recovery_failed")
            return

        try:
            context = self.store.list_feishu_context(
                trigger.chat_id,
                limit=self.context_limit,
                app_id=trigger.app_id,
                thread_id=trigger.thread_id,
                root_message_id=trigger.root_message_id,
                before_message_id=trigger.message_id,
                lookback_seconds=self.context_lookback_seconds,
            )
            snapshot_owner = tempfile.TemporaryDirectory(
                prefix="ceo-agent-feishu-images-"
            )
            snapshot_dir = Path(snapshot_owner.name)
            snapshot_dir.chmod(0o700)
            image_paths, attachment_summaries = self._media_context(
                trigger, snapshot_dir=snapshot_dir
            )
            prompt = build_feishu_turn_prompt(
                trigger,
                context,
                context_limit=self.context_limit,
                attachment_summaries=attachment_summaries,
            )
            if image_paths:
                # Do not fall back to text-only execution when the runner does
                # not implement the image contract: that would silently lose
                # the user's evidence and invite hallucination.
                decision = self.runner.decide(
                    prompt, None, image_paths=image_paths
                )
            else:
                decision = self.runner.decide(prompt, None)
        except Exception as exc:
            self._fail_claimed_task(
                task, f"feishu_decision_failed:{type(exc).__name__}"
            )
            return
        finally:
            if snapshot_owner is not None:
                snapshot_owner.cleanup()

        action = _action_value(decision.action)
        checked_text = ""
        leak_failed = False
        raw_draft = str(getattr(decision, "reply_text", "") or "").strip()
        if action in {
            CodexAction.SEND_REPLY.value,
            CodexAction.ASK_CLARIFYING_QUESTION.value,
        }:
            try:
                checked_text = self.leak_check(raw_draft).strip()
                # An injected formatter may redact, but may never bypass the
                # channel's mandatory final leak check.
                if contains_forbidden_leak(checked_text):
                    raise UnsafeFeishuReply("reply_failed_leak_check")
            except Exception:
                leak_failed = True
        audited_draft = ""
        if action in {
            CodexAction.SEND_REPLY.value,
            CodexAction.ASK_CLARIFYING_QUESTION.value,
        }:
            audited_draft = (
                "[redacted unsafe draft]"
                if contains_forbidden_leak(raw_draft) or leak_failed
                else (checked_text if checked_text else raw_draft)
            )
        finalize_common = {
            "app_id": self.app_id,
            "lease_token": task.lease_token,
            "action": action,
            "sensitivity_kind": _sensitivity_value(decision),
            "codex_reason": str(getattr(decision, "reason", "") or ""),
            "draft_reply_text": audited_draft,
            "audit_summary": str(
                getattr(decision, "audit_summary", "") or ""
            ),
        }

        if action in {
            CodexAction.SEND_REPLY.value,
            CodexAction.ASK_CLARIFYING_QUESTION.value,
        }:
            if _contains_forbidden_side_effect(decision):
                self.store.finalize_feishu_reply_task(
                    task.id,
                    **finalize_common,
                    task_status="failed",
                    send_status="failed",
                    error="external_system_actions_rejected",
                )
                return
            if leak_failed:
                self.store.finalize_feishu_reply_task(
                    task.id,
                    **finalize_common,
                    task_status="failed",
                    send_status="failed",
                    error="reply_failed_leak_check",
                )
                return
            text = checked_text
            if not text:
                self.store.finalize_feishu_reply_task(
                    task.id,
                    **finalize_common,
                    task_status="failed",
                    send_status="failed",
                    error="empty_feishu_reply",
                )
                return
            trusted_mentions = (
                (trigger.sender_open_id,)
                if self.reply_mention_sender
                and trigger.chat_type in {"group", "topic"}
                else ()
            )
            try:
                payload = choose_reply_payload(
                    text,
                    trusted_mention_open_ids=trusted_mentions,
                )
            except ValueError:
                self.store.finalize_feishu_reply_task(
                    task.id,
                    **finalize_common,
                    task_status="failed",
                    send_status="failed",
                    error="invalid_feishu_reply_payload",
                )
                return
            self.store.finalize_feishu_reply_task(
                task.id,
                **{**finalize_common, "draft_reply_text": payload.text},
                task_status="done",
                send_status="pending",
                delivery_app_id=trigger.app_id,
                delivery_chat_id=trigger.chat_id,
                reply_to_message_id=trigger.message_id,
                reply_in_thread=bool(
                    trigger.thread_id or trigger.chat_type == "topic"
                ),
                reply_format=payload.kind,
                mention_open_ids=payload.mention_open_ids,
                payload_sha256=payload.sha256(),
                idempotency_key=delivery_idempotency_key(
                    app_id=trigger.app_id,
                    reply_task_id=task.id,
                    trigger_message_id=trigger.message_id,
                ),
            )
            return

        if action == CodexAction.NO_REPLY.value:
            if _contains_forbidden_non_system_side_effect(decision):
                self.store.finalize_feishu_reply_task(
                    task.id,
                    **finalize_common,
                    task_status="failed",
                    send_status="failed",
                    error="external_system_actions_rejected",
                )
                return
            reaction_state, emoji_type = _reaction_contract(decision)
            if reaction_state == "invalid":
                self.store.finalize_feishu_reply_task(
                    task.id,
                    **finalize_common,
                    task_status="failed",
                    send_status="failed",
                    error="external_system_actions_rejected",
                )
                return
            if reaction_state == "text_emotion":
                self.store.finalize_feishu_reply_task(
                    task.id,
                    **{
                        **finalize_common,
                        "audit_summary": _audit_with_status(
                            finalize_common["audit_summary"],
                            "feishu_text_emotion_has_no_equivalent",
                        ),
                    },
                    task_status="done",
                    send_status="skipped",
                    error="feishu_text_emotion_has_no_equivalent",
                )
                return
            if reaction_state == "emoji" and not self.reaction_enabled:
                self.store.finalize_feishu_reply_task(
                    task.id,
                    **{
                        **finalize_common,
                        "audit_summary": _audit_with_status(
                            finalize_common["audit_summary"],
                            "feishu_reaction_gate_closed",
                        ),
                    },
                    task_status="done",
                    send_status="skipped",
                    error="feishu_reaction_gate_closed",
                )
                return
            message_action_specs = ()
            audit_summary = finalize_common["audit_summary"]
            if reaction_state == "emoji":
                message_action_specs = (
                    {
                        "action_key": "trigger_reaction",
                        "kind": "add_reaction",
                        # The target is bound to durable trigger context.  No
                        # target field from model output is ever inspected.
                        "target_message_id": trigger.message_id,
                        "payload": {"emoji_type": emoji_type},
                    },
                )
                audit_summary = _audit_with_status(
                    audit_summary, "feishu_reaction_queued"
                )
            self.store.finalize_feishu_reply_task(
                task.id,
                **{**finalize_common, "audit_summary": audit_summary},
                task_status="done",
                send_status="skipped",
                message_action_specs=message_action_specs,
            )
            return

        if action == CodexAction.HANDOFF_TO_HUMAN.value:
            handoff_text = _bounded_handoff_text(trigger)
            handoff_statuses: list[str] = []
            handoff_errors: list[str] = []
            if _contains_forbidden_side_effect(decision):
                handoff_statuses.append("external_system_actions_rejected")
                self.store.finalize_feishu_reply_task(
                    task.id,
                    **{
                        **finalize_common,
                        "audit_summary": _audit_with_status(
                            finalize_common["audit_summary"],
                            ";".join(handoff_statuses),
                        ),
                    },
                    task_status="failed",
                    send_status="failed",
                    error="external_system_actions_rejected",
                )
                return

            message_action_specs: tuple[dict[str, Any], ...] = ()
            local_notification_spec = {
                "kind": "handoff_fallback",
                "dependency_mode": "immediate",
                "title": "CEO Feishu handoff required",
                "message": handoff_text,
            }
            if not self.handoff_enabled:
                handoff_statuses.append("feishu_handoff_gate_closed")
                handoff_errors.append("feishu_handoff_gate_closed")
                handoff_statuses.append(
                    "feishu_handoff_local_fallback_queued"
                )
            elif not self.handoff_open_ids:
                handoff_statuses.append("feishu_handoff_allowlist_empty")
                handoff_errors.append("feishu_handoff_allowlist_empty")
                handoff_statuses.append(
                    "feishu_handoff_local_fallback_queued"
                )
            else:
                message_action_specs = tuple(
                    {
                        "action_key": (
                            "handoff:"
                            + hashlib.sha256(target.encode("utf-8")).hexdigest()[
                                :20
                            ]
                        ),
                        "kind": "handoff_notify",
                        "target_open_id": target,
                        "payload": {"text": handoff_text},
                    }
                    for target in self.handoff_open_ids
                )
                handoff_statuses.append(
                    f"feishu_handoff_actions_queued:{len(message_action_specs)}"
                )
                local_notification_spec["dependency_mode"] = "remote_failure"
                handoff_statuses.append(
                    "feishu_handoff_local_fallback_waiting"
                )
            self.store.finalize_feishu_reply_task(
                task.id,
                **{
                    **finalize_common,
                    "audit_summary": _audit_with_status(
                        finalize_common["audit_summary"],
                        ";".join(handoff_statuses),
                    ),
                },
                task_status="done",
                send_status="skipped",
                error=";".join(handoff_errors),
                message_action_specs=message_action_specs,
                handoff_target_allowlist=self.handoff_open_ids,
                local_notification_spec=local_notification_spec,
            )
            return
        self.store.finalize_feishu_reply_task(
            task.id,
            **finalize_common,
            task_status="failed",
            send_status="failed",
            error="unexpected_feishu_decision",
        )
