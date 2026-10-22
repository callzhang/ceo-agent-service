from types import SimpleNamespace

from app.agent_runtime_config import load_runtime_config
from app.agent_runtime_router import CodexCommandFactory
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.email_unsubscribe_page_judge import build_page_judge, build_prompt


def test_page_judge_excludes_native_project_document_without_changing_page_input():
    calls = []

    def execute(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(value='{"state":"done","evidence":"You are unsubscribed."}')

    build_page_judge(SimpleNamespace(execute=execute))(
        "synthetic-action", "example.test", "You are unsubscribed.", []
    )

    call = calls[0]
    assert call["prompt"] == build_prompt("example.test", "You are unsubscribed.", [])
    assert call["command_factory"].skill_names == ()
    assert call["command_factory"].project_doc_max_bytes == 0


def test_page_judge_limit_reaches_native_command_only_when_requested(tmp_path):
    config = load_runtime_config({"CEO_AGENT_RUNTIME_ROUTES": "codex_oauth"})
    adapter = CodexRuntimeAdapter(tmp_path, config, codex_bin="codex-test")
    route = config.routes[0]
    def command_for(limit):
        factory = CodexCommandFactory.standard(
            developer_instructions="Judge this synthetic page.",
            skill_names=(),
            project_doc_max_bytes=limit,
        )
        return factory.build(
            adapter=adapter, route=route, prompt="Page text", session_id=None,
        )[0]

    baseline = command_for(None)
    candidate = command_for(0)
    assert "project_doc_max_bytes=0" not in baseline
    assert any(
        candidate[index:index + 2] == ["-c", "project_doc_max_bytes=0"]
        for index in range(len(candidate) - 1)
    )
    assert candidate.count("project_doc_max_bytes=0") == 1
