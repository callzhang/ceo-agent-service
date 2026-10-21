#!/usr/bin/env python3
"""Read Dingteam scores or submit one reviewed score in its authorized page.

The request is one JSON object on stdin. Browser session material stays in the
page; stdout contains only the provider result, never tokens or headers.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dingteam_okr_headless_source as headless  # noqa: E402


class PageOperationError(RuntimeError):
    def __init__(self, reason: str, *, write_may_have_started: bool) -> None:
        super().__init__(reason)
        self.write_may_have_started = write_may_have_started


def _required(request: dict, key: str) -> str:
    value = request.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Dingteam OKR request needs {key}")
    return value


def _page_script(request: dict, result_attribute: str) -> str:
    operation = request.get("operation")
    if operation == "read_score_detail":
        _required(request, "period_id")
        _required(request, "objective_id")
    elif operation == "score_kr":
        _required(request, "period_id")
        _required(request, "objective_id")
        _required(request, "batch_id")
        _required(request, "kr_id")
        _required(request, "evaluator_user_id")
        _required(request, "description")
        score = request.get("score")
        if type(score) not in (int, float) or not 0 <= score <= 1:
            raise ValueError("Dingteam OKR score must be on the 0-1 scale")
    else:
        raise ValueError("unknown Dingteam OKR page operation")

    return """
(async function() {
  const resultAttribute = __RESULT_ATTRIBUTE__;
  const request = __REQUEST__;
  let account;
  let api;
  function currentPrincipal() {
    if (!account || !account.userId) throw new Error('OKR page principal is unavailable');
    return String(account.userId);
  }
  async function readCurrent() {
    if (!api || !api.objective || !api.person) {
      throw new Error('OKR page API is unavailable');
    }
    const principal = currentPrincipal();
    const periods = await api.person.period.list({userId: principal});
    const periodList = periods && Array.isArray(periods.list) ? periods.list : [];
    const period = periodList.find(item => item &&
      String(item.okrId) === request.period_id);
    if (!period || !period.name) throw new Error('requested OKR period is unavailable');
    const batches = await api.objective.getObjectiveScoreBatch({okrId: request.period_id});
    const cells = batches && Array.isArray(batches.cells) ? batches.cells : [];
    const current = cells.filter(cell => cell && cell.isCurrent);
    if (current.length !== 1 || !current[0].batchId) {
      throw new Error('current OKR score batch is ambiguous');
    }
    const batchId = String(current[0].batchId);
    if (current[0].name !== period.name) {
      throw new Error('OKR score batch does not match the requested period');
    }
    const scoreInfo = await api.objective.findScoreInfos({mainId: request.period_id, batchId});
    if (!scoreInfo || scoreInfo.scoreType !== 1) {
      throw new Error('OKR period does not use KR-level scoring');
    }
    const detail = await api.objective.getScoreDetail({objectiveId: request.objective_id, batchId});
    if (!detail || String(detail.okrId) !== request.period_id ||
        String(detail.objectiveId) !== request.objective_id) {
      throw new Error('OKR score detail does not match the requested period and objective');
    }
    return {batch_id: batchId, detail, principal_user_id: principal};
  }
  function evaluator(current) {
    if (!current || current.batch_id !== request.batch_id ||
        !current.detail || String(current.detail.objectiveId) !== request.objective_id ||
        !Array.isArray(current.detail.krScoreDetails)) return null;
    const rows = current.detail.krScoreDetails.filter(
      row => row && String(row.krId) === request.kr_id);
    if (rows.length !== 1 || !Array.isArray(rows[0].roleUsers)) return null;
    const users = rows[0].roleUsers.flatMap(
      role => role && Array.isArray(role.users) ? role.users : []
    ).filter(user => user && String(user.userId || user.user_id || '') === request.evaluator_user_id);
    return users.length === 1 ? users[0] : null;
  }
  function matches(current) {
    const user = evaluator(current);
    return !!user && typeof user.score === 'number' &&
      Math.abs(user.score - request.score) <= 1e-9 &&
      user.info === request.description && user.status === 'complete';
  }
  let writeAttempted = false;
  try {
    let requireModule;
    if (window.webpackChunkallinone) {
      window.webpackChunkallinone.push([
        ['__ceo_okr_score_' + Date.now()], {}, function(require) { requireModule = require; }
      ]);
    }
    account = requireModule && requireModule(86036).A;
    api = requireModule && requireModule(1872).A;
    let data;
    if (request.operation === 'read_score_detail') {
      data = await readCurrent();
    } else {
      const before = await readCurrent();
      if (before.principal_user_id !== request.evaluator_user_id || !evaluator(before)) {
        data = {status: 'failed', reason: 'okr_score_target_changed'};
      } else if (matches(before)) {
        data = {status: 'verified', current: before, write_attempted: false};
      } else {
      writeAttempted = true;
      await api.objective.makeScore({
        objectiveId: request.objective_id,
        records: [{mainId: request.kr_id, score: request.score,
                   scoreDesc: request.description, type: 2}]
      });
      const after = await readCurrent();
      data = matches(after)
        ? {status: 'verified', current: after, write_attempted: true}
        : {status: 'uncertain', reason: 'okr_score_readback_incomplete'};
      }
    }
    document.documentElement.setAttribute(resultAttribute,
      JSON.stringify({ok: true, data}));
  } catch (error) {
    if (writeAttempted) {
      try {
        const after = await readCurrent();
        document.documentElement.setAttribute(resultAttribute, JSON.stringify({ok: true, data:
          matches(after) ? {status: 'verified', current: after, write_attempted: true}
                         : {status: 'uncertain', reason: 'okr_score_write_result_unknown'}
        }));
        return;
      } catch (_) {}
      document.documentElement.setAttribute(resultAttribute, JSON.stringify({ok: true, data:
        {status: 'uncertain', reason: 'okr_score_write_result_unknown'}
      }));
    } else {
      document.documentElement.setAttribute(resultAttribute, JSON.stringify(
        request.operation === 'score_kr'
          ? {ok: true, data: {status: 'failed', reason: 'okr_score_source_unavailable'}}
          : {ok: false, error: String(error)}
      ));
    }
  }
})();
""".replace("__RESULT_ATTRIBUTE__", json.dumps(result_attribute)).replace(
        "__REQUEST__", json.dumps(request, ensure_ascii=True)
    )


def run_request(request: dict, *, timeout_seconds: float = 45) -> dict:
    result_attribute = f"data-codex-dingteam-okr-score-{uuid.uuid4().hex}"
    page_script = _page_script(request, result_attribute)
    write_started = False
    try:
        with headless._headless_browser_lock():
            with headless.sync_playwright() as playwright:
                with headless._service_browser(playwright) as context:
                    page = context.new_page()
                    page.set_default_timeout(timeout_seconds * 1000)
                    page.goto(
                        headless.browser.ENTRY_URL,
                        wait_until="domcontentloaded",
                        timeout=60000,
                    )
                    page.wait_for_timeout(2500)
                    attempted = headless._maybe_attempt_local_dingtalk_sso(
                        page, attempted=False
                    )
                    deadline = time.monotonic() + headless.HEADLESS_REFRESH_SECONDS
                    while time.monotonic() < deadline:
                        page.wait_for_timeout(800)
                        attempted = headless._maybe_attempt_local_dingtalk_sso(
                            page, attempted=attempted
                        )
                        if page.url.startswith("https://dingokr.dingteam.com/"):
                            try:
                                ready = page.evaluate("""() => {
                                  let requireModule;
                                  window.webpackChunkallinone?.push([
                                    ['__ceo_okr_ready_' + Date.now()], {},
                                    function(require) { requireModule = require; }
                                  ]);
                                  return !!requireModule?.(86036)?.A?.userId;
                                }""")
                            except Exception as exc:
                                if "execution context was destroyed" not in str(exc).lower():
                                    raise
                                ready = False
                            if ready:
                                break
                    else:
                        raise RuntimeError("Dingteam OKR page is not authenticated")
                    write_started = request["operation"] == "score_kr"
                    page.evaluate(page_script)
                    raw = page.evaluate(
                        "attribute => document.documentElement.getAttribute(attribute)",
                        result_attribute,
                    )
    except Exception as exc:
        diagnostic = getattr(exc, "stderr", "") or str(exc)
        if isinstance(diagnostic, str) and diagnostic.splitlines():
            diagnostic = diagnostic.splitlines()[-1]
        raise PageOperationError(
            str(diagnostic), write_may_have_started=write_started
        ) from exc
    if not isinstance(raw, str) or not raw:
        raise RuntimeError("Dingteam OKR page did not return a result")
    response = json.loads(raw)
    if response.get("ok") is not True:
        raise RuntimeError(str(response.get("error") or "Dingteam OKR page operation failed"))
    data = response.get("data")
    if not isinstance(data, dict):
        raise ValueError("Dingteam OKR page response is not an object")
    return data


def main() -> int:
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise ValueError("Dingteam OKR request is not an object")
        print(json.dumps(run_request(request), ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({
            "error": type(exc).__name__,
            "reason": headless._safe_source_error_detail(str(exc))[:240],
            "write_may_have_started": bool(getattr(exc, "write_may_have_started", False)),
        }), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
