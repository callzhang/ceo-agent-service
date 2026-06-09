import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class UserPromptBlock:
    name: str
    expression: str
    description: str
    default: str


_BLOCKS: ContextVar[dict[str, str] | None] = ContextVar(
    "user_prompt_blocks",
    default=None,
)


USER_PROMPT_BLOCKS = [
    UserPromptBlock(
        name="style_lines",
        expression="app.user_prompt_blocks:style_lines()",
        description="相似历史回复风格例子等动态风格上下文。",
        default=(
            "相似历史回复风格例子（只学习语气、判断顺序和句式结构；"
            "不要复用例子里的事实、人名、项目名、客户名、数字或结论；不要引用这些例子）:\n"
            "- 例1: 先定优先级，再确认谁负责、什么时候交付、怎么验收。\n"
            "- 例2: 先把风险拆成产品、算法和交付三类，每类只留一个负责人和一个截止时间。"
        ),
    ),
    UserPromptBlock(
        name="current_message_block",
        expression="app.user_prompt_blocks:current_message_block()",
        description="当前待处理的新消息、会话名和会话类型。",
        default=(
            "当前待处理消息:\n"
            "会话: 示例群\n"
            "会话类型: 群聊\n"
            "新消息:\n"
            "- Mina sender_user_id=sender-user-1 2026-05-29 09:00:00: "
            "@CEO 看下这个问题"
        ),
    ),
    UserPromptBlock(
        name="sender_org_block",
        expression="app.user_prompt_blocks:sender_org_block()",
        description="发信人组织信息 JSON；没有可用组织信息时为空。",
        default='发信人组织信息(JSON):\n{"name": "Mina", "user_id": "sender-user-1"}',
    ),
    UserPromptBlock(
        name="known_people_block",
        expression="app.user_prompt_blocks:known_people_block()",
        description="可用组织人员标识；没有相关人员时为空。",
        default=(
            "可用组织人员标识（如内部人员问题对象匹配这些人，"
            "personnel_subject_user_id 必须使用对应 user_id）:\n"
            "- 张晓民: user_id=subject-user-1"
        ),
    ),
    UserPromptBlock(
        name="context_messages_block",
        expression="app.user_prompt_blocks:context_messages_block()",
        description="自上次回复后的上下文消息，最多 20 条。",
        default=(
            "上下文消息（自上次回复后的新信息，最多 20 条）:\n"
            + json.dumps(
                [
                    {
                        "open_message_id": "ctx-1",
                        "create_time": "2026-05-29 08:59:00",
                        "sender": {
                            "name": "Mina",
                            "user_id": "sender-user-1",
                            "open_dingtalk_id": "open-sender-1",
                        },
                        "message_type": "text",
                        "content": "上文背景",
                        "mentioned_user_ids": ["principal-user-1"],
                        "quoted": {
                            "open_message_id": "quoted-1",
                            "content": "引用背景",
                        },
                    }
                ],
                ensure_ascii=False,
                indent=2,
            )
        ),
    ),
    UserPromptBlock(
        name="material_references_block",
        expression="app.user_prompt_blocks:material_references_block()",
        description="待 agent 判断是否读取的钉钉文档、AI 听记或普通文件引用；没有材料时为空。",
        default=(
            "待读取材料（由 agent 判断是否读取）:\n"
            "如果判断依赖材料正文，必须先读取材料；如果消息正文已经足够，可以不读取。"
            "读取失败时不要臆测材料内容，应说明权限或材料问题。\n"
            "DWS 读取命令提示:\n"
            "- 钉钉文档: dws doc info --node <URL> --format json；"
            "需要正文时 dws doc read --node <URL> --format json\n"
            "- AI 听记: dws minutes get info --id <MINUTES_ID> --format json\n"
            "- 普通文件: 先用消息中的文件名和上下文判断是否需要读取；"
            "需要时使用 DWS 文件/云盘能力查询或下载。\n"
            "- 材料1:\n"
            "  类型: dingtalk_doc\n"
            "  引用: https://alidocs.dingtalk.com/i/nodes/example\n"
            "  来源消息: msg-1\n"
            "  发送人: Mina\n"
            "  时间: 2026-06-08 18:46:32"
        ),
    ),
    UserPromptBlock(
        name="linked_documents_block",
        expression="app.user_prompt_blocks:linked_documents_block()",
        description="已读取的钉钉文档、普通文件正文或摘要；没有材料时为空。",
        default=(
            "已获取的钉钉材料:\n"
            "材料 1: 示例文档\n"
            "URL: https://alidocs.dingtalk.com/i/nodes/example\n"
            "正文:\n示例正文"
        ),
    ),
    UserPromptBlock(
        name="image_download_block",
        expression="app.user_prompt_blocks:image_download_block()",
        description="图片下载失败状态；图片都成功下载或没有图片时为空。",
        default=(
            "图片读取状态:\n"
            "以下图片未能下载。如果当前问题依赖图片内容，不能臆测图片细节；"
            "应说明图片读取失败并追问可查看版本。如果当前问题可基于文字上下文独立处理，可以继续处理。\n"
            "- msg-1: resource @img error unsupported resourceType: image"
        ),
    ),
]

_DEFAULTS = {block.name: block.default for block in USER_PROMPT_BLOCKS}


@contextmanager
def user_prompt_block_context(blocks: dict[str, str]) -> Iterator[None]:
    token = _BLOCKS.set(blocks)
    try:
        yield
    finally:
        _BLOCKS.reset(token)


def _block(name: str) -> str:
    blocks = _BLOCKS.get()
    if blocks is not None:
        return blocks.get(name, "")
    return _DEFAULTS[name]


def style_lines() -> str:
    return _block("style_lines")


def current_message_block() -> str:
    return _block("current_message_block")


def sender_org_block() -> str:
    return _block("sender_org_block")


def known_people_block() -> str:
    return _block("known_people_block")


def linked_documents_block() -> str:
    return _block("linked_documents_block")


def material_references_block() -> str:
    return _block("material_references_block")


def image_download_block() -> str:
    return _block("image_download_block")


def context_messages_block() -> str:
    return _block("context_messages_block")
