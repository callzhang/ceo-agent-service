from typing import Literal

from pydantic import BaseModel


class HistoryItem(BaseModel):
    kind: Literal["reply", "meeting", "task"]
    source_id: int
    source_title: str
    source_actor: str
    input_label: str
    input_text: str
    output_label: str
    output_text: str
    action: str
    status: str
    target_title: str = ""
    codex_session_id: str = ""
    project_id: int = 0
    todo_id: int = 0
    follow_up_id: int = 0
    channel: str = "dingtalk"
    created_at: str
