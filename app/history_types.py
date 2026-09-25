"""The History page's task types: one list for the query and the filter.

Derek, 2026-09-25: the History type filter must name every kind of work the
service runs, and History must also show scheduled service-command runs and
email provider actions.  Before this module the page hard-coded seven
filters, and the union query put DingTalk messages, calendar invitations,
email replies and scheduled Agent runs under one value, ``replay``.

``HISTORY_TYPES`` is the order and wording the console shows.  Every
``history_type`` the History union query writes for a visible source is one
of these values; ``SERVICE_ERROR`` is written by the ``errors`` branch, which
is not a History source (see ``HISTORY_SOURCE_TABLES``), so it is not listed.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agent_cron.commands import SERVICE_COMMAND_OPTIONS


@dataclass(frozen=True)
class HistoryType:
    value: str
    label: str


QUEUE = "queue"
DINGTALK = "dingtalk"
CALENDAR = "calendar"
APPROVAL = "approval"
EMAIL = "email"
EMAIL_UNSUBSCRIBE = "email_unsubscribe"
EMAIL_ACTION = "email_action"
WECHAT = "wechat"
MEETING = "meeting"
TASK = "task"
SCHEDULED_AGENT = "scheduled_agent"
SCHEDULED_COMMAND = "scheduled_command"
SERVICE_ERROR = "service_error"

HISTORY_TYPES: tuple[HistoryType, ...] = (
    HistoryType(QUEUE, "队列"),
    HistoryType(DINGTALK, "钉钉消息"),
    HistoryType(CALENDAR, "日历邀请"),
    HistoryType(APPROVAL, "OA 审批"),
    HistoryType(EMAIL, "邮件"),
    HistoryType(EMAIL_UNSUBSCRIBE, "邮件退订"),
    HistoryType(EMAIL_ACTION, "邮件动作"),
    HistoryType(WECHAT, "微信"),
    HistoryType(MEETING, "会议跟进"),
    HistoryType(TASK, "Task"),
    HistoryType(SCHEDULED_AGENT, "定时任务"),
    HistoryType(SCHEDULED_COMMAND, "定时命令"),
)
HISTORY_TYPE_VALUES = frozenset(item.value for item in HISTORY_TYPES)

# The union branches the History page reads.  The startup cache warm-up and
# the History route must pass this same tuple: the page cache is keyed on it.
HISTORY_SOURCE_TABLES: tuple[str, ...] = (
    "reply_attempts",
    "meeting_alignment_runs",
    "work_updates",
    "todo_evidence_candidates",
    "follow_up_drafts",
    "work_todo_dingtalk_links",
    "scheduled_task_runs",
    "email_actions",
)

# How the calendar-invitation producer recognises an invitation card:
# ``DingTalkAutoReplyWorker._is_calendar_message`` (feature ``calendar_invite``,
# and the ``calendar_only`` split between ``calendar-invites-once`` and
# ``produce-once``).  An attempt keeps the trigger's content as
# ``trigger_text``; the provider's calendar ``message_type`` never arrives
# without these markers in the content (274 of 274 in production).  A test
# holds the two definitions together.
CALENDAR_CONTENT_PREFIX = "[日程]"
CALENDAR_CONTENT_MARKERS = ("newCalendar=1", "calendarDetail", "uniqueId=")
CALENDAR_ATTEMPT_ACTIONS = ("calendar_response", "calendar_reconciliation")


def _sql_text(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _encoded_markers() -> tuple[str, ...]:
    # The producer reads markers from the URL-decoded content as well.
    encoded: list[str] = []
    for marker in CALENDAR_CONTENT_MARKERS:
        encoded.append(marker)
        if "=" in marker:
            encoded.append(marker.replace("=", "%3D"))
            encoded.append(marker.replace("=", "%3d"))
    return tuple(encoded)


def calendar_attempt_sql(alias: str = "reply_attempts") -> str:
    """SQL predicate: this DingTalk attempt handled a calendar invitation."""
    text = f"{alias}.trigger_text"
    markers = " or ".join(
        f"instr({text}, {_sql_text(marker)})>0" for marker in _encoded_markers()
    )
    actions = ", ".join(_sql_text(action) for action in CALENDAR_ATTEMPT_ACTIONS)
    return (
        f"({alias}.action in ({actions})"
        f" or coalesce({alias}.calendar_event_id, '')<>''"
        f" or substr(ltrim({text}, ' ' || char(9, 10, 13)), 1, "
        f"{len(CALENDAR_CONTENT_PREFIX)})={_sql_text(CALENDAR_CONTENT_PREFIX)}"
        f" or {markers})"
    )


def reply_attempt_history_type_sql(alias: str = "reply_attempts") -> str:
    """The History type of one ``reply_attempts`` row."""
    return f"""case
                        when {alias}.action='oa_approval'
                             or {alias}.oa_process_instance_id<>'' then '{APPROVAL}'
                        when {alias}.channel='wechat' then '{WECHAT}'
                        when {alias}.channel='email' and (
                            {alias}.action='direct_unsubscribe'
                            or (
                                {alias}.conversation_title='Email unsubscribe'
                                and {alias}.trigger_text='Immutable ActionPlan authorizes unsubscribe.'
                            )
                        ) then '{EMAIL_UNSUBSCRIBE}'
                        when {alias}.channel='email' then '{EMAIL}'
                        when {alias}.channel='scheduled' then '{SCHEDULED_AGENT}'
                        when {calendar_attempt_sql(alias)} then '{CALENDAR}'
                        else '{DINGTALK}'
                    end"""


# A service command whose results are handed to an Agent (``produce-once``,
# ``calendar-invites-once`` ...) already appears in History once per item it
# queued; its successful runs, several thousand a day and nearly all of them
# "found nothing", are not shown.  A command whose work lands nowhere else
# (听记同步, 听记权限申请, OKR 周报) is shown on every run.  A failed trigger
# of any scheduled task is always shown.
SCHEDULED_COMMANDS_SHOWN_ON_SUCCESS: tuple[str, ...] = tuple(
    option.name
    for option in SERVICE_COMMAND_OPTIONS
    if not option.consumer_prompt_enabled
)
# A trigger skipped because the previous one was still running is the
# scheduler's overlap guard (~10,000 rows), not a result.  ``producer_completed``
# is the same kind of bookkeeping from before service commands existed.
# (``app.agent_cron.scheduler.PREVIOUS_EXECUTION_ACTIVE``; that module imports
# the store, so the value is repeated here and a test keeps them equal.)
SCHEDULED_ROUTINE_SKIP_REASONS: tuple[str, ...] = (
    "scheduled_task_previous_execution_active",
    "producer_completed",
)


def scheduled_run_filter_sql(alias: str = "runs") -> str:
    """SQL predicate: this scheduled trigger is a History row.

    Most of the ~85,000 triggers are successful or overlapping runs of the
    per-minute producers, so the skipped and successful terms are reached
    through the owning task (``idx_scheduled_task_runs_latest``), never by
    reading every dispatched or skipped trigger.  Only an Agent-form task
    skips for any reason but overlap (its runtime or Skills were
    unavailable); a service command is run in-process and does not.
    The unary ``+`` keeps SQLite from answering those two terms through the
    ``dispatch_status`` indexes, which would read all 74,000 dispatched rows.
    """
    commands = ", ".join(_sql_text(name) for name in SCHEDULED_COMMANDS_SHOWN_ON_SUCCESS)
    routine = " and ".join(
        f"{alias}.skip_or_error_reason not like {_sql_text(reason + '%')}"
        for reason in SCHEDULED_ROUTINE_SKIP_REASONS
    )
    return (
        f"({alias}.dispatch_status='failed'"
        f" or ({alias}.scheduled_task_id in"
        f" (select id from scheduled_tasks where command='')"
        f" and +{alias}.dispatch_status='skipped' and {routine})"
        f" or ({alias}.scheduled_task_id in"
        f" (select id from scheduled_tasks where command in ({commands}))"
        f" and +{alias}.dispatch_status='dispatched'"
        f" and {alias}.execution_kind='service_command'"
        f" and {alias}.execution_id in ({commands})))"
    )


def history_type_label(value: str) -> str:
    for item in HISTORY_TYPES:
        if item.value == value:
            return item.label
    return value
