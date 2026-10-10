"""Persist scripted executor output in the native format used by readback."""
import json
from uuid import uuid4


def install_protocol_native_trajectories(tmp_path, monkeypatch, executor_class, *, allow_nonprotocol_output=False):
    original = executor_class.__call__
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    paths = {}

    def native_path(session_id, **_kwargs):
        return paths.get(session_id)

    monkeypatch.setattr("app.codex_history.find_codex_session_path", native_path)
    monkeypatch.setattr("app.native_trajectory.find_codex_session_path", native_path)

    def execute(self, command, **kwargs):
        result = original(self, command, **kwargs)
        records = []
        for line in result.stdout.splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                if not allow_nonprotocol_output:
                    raise
                continue
            if isinstance(record, dict):
                records.append(record)
            elif not allow_nonprotocol_output:
                raise ValueError("scripted protocol record must be an object")
        if allow_nonprotocol_output:
            sessions = [record.get("thread_id") for record in records
                        if record.get("type") == "thread.started" and isinstance(record.get("thread_id"), str)
                        and record["thread_id"].strip()]
            if not sessions:
                return result
            session_id = sessions[-1]
        else:
            session_id = records[0]["thread_id"]
        path = paths.setdefault(session_id, tmp_path / "native-protocol" / f"{uuid4()}.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        native = [{"type": "event_msg", "payload": {"type": "task_started"}}]
        for record in records:
            record_type = record.get("type")
            if not isinstance(record_type, str):
                if allow_nonprotocol_output:
                    continue
                raise ValueError("scripted protocol event type must be a string")
            if record_type not in {"item.started", "item.completed"}:
                continue
            if not isinstance(record.get("item"), dict):
                if allow_nonprotocol_output:
                    continue
                raise ValueError("scripted native item must be an object")
            item = dict(record["item"])
            item_type = item.get("type")
            native_type = {
                "mcp_tool_call": "McpToolCall",
                "command_execution": "CommandExecution",
                "agent_message": "AgentMessage",
            }.get(item_type) if isinstance(item_type, str) else None
            if native_type is None:
                if allow_nonprotocol_output:
                    continue
                raise ValueError("unsupported scripted native item")
            item["type"] = native_type
            native.append({"type": "event_msg", "payload": {
                "type": record["type"].replace(".", "_"), "item": item,
            }})
        native.append({"type": "event_msg", "payload": {"type": "task_complete"}})
        with path.open("a", encoding="utf-8") as stream:
            for record in native:
                stream.write(json.dumps(record) + "\n")
        return result

    monkeypatch.setattr(executor_class, "__call__", execute)
