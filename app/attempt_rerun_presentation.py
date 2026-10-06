"""Read-only wording for manual reevaluation, without changing retry eligibility."""

import json
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class RerunPresentation:
    label: str = "重新处理"
    confirmation: str = "确认重新处理这条 Attempt？"
    explanation: str = ""


def attempt_rerun_presentation(
    store: Any, reply_task: Any, agent_runs: Iterable[Any] = (),
) -> RerunPresentation:
    if store is not None and reply_task is not None:
        # Match the existing System guard's business-object and generation scope.
        # Select only diagnostic JSON; reading a page never changes that guard.
        with store._connect() as db:
            errors = [row[0] for row in db.execute(
                "select r.structured_error_json from agent_runs r "
                "join reply_tasks t on t.id=r.reply_task_id "
                "where t.business_object_key=? and r.structured_error_json<>''",
                (reply_task.business_object_key,),
            )]
    else:
        errors = [run.structured_error_json for run in agent_runs]
    for raw in errors:
        try:
            error = json.loads(raw or "{}")
        except json.JSONDecodeError:
            continue
        if isinstance(error, dict) and (
            error.get("code") == "provider_risk_rejected"
            or error.get("source_code") == "provider_risk_rejected"
        ):
            return RerunPresentation(
                label="重新评估候选",
                confirmation="确认重新评估候选？不会重放历史被拒执行。",
                explanation="这次处理没有完成。可重新评估候选，不重放历史被拒执行；历史记录和已核验回执保留。",
            )
    return RerunPresentation()
