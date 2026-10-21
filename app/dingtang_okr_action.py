"""One reviewed Dingteam OKR KR score, executed through the existing page session."""

from __future__ import annotations

from hashlib import sha256
import json
from math import isclose, isfinite
from pathlib import Path
import subprocess
import sys
from typing import Mapping

from app.agent_contracts import ProposedAction
from app.system_executor import ActionOutcome


_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "dingteam_okr_score.py"


class DingtangOkrPageRequestError(RuntimeError):
    def __init__(self, reason: str, *, may_have_written: bool) -> None:
        super().__init__(reason)
        self.may_have_written = may_have_written


class DingtangOkrBrowserClient:
    """Use the same authorized Chrome page and JS injection as the live Skill source."""

    def _call(self, request: dict[str, object]) -> dict[str, object]:
        process = subprocess.Popen(
            [sys.executable, str(_SCRIPT)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(
                input=json.dumps(request, ensure_ascii=False), timeout=290
            )
        except subprocess.TimeoutExpired as exc:
            from scripts.dingteam_okr_headless_source import _stop_process_group

            _stop_process_group(process)
            raise DingtangOkrPageRequestError(
                "Dingteam OKR browser timed out",
                may_have_written=request.get("operation") == "score_kr",
            ) from exc
        if process.returncode != 0:
            try:
                error = json.loads(stderr)
                reason = str(error.get("reason") or error.get("error") or "page request failed")
                may_have_written = error.get("write_may_have_started") is True
            except (ValueError, AttributeError):
                reason = "page request failed"
                may_have_written = request.get("operation") == "score_kr"
            raise DingtangOkrPageRequestError(
                f"Dingteam OKR {reason}", may_have_written=may_have_written
            )
        payload = json.loads(stdout)
        if not isinstance(payload, dict):
            raise ValueError("Dingteam OKR page response is not an object")
        return payload

    def read_score_detail(self, *, period_id: str, objective_id: str) -> dict[str, object]:
        return self._call({
            "operation": "read_score_detail",
            "period_id": period_id,
            "objective_id": objective_id,
        })

    def score_kr(
        self, *, period_id: str, objective_id: str, batch_id: str,
        kr_id: str, evaluator_user_id: str, score: float, description: str,
    ) -> dict[str, object]:
        return self._call({
            "operation": "score_kr",
            "period_id": period_id,
            "objective_id": objective_id,
            "batch_id": batch_id,
            "kr_id": kr_id,
            "evaluator_user_id": evaluator_user_id,
            "score": score,
            "description": description,
        })


def _score_spec(action: ProposedAction) -> tuple[str, str, str, str, str, float, str]:
    target = action.target
    fields = ("period_id", "objective_id", "batch_id", "kr_id", "evaluator_user_id")
    ids = tuple(target.get(field) for field in fields)
    if any(not isinstance(value, str) or not value.strip() for value in ids):
        raise ValueError("OKR score needs exact period, objective, batch, KR and evaluator IDs")
    score = action.payload.get("score")
    if type(score) not in (int, float) or not isfinite(score) or not 0 <= score <= 1:
        raise ValueError("OKR score must be a finite number on the 0-1 API scale")
    description = action.payload.get("score_description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("OKR score needs a nonempty evidence-based description")
    return (*ids, float(score), description)


def _evaluator(detail: Mapping[str, object], kr_id: str, evaluator_id: str) -> dict | None:
    rows = detail.get("krScoreDetails")
    if not isinstance(rows, list):
        return None
    matching = [row for row in rows if isinstance(row, dict) and str(row.get("krId")) == kr_id]
    if len(matching) != 1:
        return None
    roles = matching[0].get("roleUsers")
    if not isinstance(roles, list):
        return None
    users = []
    for role in roles:
        if not isinstance(role, dict) or not isinstance(role.get("users"), list):
            continue
        users.extend(
            user for user in role["users"]
            if isinstance(user, dict)
            and str(user.get("userId") or user.get("user_id") or "") == evaluator_id
        )
    return users[0] if len(users) == 1 else None


def _current(
    response: Mapping[str, object], *, period_id: str, batch_id: str, objective_id: str,
    kr_id: str, evaluator_id: str,
) -> dict | None:
    if str(response.get("batch_id") or "") != batch_id:
        return None
    if str(response.get("principal_user_id") or "") != evaluator_id:
        return None
    detail = response.get("detail")
    if (
        not isinstance(detail, dict)
        or str(detail.get("objectiveId") or "") != objective_id
        or str(detail.get("okrId") or "") != period_id
    ):
        return None
    return _evaluator(detail, kr_id, evaluator_id)


def _matches(user: Mapping[str, object], score: float, description: str) -> bool:
    actual = user.get("score")
    return (
        type(actual) in (int, float)
        and isclose(float(actual), score, abs_tol=1e-9)
        and user.get("info") == description
        and user.get("status") == "complete"
    )


class DingtangOkrScoreHandler:
    def __init__(self, client: object | None = None) -> None:
        self.client = client or DingtangOkrBrowserClient()

    def _receipt(
        self, period_id: str, objective_id: str, batch_id: str, kr_id: str,
        evaluator_id: str, score: float, description: str,
    ) -> ActionOutcome:
        return ActionOutcome("verified", {
            "period_id": period_id,
            "objective_id": objective_id,
            "batch_id": batch_id,
            "kr_id": kr_id,
            "evaluator_user_id": evaluator_id,
            "score": score,
            "score_description_sha256": sha256(description.encode()).hexdigest(),
            "readback": "complete_exact_evaluator_score_and_description",
        })

    def dispatch(
        self, action: ProposedAction, *, action_key: str, candidate: dict[str, object]
    ) -> ActionOutcome:
        try:
            period_id, objective_id, batch_id, kr_id, evaluator_id, score, description = _score_spec(action)
        except ValueError as exc:
            return ActionOutcome("failed", {"reason": str(exc)})
        try:
            result = self.client.score_kr(
                period_id=period_id, objective_id=objective_id, batch_id=batch_id,
                kr_id=kr_id, evaluator_user_id=evaluator_id,
                score=score, description=description,
            )
        except DingtangOkrPageRequestError as exc:
            if not exc.may_have_written:
                return ActionOutcome("failed", {"reason": "okr_score_source_unavailable"})
            return self.reconcile(action, action_key=action_key, candidate=candidate) or ActionOutcome(
                "uncertain", {"reason": "okr_score_write_result_unknown"}
            )
        except Exception:
            # A process failure can occur after the page submitted makeScore.
            return self.reconcile(action, action_key=action_key, candidate=candidate) or ActionOutcome(
                "uncertain", {"reason": "okr_score_write_result_unknown"}
            )
        if result.get("status") == "failed":
            return ActionOutcome("failed", {"reason": str(result.get("reason") or "okr_score_target_changed")})
        if result.get("status") != "verified":
            return ActionOutcome("uncertain", {"reason": str(result.get("reason") or "okr_score_readback_incomplete")})
        current = result.get("current")
        user = _current(
            current if isinstance(current, dict) else {},
            period_id=period_id, batch_id=batch_id, objective_id=objective_id,
            kr_id=kr_id, evaluator_id=evaluator_id,
        )
        if user is None or not _matches(user, score, description):
            return ActionOutcome("uncertain", {"reason": "okr_score_readback_incomplete"})
        return self._receipt(period_id, objective_id, batch_id, kr_id, evaluator_id, score, description)

    def reconcile(
        self, action: ProposedAction, *, action_key: str, candidate: dict[str, object]
    ) -> ActionOutcome | None:
        try:
            period_id, objective_id, batch_id, kr_id, evaluator_id, score, description = _score_spec(action)
            current = self.client.read_score_detail(
                period_id=period_id, objective_id=objective_id
            )
        except Exception:
            return None
        user = _current(
            current, period_id=period_id, batch_id=batch_id, objective_id=objective_id,
            kr_id=kr_id, evaluator_id=evaluator_id,
        )
        if user is None or not _matches(user, score, description):
            return None
        return self._receipt(period_id, objective_id, batch_id, kr_id, evaluator_id, score, description)
