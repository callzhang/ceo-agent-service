import sqlite3

from app.codex_dev import (
    CodexDevCompletionNotifier,
    _summarize_codex_dev_output,
    build_codex_dev_task_prompt,
    build_codex_dev_completion_message,
    process_codex_dev_tasks,
)
from app.store import AutoReplyStore


class FakeDevRunner:
    last_session_id = "session-dev-1"
    last_transcript_start_line = 0
    last_transcript_end_line = 12
    last_audit_tool_events = [{"tool": "exec_command", "command": "pytest"}]

    def __init__(self):
        self.tasks = []

    def execute(self, task):
        self.tasks.append(task)
        return "implemented\n1172 passed"


class FakeCompletionNotifier:
    def __init__(self):
        self.sent = []

    def notify_done(self, task, result_summary):
        self.sent.append((task, result_summary))


class FakeDws:
    def __init__(self):
        self.sent_messages = []
        self.dings = []
        self.ding_error = None
        self.send_error = None
        self.user_profiles = {
            "principal-user-1": type(
                "Profile",
                (),
                {"open_dingtalk_id": "open-principal-1", "name": "Mina"},
            )()
        }

    def get_user_profile(self, user_id):
        return self.user_profiles[user_id]

    def send_message(
        self,
        conversation_id,
        text,
        at_open_dingtalk_ids=None,
        at_open_dingtalk_names=None,
    ):
        if self.send_error:
            raise self.send_error
        self.sent_messages.append(
            (
                conversation_id,
                text,
                at_open_dingtalk_ids or [],
                at_open_dingtalk_names or [],
            )
        )
        return {"ok": True}

    def ding_user(self, user_id, text):
        if self.ding_error:
            raise self.ding_error
        self.dings.append((user_id, text))


def test_build_codex_dev_task_prompt_includes_safety_boundary(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = store.enqueue_codex_dev_task(
        conversation_id="cid-1",
        conversation_title="HR管理",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_sender_user_id="principal-user-1",
        trigger_text="Mina Agent，用codex执行这个任务。开发心跳功能",
        instruction="开发心跳功能",
    )
    task = store.claim_codex_dev_tasks(limit=1)[0]
    assert task.id == task_id

    prompt = build_codex_dev_task_prompt(task)

    assert "Execute this development task now" in prompt
    assert "开发心跳功能" in prompt
    assert "Do not push, deploy, create a PR, restart launchd" in prompt
    assert "Only send DingTalk messages to the originating DingTalk conversation" in prompt
    assert "originating DingTalk conversation" in prompt
    assert "Context enrichment" in prompt
    assert "memory_recall" in prompt
    assert "weekly management meetings" in prompt
    assert "raw clickable URL" in prompt
    assert "https://alidocs.dingtalk.com/" in prompt
    assert "explicit target DingTalk URL" in prompt
    assert "local file with a similar title is not completion" in prompt


def test_process_codex_dev_tasks_marks_done(tmp_path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    task_id = store.enqueue_codex_dev_task(
        conversation_id="cid-1",
        conversation_title="HR管理",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_sender_user_id="principal-user-1",
        trigger_text="Mina Agent，用codex执行这个任务。开发心跳功能",
        instruction="开发心跳功能",
    )
    runner = FakeDevRunner()

    processed = process_codex_dev_tasks(store, runner)

    assert processed == 1
    assert [task.id for task in runner.tasks] == [task_id]
    with sqlite3.connect(db_path) as db:
        row = db.execute(
            """
            select status,result_summary,codex_session_id,audit_tool_events_json
            from codex_dev_tasks
            where id=?
            """,
            (task_id,),
        ).fetchone()
    assert row[0] == "done"
    assert "1172 passed" in row[1]
    assert row[2] == "session-dev-1"
    assert "exec_command" in row[3]


def test_summarize_codex_dev_output_uses_latest_completed_agent_message():
    raw = "\n".join(
        [
            '{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"我先验证一下。"}}',
            '{"type":"item.started","item":{"id":"item_2","type":"command_execution","command":"git status","aggregated_output":"","exit_code":null,"status":"in_progress"}}',
            '{"type":"item.completed","item":{"id":"item_2","type":"command_execution","command":"git status","aggregated_output":"fatal: not a git repository","exit_code":0,"status":"completed"}}',
            '{"type":"item.completed","item":{"id":"item_3","type":"agent_message","text":"已完成：目标文档已更新。\\n文档链接：\\nhttps://alidocs.dingtalk.com/i/nodes/doc-ok"}}',
        ]
    )

    summary = _summarize_codex_dev_output(raw)

    assert summary == (
        "已完成：目标文档已更新。\n"
        "文档链接：\n"
        "https://alidocs.dingtalk.com/i/nodes/doc-ok"
    )
    assert "command_execution" not in summary
    assert "git status" not in summary


def test_process_codex_dev_tasks_notifies_original_conversation_when_done(tmp_path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    store.enqueue_codex_dev_task(
        conversation_id="cid-q3",
        conversation_title="Q3战略讨论",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_sender_user_id="principal-user-1",
        trigger_text="Mina agent, 用codex执行这个任务，形成会议管理办法文件",
        instruction="形成会议管理办法文件",
    )
    runner = FakeDevRunner()
    notifier = FakeCompletionNotifier()

    processed = process_codex_dev_tasks(store, runner, completion_notifier=notifier)

    assert processed == 1
    assert len(notifier.sent) == 1
    task, result_summary = notifier.sent[0]
    assert task.conversation_id == "cid-q3"
    assert task.conversation_title == "Q3战略讨论"
    assert "1172 passed" in result_summary


def test_completion_notifier_sends_done_message_with_result_link(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_codex_dev_task(
        conversation_id="cid-q3",
        conversation_title="Q3战略讨论",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_sender_user_id="principal-user-1",
        trigger_text="Mina agent, 用codex执行这个任务，形成会议管理办法文件",
        instruction="形成会议管理办法文件",
    )
    task = store.claim_codex_dev_tasks(limit=1)[0]
    dws = FakeDws()
    notifier = CodexDevCompletionNotifier(dws)

    notifier.notify_done(
        task,
        "已创建钉钉文档：https://alidocs.dingtalk.com/i/nodes/doc-1",
    )

    assert len(dws.sent_messages) == 1
    conversation_id, text, at_open_dingtalk_ids, at_open_dingtalk_names = (
        dws.sent_messages[0]
    )
    assert conversation_id == "cid-q3"
    assert at_open_dingtalk_ids == ["open-principal-1"]
    assert at_open_dingtalk_names == ["Mina"]
    assert "已执行完毕" in text
    assert "形成会议管理办法文件" in text
    assert "https://alidocs.dingtalk.com/i/nodes/doc-1" in text
    assert "（by Mina Agent）" in text
    assert dws.dings == [
        (
            "principal-user-1",
            "Mina Agent 已执行完毕，请回原对话查看：形成会议管理办法文件",
        )
    ]


def test_completion_notifier_does_not_ding_when_send_message_fails(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_codex_dev_task(
        conversation_id="cid-q3",
        conversation_title="Q3战略讨论",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_sender_user_id="principal-user-1",
        trigger_text="Mina agent, 用codex执行这个任务，形成会议管理办法文件",
        instruction="形成会议管理办法文件",
    )
    task = store.claim_codex_dev_tasks(limit=1)[0]
    dws = FakeDws()
    dws.send_error = RuntimeError("send failed")
    notifier = CodexDevCompletionNotifier(dws)

    try:
        notifier.notify_done(task, "完成")
    except RuntimeError:
        pass

    assert dws.dings == []


def test_completion_notifier_ignores_ding_failure_after_message_sent(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_codex_dev_task(
        conversation_id="cid-q3",
        conversation_title="Q3战略讨论",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_sender_user_id="principal-user-1",
        trigger_text="Mina agent, 用codex执行这个任务，形成会议管理办法文件",
        instruction="形成会议管理办法文件",
    )
    task = store.claim_codex_dev_tasks(limit=1)[0]
    dws = FakeDws()
    dws.ding_error = RuntimeError("ding failed")
    notifier = CodexDevCompletionNotifier(dws)

    notifier.notify_done(task, "完成")

    assert len(dws.sent_messages) == 1


def test_process_codex_dev_tasks_summarizes_final_answer_from_jsonl(tmp_path):
    class JsonlRunner(FakeDevRunner):
        def execute(self, task):
            self.tasks.append(task)
            return "\n".join(
                [
                    '{"type":"item.completed","item":{"type":"command_execution","aggregated_output":"SECRET RAW TOOL OUTPUT","status":"completed"}}',
                    '{"type":"response_item","payload":{"type":"message","role":"assistant","phase":"final_answer","content":[{"type":"output_text","text":"完成。已生成报告：\\n\\n[q2-leadership-360-report.md](/Users/mina/Documents/memory/q2-leadership-360-report.md)\\n\\n测试：无代码测试。"}]}}',
                    '{"type":"event_msg","payload":{"type":"task_complete","last_agent_message":"完成。已生成报告：\\n\\n[q2-leadership-360-report.md](/Users/mina/Documents/memory/q2-leadership-360-report.md)\\n\\n测试：无代码测试。"}}',
                ]
            )

    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    task_id = store.enqueue_codex_dev_task(
        conversation_id="cid-1",
        conversation_title="HR管理",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_sender_user_id="principal-user-1",
        trigger_text="Mina Agent，用codex执行这个任务。生成报告",
        instruction="生成报告",
    )

    processed = process_codex_dev_tasks(store, JsonlRunner())

    assert processed == 1
    with sqlite3.connect(db_path) as db:
        row = db.execute(
            "select result_summary from codex_dev_tasks where id=?",
            (task_id,),
        ).fetchone()
    assert "完成。已生成报告" in row[0]
    assert "q2-leadership-360-report.md" in row[0]
    assert "SECRET RAW TOOL OUTPUT" not in row[0]
    assert "aggregated_output" not in row[0]


def test_completion_message_lists_local_files_without_fake_result_links(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_codex_dev_task(
        conversation_id="cid-q3",
        conversation_title="Q3战略讨论",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_sender_user_id="principal-user-1",
        trigger_text="Mina agent, 用codex执行这个任务，形成报告",
        instruction="形成报告",
    )
    task = store.claim_codex_dev_tasks(limit=1)[0]

    message = build_codex_dev_completion_message(
        task,
        "完成。已生成报告：\n\n"
        "[q2-leadership-360-report.md](/Users/mina/Documents/memory/q2-leadership-360-report.md)\n\n"
        "另见 https://alidocs.dingtalk.com/i/nodes/doc-1",
    )

    assert "结果链接：" in message
    assert "- https://alidocs.dingtalk.com/i/nodes/doc-1" in message
    assert "本地文件：" in message
    assert "- q2-leadership-360-report.md：/Users/mina/Documents/memory/q2-leadership-360-report.md" in message
    assert "结果链接：\n- /Users/mina/Documents/memory/q2-leadership-360-report.md" not in message


def test_completion_message_does_not_inline_report_body_or_link_plain_titles(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_codex_dev_task(
        conversation_id="cid-q3",
        conversation_title="Q3战略讨论",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_sender_user_id="principal-user-1",
        trigger_text="Mina agent, 用codex执行这个任务，形成报告",
        instruction="形成报告",
    )
    task = store.claim_codex_dev_tasks(limit=1)[0]

    message = build_codex_dev_completion_message(
        task,
        "完成。已生成钉钉文档：领导力 360 评估 2026 Q2 末报告.adoc\n\n"
        "## 五、个人观察\n\n"
        "| 被评价人 | Q2 记录数 | Q2 平均分 |\n"
        "|---|---:|---:|\n"
        "| 胡明 | 1 | 10.00 |\n"
        "| 韩露 | 3 | 9.00 |\n\n"
        "重点解读：\n"
        "- 邹婧玮：Q2 得分高且样本达到 3 条，反馈集中在组织前瞻性。\n"
        "- 刘兴祖、王靖、韩露：Q2 相比 Q1 均有提升。",
    )

    assert "结果：" in message
    assert "报告已生成，但未返回可点击链接" in message
    assert "| 胡明 |" not in message
    assert "重点解读：" not in message
    assert "结果链接：" not in message
    assert "领导力 360 评估 2026 Q2 末报告.adoc" in message


def test_completion_message_with_links_omits_result_summary_and_tool_output(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_codex_dev_task(
        conversation_id="cid-q3",
        conversation_title="Q3战略讨论",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_sender_user_id="principal-user-1",
        trigger_text="Mina agent, 用codex执行这个任务，形成PPT",
        instruction="形成PPT",
    )
    task = store.claim_codex_dev_tasks(limit=1)[0]

    message = build_codex_dev_completion_message(
        task,
        'completed","item":{"type":"command_execution","command":"/bin/zsh -lc '
        '\'dws drive upload --file /Users/mina/Documents/memory/outputs/report.pptx\'",'
        '"aggregated_output":"[1/3] 获取上传凭证...","result":{"docUrl":'
        '"[领导力 360 评估 2026Q2末-全员共识与管理行动计划.pptx]"'
        "(https://alidocs.dingtalk.com/i/nodes/7QG4Yx2JpL9XZNEElQxp4xoGJ9dEq3XD)\"}}",
    )

    assert "结果：" not in message
    assert "结果链接：" in message
    assert "https://alidocs.dingtalk.com/i/nodes/7QG4Yx2JpL9XZNEElQxp4xoGJ9dEq3XD" in message
    assert "command_execution" not in message
    assert "aggregated_output" not in message
    assert "dws drive upload" not in message


def test_completion_message_filters_non_user_facing_and_placeholder_links(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_codex_dev_task(
        conversation_id="cid-q3",
        conversation_title="Q3战略讨论",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_sender_user_id="principal-user-1",
        trigger_text="Mina agent, 用codex执行这个任务，修改PPT",
        instruction="修改PPT",
    )
    task = store.claim_codex_dev_tasks(limit=1)[0]

    message = build_codex_dev_completion_message(
        task,
        "完成。内部 namespace: "
        "http://schemas.openxmlformats.org/presentationml/2006/main "
        "http://schemas.openxmlformats.org/drawingml/2006/main "
        "http://schemas.microsoft.com/office/powerpoint/2010/main "
        "首页不是结果：https://alidocs.dingtalk.com/ https://docs.dingtalk.com/ "
        "提示示例：https://alidocs.dingtalk.com/...` "
        "https://docs.dingtalk.com/...` "
        "真实结果：https://alidocs.dingtalk.com/i/nodes/realDoc123",
    )

    assert "结果链接：" in message
    assert "- https://alidocs.dingtalk.com/i/nodes/realDoc123" in message
    assert "schemas.openxmlformats.org" not in message
    assert "schemas.microsoft.com" not in message
    assert "- https://alidocs.dingtalk.com/" not in message.splitlines()
    assert "- https://docs.dingtalk.com/" not in message.splitlines()
    assert "https://alidocs.dingtalk.com/..." not in message
    assert "https://docs.dingtalk.com/..." not in message


def test_completion_message_prefers_document_links_after_escaped_newlines(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_codex_dev_task(
        conversation_id="cid-q2",
        conversation_title="Mina 邹",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_sender_user_id="principal-user-1",
        trigger_text="Mina Agent，用codex执行这个任务",
        instruction="生成每位领导的个人反馈文档",
    )
    task = store.claim_codex_dev_tasks(limit=1)[0]

    message = build_codex_dev_completion_message(
        task,
        "完成：已生成 12 份文档。\\n\\n"
        "文件夹：\\n"
        "https://alidocs.dingtalk.com/i/nodes/folderBad?utm_scene=team_space\\n\\n"
        "文档链接：\\n"
        "https://alidocs.dingtalk.com/i/nodes/docGood1\\n"
        "https://alidocs.dingtalk.com/i/nodes/docGood2\\n\\n"
        "Files changed/generated locally:\\n"
        "`outputs/q2-leadership-360-personal/*.md`",
    )

    assert "目录链接：" in message
    assert "- https://alidocs.dingtalk.com/i/nodes/folderBad?utm_scene=team_space" in message
    assert "文档链接：" in message
    assert "- https://alidocs.dingtalk.com/i/nodes/docGood1" in message
    assert "- https://alidocs.dingtalk.com/i/nodes/docGood2" in message
    assert "folderBad?utm_scene=team_space\\n" not in message
    assert "\\n" not in message


def test_completion_message_prefers_artifact_url_over_context_source_links(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_codex_dev_task(
        conversation_id="cid-q3",
        conversation_title="Mina 邹",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_sender_user_id="principal-user-1",
        trigger_text="Mina Agent，用codex执行这个任务",
        instruction="创建管理周会模板",
    )
    task = store.claim_codex_dev_tasks(limit=1)[0]

    message = build_codex_dev_completion_message(
        task,
        "Conclusion: created the new DingTalk document `管理层周会模板`.\n\n"
        "Artifact URL: https://alidocs.dingtalk.com/i/nodes/newTemplate123\n\n"
        "Context sources used: source DingTalk doc `公司会议提效机制` at "
        "https://alidocs.dingtalk.com/i/nodes/sourceDoc456.",
    )

    assert "结果链接：" in message
    assert "- https://alidocs.dingtalk.com/i/nodes/newTemplate123" in message
    assert "sourceDoc456" not in message


def test_process_codex_dev_tasks_augments_summary_with_created_doc_url_from_audit_events(
    tmp_path,
):
    class RunnerWithToolDocUrl(FakeDevRunner):
        last_audit_tool_events = [
            {
                "tool": "exec_command",
                "command": (
                    'dws doc create --name "管理层周会模板" '
                    "--content-file management-weekly-template.md --format json"
                ),
            },
            {
                "tool": "tool_output",
                "output": (
                    "Output:\n"
                    '{\n'
                    '  "docUrl": "https://alidocs.dingtalk.com/i/nodes/newTemplate123",\n'
                    '  "name": "管理层周会模板",\n'
                    '  "success": true\n'
                    "}\n"
                ),
            },
        ]

        def execute(self, task):
            self.tasks.append(task)
            return "\n".join(
                [
                    (
                        '{"type":"item.completed","item":{"type":"agent_message",'
                        '"text":"已完成。\\n结果链接：\\n• 管理层周会模板.adoc'
                        '\\n• 公司会议提效机制.adoc"}}'
                    ),
                ]
            )

    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    store.enqueue_codex_dev_task(
        conversation_id="cid-q3",
        conversation_title="Mina 邹",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_sender_user_id="principal-user-1",
        trigger_text="Mina Agent，用codex执行这个任务。创建管理周会模板",
        instruction="创建管理周会模板",
    )
    runner = RunnerWithToolDocUrl()
    notifier = FakeCompletionNotifier()

    processed = process_codex_dev_tasks(store, runner, completion_notifier=notifier)

    assert processed == 1
    assert len(notifier.sent) == 1
    _task, result_summary = notifier.sent[0]
    assert "https://alidocs.dingtalk.com/i/nodes/newTemplate123" in result_summary


def test_process_codex_dev_tasks_marks_failed(tmp_path):
    class FailingRunner(FakeDevRunner):
        def execute(self, task):
            raise RuntimeError("tests failed")

    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    task_id = store.enqueue_codex_dev_task(
        conversation_id="cid-1",
        conversation_title="HR管理",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_sender_user_id="principal-user-1",
        trigger_text="Mina Agent，用codex执行这个任务。开发心跳功能",
        instruction="开发心跳功能",
    )

    processed = process_codex_dev_tasks(store, FailingRunner())

    assert processed == 0
    with sqlite3.connect(db_path) as db:
        row = db.execute(
            "select status,error from codex_dev_tasks where id=?",
            (task_id,),
        ).fetchone()
        error = db.execute(
            "select kind,detail from errors order by id desc limit 1"
        ).fetchone()
    assert row == ("failed", "tests failed")
    assert error == ("codex_dev_task", "tests failed")
