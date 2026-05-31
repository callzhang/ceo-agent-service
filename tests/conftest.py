import os


os.environ["CEO_ENV_FILE"] = "/private/tmp/ceo-agent-service-test.env.missing"
os.environ["CEO_PRINCIPAL_NAME"] = "Alex"
os.environ["USER_ALIAS"] = "明哥"
os.environ["CEO_MENTION_ALIASES"] = "@Alex Chen,@明哥"
os.environ["DOCUMENT_EXTRACTION_IDS"] = "明哥,Alex"
os.environ["CEO_ASSISTANT_SIGNATURE"] = "（by明哥分身）"
os.environ["CEO_HANDOFF_ACK"] = "我让明哥本人看一下。（by明哥分身）"
os.environ["CEO_PROMPT_VAR_RESPONSIBILITY_SUMMARY"] = (
    "Alex 的组织职责包括算法负责人；凡是询问算法团队、算法同学、算法分享、算法资源或算法方向是否参与的消息，"
    "如果明确 @ Alex，即使同时 @ 了别人，也应视为需要 Alex 回复。"
)
os.environ["CEO_DING_ROBOT_NAME"] = "极简云机器人"
os.environ["CEO_FORBIDDEN_PATH_PREFIXES"] = "/Users/principal/,/home/principal/"
os.environ["FAST_PATH_UNREAD_BACKOFF"] = "0s"
