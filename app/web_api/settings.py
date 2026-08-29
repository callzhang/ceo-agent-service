"""Structured settings payload builders used by the React console."""

from app import config


def _duration(value) -> str:
    seconds = int(value.total_seconds())
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def _slash(values: tuple[str, ...]) -> str:
    return "/".join(values)


def info_sections() -> list[dict[str, object]]:
    mention = _slash(config.mention_aliases())
    broadcast = _slash(config.broadcast_mention_aliases())
    return [
        {
            "title": "快路径",
            "items": [
                {
                    "label": "入口",
                    "description": (
                        "每次 producer 运行都会调用 list_unread_conversations(count=50)。"
                        "快路径首次扫描到未读会话后，会读取未读消息并写入 reply_tasks/pending，"
                        f"但延迟 {_duration(config.fast_path_unread_backoff_duration())} 后才允许 consumer 领取；"
                        "慢路径未到点时，会过滤早于 message_fast_path_checked_at 的会话。"
                    ),
                },
                {
                    "label": "读取",
                    "description": (
                        "快路径首次触发时使用 read_unread_messages 取得可审计的 trigger。producer 也会调用 "
                        f"read_mentioned_messages 和广播 mention 查询，所以即使未读状态不完整，"
                        f"也能找到 {mention}、{broadcast} 这类点名或广播消息。"
                    ),
                },
                {
                    "label": "输出",
                    "description": (
                        "候选消息会经过过滤、按 seen_messages 去重、检查过期窗口；之后要么作为通知/系统消息跳过，"
                        "要么进入 reply_tasks。等待窗口结束时如果会话已不再未读，会记录 skipped；"
                        "仍未读则进入 processing。"
                    ),
                },
            ],
        },
        {
            "title": "慢路径",
            "items": [
                {"label": "周期", "description": f"每 {_duration(config.message_recovery_interval())} 运行一次。"},
                {
                    "label": "私聊恢复",
                    "description": (
                        "从本地 DB 加入最近 "
                        f"{_duration(config.single_chat_read_recovery_window())} 内的私聊会话，最多 "
                        f"{config.single_chat_read_recovery_limit()} 个。它会读取最近消息和未读消息，"
                        "再处理 latest seen message 之后的新消息。"
                    ),
                },
                {
                    "label": "群聊恢复",
                    "description": (
                        "慢路径不从本地 seen_messages 主动恢复群聊。群聊只通过 read_mentioned_messages、"
                        "广播 mention 查询，或当前未读会话中的明确点名进入候选。"
                    ),
                },
            ],
        },
        {
            "title": "群聊",
            "items": [
                {
                    "label": "触发",
                    "description": (
                        "群聊候选必须通过 addresses_principal：包含 "
                        f"{mention}，或包含 {broadcast} 这类广播别名。"
                        "没有这些点名信息的群聊消息，快路径和慢路径都不会处理。"
                    ),
                },
                {
                    "label": "文档",
                    "description": (
                        "群聊文档卡片只有先满足上面的群聊触发规则，才会进入 agent 判断。"
                        f"没有 {mention} 的普通群聊文档分享不会创建 reply task。"
                    ),
                },
                {
                    "label": "合并",
                    "description": (
                        "同一发送人的连续候选消息会先合并再入队，所以一个 reply_task 可以代表一小段相关群聊消息。"
                    ),
                },
            ],
        },
        {
            "title": "私聊",
            "items": [
                {
                    "label": "触发",
                    "description": (
                        f"私聊不要求 {mention}。经过未读/恢复选择和系统通知过滤后，最新一条剩余私聊消息会进入 agent 判断。"
                    ),
                },
                {
                    "label": "文档",
                    "description": (
                        "私聊文档会进入 agent 判断；不能因为文档卡片渲染成图片/链接卡片，就直接当作 no_reply。"
                    ),
                },
                {
                    "label": "系统过滤",
                    "description": (
                        "预过滤仍会跳过明确的系统/状态通知、本人消息、过期且已 seen 的消息，以及不可处理的渲染媒体。"
                        "日历、OA 审批、会议纪要权限消息会绕过通用通知跳过逻辑，进入各自的专门处理器。"
                    ),
                },
            ],
        },
    ]


def info_payload() -> dict[str, object]:
    return {
        "sections": info_sections(),
        "notes": [
            "Producer 负责发现候选消息，Consumer 负责执行 reply task；两者通过 SQLite 队列衔接。",
            "快路径与慢路径使用不同的恢复边界，避免重复读取和重复创建 reply task。",
            "页面展示的是当前配置下的运行规则；实际发送、审核和外部回执仍由后端业务链路负责。",
        ],
    }
