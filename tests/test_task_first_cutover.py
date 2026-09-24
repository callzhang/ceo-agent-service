from pathlib import Path


def test_active_task_path_has_no_project_first_writes_or_patch_schema():
    root = Path(__file__).resolve().parents[1]
    active_sources = [
        (root / "app/task_models.py").read_text(encoding="utf-8"),
        (root / "app/task_agent.py").read_text(encoding="utf-8"),
        (root / "app/task_retrieval.py").read_text(encoding="utf-8"),
        (root / "app/web_api/tasks.py").read_text(encoding="utf-8"),
        (root / "app/web_api/registration.py").read_text(encoding="utf-8"),
    ]
    joined = "\n".join(active_sources)

    assert "TaskProjectPatch" not in joined
    assert '"create_project"' not in joined
    assert '"update_project"' not in joined
    assert "create_work_project(" not in joined
    assert "create_work_todo(" not in joined
