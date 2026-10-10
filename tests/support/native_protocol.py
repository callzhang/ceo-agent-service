"""Persist scripted executor output in the native format used by readback."""
import json


def install_protocol_native_trajectories(tmp_path, monkeypatch, executor_class):
    original = executor_class.__call__
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    def native_path(session_id, **_kwargs):
        path = tmp_path / "native-protocol" / f"{session_id}.jsonl"
        return path if path.is_file() else None

    monkeypatch.setattr("app.codex_history.find_codex_session_path", native_path)
    monkeypatch.setattr("app.native_trajectory.find_codex_session_path", native_path)

    def execute(self, command, **kwargs):
        result = original(self, command, **kwargs)
        records = [json.loads(line) for line in result.stdout.splitlines()]
        session_id = records[0]["thread_id"]
        path = tmp_path / "native-protocol" / f"{session_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        native = [{"type": "event_msg", "payload": {"type": "task_started"}}]
        for record in records[1:]:
            if record.get("type") not in {"item.started", "item.completed"}:
                continue
            item = dict(record["item"])
            native_type = {
                "mcp_tool_call": "McpToolCall",
                "command_execution": "CommandExecution",
                "agent_message": "AgentMessage",
            }.get(item["type"])
            assert native_type is not None
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
