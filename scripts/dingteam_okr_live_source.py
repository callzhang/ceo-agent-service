#!/usr/bin/env python3
"""Fetch live Dingteam OKR data from an authorized Chrome tab.

This script intentionally uses the page's own API module from the logged-in
`dingokr.dingteam.com` tab. It does not read browser cookies, localStorage, or
profile files.
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
import uuid


APPLESCRIPT = """
on run argv
  set targetScript to item 1 of argv
  tell application "Google Chrome"
    repeat with w in windows
      repeat with t in tabs of w
        if (URL of t) contains "dingokr.dingteam.com" then
          tell t to return execute javascript targetScript
        end if
      end repeat
    end repeat
  end tell
  error "No authorized Dingteam OKR Chrome tab found"
end run
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--period-label", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=24.0)
    args = parser.parse_args()

    result_attribute = f"data-codex-dingteam-okr-live-{uuid.uuid4().hex}"
    page_script = _build_page_script(
        user_id=args.user_id,
        period_label=args.period_label,
        result_attribute=result_attribute,
    )
    _execute_in_dingteam_tab(_inject_script(page_script))

    deadline = time.monotonic() + args.timeout_seconds
    while time.monotonic() < deadline:
        raw = _execute_in_dingteam_tab(
            "document.documentElement.getAttribute("
            + json.dumps(result_attribute)
            + ") || ''"
        )
        if raw:
            result = json.loads(raw)
            if not result.get("ok"):
                raise RuntimeError(result)
            print(json.dumps(result["data"], ensure_ascii=False))
            return 0
        time.sleep(0.4)

    raise TimeoutError("Timed out waiting for Dingteam OKR live source result")


def _execute_in_dingteam_tab(script: str) -> str:
    completed = subprocess.run(
        ["osascript", "-e", APPLESCRIPT, script],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout.strip()


def _inject_script(page_script: str) -> str:
    encoded = base64.b64encode(page_script.encode("utf-8")).decode("ascii")
    return (
        "(function(){"
        f"var sourceBase64={json.dumps(encoded)};"
        "var bytes=Uint8Array.from(atob(sourceBase64),function(c){return c.charCodeAt(0);});"
        "var source=new TextDecoder('utf-8').decode(bytes);"
        "var script=document.createElement('script');"
        "script.textContent=source;"
        "document.documentElement.appendChild(script);"
        "script.remove();"
        "return 'started';"
        "})()"
    )


def _build_page_script(*, user_id: str, period_label: str, result_attribute: str) -> str:
    return f"""
(async function(){{
  const resultAttribute = {json.dumps(result_attribute)};
  const requestedUserId = {json.dumps(user_id)};
  const requestedPeriodLabel = {json.dumps(period_label)};

  function exposeError(error) {{
    document.documentElement.setAttribute(resultAttribute, JSON.stringify({{
      ok: false,
      error: String(error),
      stack: error && error.stack ? String(error.stack) : ''
    }}));
  }}

  window.addEventListener('error', function(event) {{
    exposeError(event.error || event.message);
  }}, {{ once: true }});
  window.addEventListener('unhandledrejection', function(event) {{
    exposeError(event.reason || 'Unhandled promise rejection');
  }}, {{ once: true }});

  function textFromRichText(raw) {{
    if (!raw || typeof raw !== 'string') return '';
    const parsed = JSON.parse(raw);
    const parts = [];
    function visit(node) {{
      if (!node || typeof node !== 'object') return;
      if (typeof node.text === 'string') parts.push(node.text);
      if (Array.isArray(node.children)) node.children.forEach(visit);
    }}
    if (Array.isArray(parsed)) parsed.forEach(visit);
    return parts.join('\\n').replace(/\\s+\\n/g, '\\n').replace(/\\n\\s+/g, '\\n').trim();
  }}

  function normalizedPeriod(value) {{
    return String(value || '')
      .toLowerCase()
      .replace(/\\s+/g, '')
      .replace(/年/g, '')
      .replace(/第/g, '')
      .replace(/一季度|1季度|q1/g, 'q1')
      .replace(/二季度|2季度|q2/g, 'q2')
      .replace(/三季度|3季度|q3/g, 'q3')
      .replace(/四季度|4季度|q4/g, 'q4')
      .replace(/季/g, '');
  }}

  function progressPercent(value) {{
    if (typeof value !== 'number') return value ?? '';
    return Math.round((value / 100) * 100) / 100;
  }}

  function formatTimestamp(value) {{
    if (typeof value !== 'number') return '';
    return new Date(value).toISOString();
  }}

  function progressChangeText(history) {{
    const values = Array.isArray(history.colorContents) ? history.colorContents : [];
    return values.map(function(item) {{ return item && item.content ? String(item.content) : ''; }})
      .filter(Boolean)
      .join('；');
  }}

  function aggregateHistory(histories) {{
    if (!Array.isArray(histories) || histories.length === 0) return '[未撰写进度]';
    return histories.map(function(history) {{
      const timestamp = formatTimestamp(history.createAt) || '时间未知';
      const change = progressChangeText(history) || '进度未注明';
      const content = textFromRichText(history.singleContent) || '未填写说明';
      return timestamp + ' | ' + change + ' | ' + content;
    }}).join('\\n');
  }}

  window.webpackChunkallinone.push([[Date.now()], {{}}, function(require) {{
    window.__codexDingteamOkrRequire = require;
  }}]);
  const api = window.__codexDingteamOkrRequire(37615).Z;
  const periodsPayload = await api.person.period.list({{ userId: requestedUserId }});
  const periods = Array.isArray(periodsPayload.list) ? periodsPayload.list : [];
  const periodKey = normalizedPeriod(requestedPeriodLabel);
  const period = periods.find(function(item) {{
    return normalizedPeriod(item.name) === periodKey;
  }});
  if (!period) {{
    throw new Error('Dingteam OKR period not found: ' + requestedPeriodLabel);
  }}

  const listPayload = await api.objective.showListView.v2({{
    mainId: period.okrId,
    type: 0,
    search: {{
      userIds: [requestedUserId],
      pageNo: 1,
      pageSize: 9999
    }}
  }});
  const objectiveList = Array.isArray(listPayload.list) ? listPayload.list : [];
  const objectiveProgressHistories = [];
  const objectiveDetails = [];
  const processedObjectives = [];
  const okrRows = [];

  for (const objective of objectiveList) {{
    const objectiveId = objective.id;
    const objectiveTitle = objective.name || textFromRichText(objective.nameRichText);
    const objectiveWeight = objective.weight ?? '';
    const objectiveProgress = progressPercent(objective.progress);
    const krCells = Array.isArray(objective.krCells) ? objective.krCells : [];
    const objectiveRow = {{
      objectiveId: objectiveId,
      title: objectiveTitle,
      weight: objectiveWeight,
      progress: objectiveProgress,
      owner: objective.owner || '',
      ownerName: objective.ownerName || '',
      latestProgressText: '',
      keyResults: [],
      unscopedProgressUpdates: []
    }};

    okrRows.push({{
      level: 'O',
      objectiveId: objectiveId,
      objectiveTitle: objectiveTitle,
      objectiveWeight: objectiveWeight,
      objectiveProgress: objectiveProgress,
      krId: '',
      krTitle: '',
      krWeight: '',
      krProgress: '',
      krDetailsUpdatesAggregated: ''
    }});

    const detailRows = await Promise.all(krCells.map(async function(kr) {{
      const krId = kr.id;
      const detail = await api.objective.findKrDetail({{ objId: objectiveId, krId: krId }});
      const progressHistory = await api.objective.log.progressHistory({{
        objectiveId: objectiveId,
        krId: krId
      }});
      return {{ krId: krId, detail: detail, progressHistory: progressHistory }};
    }}));

    for (const detailRow of detailRows) {{
      const kr = krCells.find(function(item) {{ return item.id === detailRow.krId; }});
      const detail = detailRow.detail || {{}};
      const histories = Array.isArray(detailRow.progressHistory && detailRow.progressHistory.histories)
        ? detailRow.progressHistory.histories
        : [];
      const progressUpdates = histories.map(function(history) {{
        return {{
          logId: history.logId || '',
          createdAt: formatTimestamp(history.createAt),
          progressChange: progressChangeText(history),
          content: textFromRichText(history.singleContent),
          childFiles: history.childFiles || []
        }};
      }});
      const aggregated = aggregateHistory(histories);
      const krTitle = (detail.content || (kr && kr.content) || textFromRichText(detail.contentRichText) || '').trim();
      const keyResultRow = {{
        keyResultId: detailRow.krId,
        title: krTitle,
        weight: detail.weight ?? (kr && kr.weight) ?? '',
        progress: progressPercent(detail.progress ?? (kr && kr.progress)),
        deadline: detail.deadline ?? (kr && kr.deadline) ?? null,
        progressUpdates: progressUpdates,
        progressUpdatesAggregated: aggregated
      }};
      objectiveRow.keyResults.push(keyResultRow);
      okrRows.push({{
        level: 'KR',
        objectiveId: objectiveId,
        objectiveTitle: objectiveTitle,
        objectiveWeight: objectiveWeight,
        objectiveProgress: objectiveProgress,
        krId: detailRow.krId,
        krTitle: krTitle,
        krWeight: keyResultRow.weight,
        krProgress: keyResultRow.progress,
        krDeadline: keyResultRow.deadline,
        krDetailsUpdatesAggregated: aggregated
      }});
      objectiveDetails.push({{
        objectiveId: objectiveId,
        keyResultId: detailRow.krId,
        payload: detail
      }});
      objectiveProgressHistories.push({{
        objectiveId: objectiveId,
        keyResultId: detailRow.krId,
        payload: detailRow.progressHistory
      }});
    }}

    processedObjectives.push(objectiveRow);
  }}

  const sanitizedPageUrl = location.origin + location.pathname + location.hash;
  document.documentElement.setAttribute(resultAttribute, JSON.stringify({{
    ok: true,
    data: {{
      source: {{
        system: '叮当OKR Dingteam Web',
        pageUrl: sanitizedPageUrl,
        appId: window.APP_APPID || '',
        suiteId: window.APP_SUITE_ID || '',
        goodsCode: window.APP_GOODS_CODE || '',
        capturedAt: new Date().toISOString()
      }},
      userId: requestedUserId,
      periodLabel: requestedPeriodLabel,
      period: period,
      periods: periods,
      objectiveList: objectiveList,
      objectiveDetails: objectiveDetails,
      objectiveProgressHistories: objectiveProgressHistories,
      processed: {{
        objectives: processedObjectives,
      okrRows: okrRows
      }}
    }}
  }}));
}})();
"""


if __name__ == "__main__":
    sys.exit(main())
