from datetime import datetime
from pathlib import Path

from app.dws_client import DwsOaApprovalCandidate
from app.store import AutoReplyStore
from app.task_scanners import scan_pending_oa_approvals


class _PendingApprovalDws:
    def list_pending_oa_approvals(self, *, page, size, start, end):
        return [DwsOaApprovalCandidate(process_instance_id="proc-1", title="付款")]

    def get_current_user_id(self):
        return "reviewer-1"

    def read_oa_approval_tasks(self, process_instance_id):
        return {"result": {"tasks": [{"taskId": "task-1", "status": "RUNNING"}]}}

    def read_oa_process_instance_openapi(self, process_instance_id):
        return {
            "result": {
                "tasks": [
                    {"taskId": "task-1", "userId": "reviewer-1", "status": "RUNNING"}
                ]
            }
        }

    def read_oa_approval_records(self, process_instance_id):
        return {"result": {"operationRecords": []}}


def test_oa_prompt_requires_normative_material_referenced_by_selected_skill(tmp_path):
    store = AutoReplyStore(tmp_path / "oa.sqlite3")

    assert (
        scan_pending_oa_approvals(
            store,
            _PendingApprovalDws(),
            now=datetime.fromisoformat("2026-10-16T12:00:00+08:00"),
        )
        == 1
    )

    [task] = store.claim_reply_tasks(limit=1)
    assert "财务模板（processCode 在财务 Skill 登记表内）以其规则卡为判断标准" in task.trigger_text
    assert "所选 Skill 明确引用的规则卡、登记表等规范材料必须读取" in task.trigger_text
    assert "未被适用 Skill 引用的背景材料可以跳过" in task.trigger_text
    assert "规则只在 Skill 里，不读参考文档" not in task.trigger_text

    skill = (
        Path(__file__).parents[1]
        / "ci/shared-skills/dingtalk-oa-approval/SKILL.md"
    ).read_text()
    assert "Skill 明确指定为判断依据的规则卡、登记表或外部制度属于规范来源，必须读取并核验" in skill
    assert "若当前 prompt 或适用 Skill 明确指定某个外部规则来源，则应读取该指定来源" in skill
