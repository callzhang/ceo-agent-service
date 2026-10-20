import json
import subprocess

import pytest

from app.repository_upgrade import GitRepository
from app.repository_release_notes import (
    ReleaseNotesError,
    release_notes,
    ReleaseSummaryAgent,
)


class State:
    def __init__(self):
        self.values = {}

    def get_service_state(self, key):
        return self.values.get(key)

    def set_service_state(self, key, value):
        self.values[key] = value


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def repo(tmp_path, count):
    git(tmp_path, "init", "--initial-branch=main")
    git(tmp_path, "config", "user.name", "Tester")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "commit", "--allow-empty", "-m", "baseline")
    start = git(tmp_path, "rev-parse", "HEAD")
    for n in range(count):
        git(
            tmp_path,
            "commit",
            "--allow-empty",
            "-m",
            f"change {n}",
            "-m",
            f"detail {n}\nsecond line {n}",
        )
    return GitRepository(tmp_path), start, git(tmp_path, "rev-parse", "HEAD")


def test_all_commits_and_bodies_reach_ai_without_twenty_commit_limit(tmp_path):
    repository, start, target = repo(tmp_path, 25)
    seen = []
    notes = release_notes(
        repository,
        State(),
        start,
        target,
        lambda commits: seen.extend(commits) or "修复队列并增加服务更新入口。",
    )
    assert len(notes["commits"]) == len(seen) == 25
    assert seen[0]["subject"] == "change 0"
    assert seen[-1]["body"] == "detail 24\nsecond line 24"
    assert notes["summary_source"] == "ai"
    assert notes["local_commit"] == start and notes["target_commit"] == target


def test_single_commit_uses_its_original_message_without_ai(tmp_path):
    repository, start, target = repo(tmp_path, 1)
    notes = release_notes(
        repository,
        State(),
        start,
        target,
        lambda _: pytest.fail("one commit called AI"),
    )
    assert notes["summary"] == "change 0\n\ndetail 0\nsecond line 0"
    assert notes["summary_source"] == "commit"


def test_successful_ai_summary_is_reused_only_for_exact_version_pair(tmp_path):
    repository, start, target = repo(tmp_path, 2)
    store = State()
    assert (
        release_notes(repository, store, start, target, lambda _: "更新说明")["summary"]
        == "更新说明"
    )
    assert (
        release_notes(
            repository, store, start, target, lambda _: pytest.fail("repeated AI")
        )["summary"]
        == "更新说明"
    )
    git(tmp_path, "commit", "--allow-empty", "-m", "another")
    newer = git(tmp_path, "rev-parse", "HEAD")
    assert (
        release_notes(repository, store, start, newer, lambda _: "新版本说明")[
            "summary"
        ]
        == "新版本说明"
    )


@pytest.mark.parametrize("summary", ["", "字" * 501])
def test_invalid_summary_is_not_cached_and_retry_can_succeed(tmp_path, summary):
    repository, start, target = repo(tmp_path, 2)
    store = State()
    with pytest.raises(ReleaseNotesError):
        release_notes(repository, store, start, target, lambda _: summary)
    assert (
        release_notes(repository, store, start, target, lambda _: "字" * 500)["summary"]
        == "字" * 500
    )


@pytest.mark.parametrize("background_error", [False, True])
def test_native_ai_receives_full_json_input_and_ephemeral_read_only_command(
    tmp_path, background_error
):
    from app.process_runner import ProcessRunResult

    seen = []

    class Runner:
        def build_command(self, **kwargs):
            assert not kwargs["preserve_native_model_config"]
            return ["codex", "exec", "--json", "-"]

        def build_env(self, **kwargs):
            return {}

    def execute(command, **kwargs):
        seen.append((command, kwargs["prompt"]))
        return ProcessRunResult(
            0,
            "\n".join(
                json.dumps(e)
                for e in [
                    {"type": "thread.started", "thread_id": "thread"},
                    *(
                        [
                            {
                                "type": "item.completed",
                                "item": {
                                    "type": "error",
                                    "message": "unrelated MCP startup failed",
                                },
                            }
                        ]
                        if background_error
                        else []
                    ),
                    {"type": "turn.started"},
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "agent_message",
                            "text": json.dumps(
                                {"summary": "更新说明"}, ensure_ascii=False
                            ),
                        },
                    },
                    {"type": "turn.completed"},
                ]
            ),
            "",
        )

    agent = ReleaseSummaryAgent(tmp_path, runner=Runner(), executor=execute)
    assert (
        agent([{"sha": "a" * 40, "subject": "改进", "body": "完整正文"}]) == "更新说明"
    )
    assert "--ephemeral" in seen[0][0] and "read-only" in seen[0][0]
    assert "完整正文" in seen[0][1]


def test_background_update_check_defaults_to_five_minutes(monkeypatch):
    from app.config import repository_upgrade_check_interval_seconds

    monkeypatch.delenv("CEO_REPOSITORY_UPGRADE_CHECK_INTERVAL_SECONDS", raising=False)
    assert repository_upgrade_check_interval_seconds() == 300
