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
import os
import subprocess
import sys
import time
import uuid


DINGTEAM_TAB_URL_PREFIX = "https://dingokr.dingteam.com/"


APPLESCRIPT = f"""
on run argv
  set targetScript to item 1 of argv
  tell application "Google Chrome"
    repeat with w in windows
      repeat with t in tabs of w
        if (URL of t) starts with "{DINGTEAM_TAB_URL_PREFIX}" then
          tell t to return execute javascript targetScript
        end if
      end repeat
    end repeat
  end tell
  error "No authorized Dingteam OKR Chrome tab found"
end run
"""


# Open the 叮当OKR tab automatically if none is present, reusing the existing
# Chrome login session.
_CORP_ID = os.getenv("CEO_OKR_DINGTEAM_CORPID", "ding8ffc70a4ef94915f35c2f4657eb6378f")
_APP_ID = os.getenv("CEO_OKR_DINGTEAM_APPID", "40707")
_SUITE_ID = os.getenv("CEO_OKR_DINGTEAM_SUITEID", "9242001")
DINGTEAM_URL = os.getenv(
    "CEO_OKR_DINGTEAM_URL",
    "https://dingokr.dingteam.com/web/okr/pc/index.html"
    f"?corpid={_CORP_ID}&appid={_APP_ID}&suiteid={_SUITE_ID}",
)

OPEN_TAB_APPLESCRIPT = """
on run argv
  set targetUrl to item 1 of argv
  tell application "Google Chrome"
    repeat with w in windows
      repeat with t in tabs of w
        if (URL of t) starts with "https://dingokr.dingteam.com/" then
          return "exists"
        end if
      end repeat
    end repeat
    if (count of windows) = 0 then
      make new window
    end if
    tell front window to make new tab with properties {URL:targetUrl}
    return "opened"
  end tell
end run
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--period-label", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=24.0)
    args = parser.parse_args()

    _ensure_dingteam_tab()

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
                raise RuntimeError(_format_page_error(result))
            print(json.dumps(result["data"], ensure_ascii=False))
            return 0
        time.sleep(0.4)

    raise TimeoutError("Timed out waiting for Dingteam OKR live source result")


def _format_page_error(result: dict) -> str:
    error = str(result.get("error") or "").strip()
    stack = str(result.get("stack") or "").strip()
    first_stack_line = next((line.strip() for line in stack.splitlines() if line.strip()), "")
    detail = error or first_stack_line or json.dumps(result, ensure_ascii=False)
    return f"Dingteam OKR page script failed: {detail}"


def _print_cli_error(exc: BaseException) -> None:
    print(
        json.dumps(
            {
                "message": "Dingteam OKR live source failed",
                "reason": str(exc),
            },
            ensure_ascii=False,
        ),
        file=sys.stderr,
    )


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


def _ensure_dingteam_tab(wait_seconds: float = 25.0) -> None:
    """Ensure an authorized dingokr.dingteam.com tab exists; open one if not.

    Opening reuses the existing Chrome login session. If the user is not logged
    in, the new tab lands on the DingTalk login wall and the extraction will
    fail clearly afterwards.
    """
    completed = subprocess.run(
        ["osascript", "-e", OPEN_TAB_APPLESCRIPT, DINGTEAM_URL],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    if completed.stdout.strip() == "exists":
        return
    # A new tab was opened; wait for the SPA bundle to be ready before injecting.
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        try:
            ready = _execute_in_dingteam_tab(
                "(function(){try{return window.webpackChunkallinone?'ready':'loading';}"
                "catch(e){return 'loading';}})()"
            )
            if ready == "ready":
                return
        except Exception:
            pass
        time.sleep(1.0)


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

  function commentText(raw) {{
    if (!raw || typeof raw !== 'string') return '';
    const parsed = JSON.parse(raw);
    const parts = [];
    function visit(node) {{
      if (!node || typeof node !== 'object') return;
      if (node.type === 'at' && node.atName) parts.push('@' + node.atName);
      else if (typeof node.text === 'string') parts.push(node.text);
      if (Array.isArray(node.children)) node.children.forEach(visit);
    }}
    if (Array.isArray(parsed)) parsed.forEach(visit);
    return parts.join('').replace(/[ \\t]+/g, ' ').trim();
  }}

  function krMatchKey(text) {{
    return String(text || '').replace(/\\s+/g, '').slice(0, 20);
  }}

  function aggregateComments(comments) {{
    return comments.map(function(c) {{
      const ts = (formatTimestamp(c.createAt) || '').slice(0, 10) || '时间未知';
      return ts + ' | ' + (c.author || '?') + ' | ' + c.text;
    }}).join('\\n');
  }}

  function mergeUpdates(historyAgg, commentAgg) {{
    const pieces = [];
    if (historyAgg && historyAgg !== '[未撰写进度]') pieces.push(historyAgg);
    if (commentAgg) pieces.push(commentAgg);
    return pieces.length ? pieces.join('\\n') : '[未撰写进度]';
  }}

  async function fetchObjectiveComments(objectiveId) {{
    const payload = await api.objective.findCommentListV2({{
      objectiveId: objectiveId, pageNo: 1, pageSize: 100,
      sort: false, logTypeCells: [], krId: '', commentId: ''
    }});
    const list = Array.isArray(payload.list) ? payload.list : [];
    const out = [];
    list.forEach(function(item) {{
      if (!item || item.type !== 5) return;
      const text = commentText(item.richTextContent);
      if (!text) return;
      const krInfo = (item.krInfo && typeof item.krInfo === 'object') ? item.krInfo : {{}};
      const creator = (item.creator && typeof item.creator === 'object') ? item.creator : {{}};
      out.push({{
        createAt: item.createAt,
        author: creator.name || creator.userName || '',
        text: text,
        krId: krInfo.krId || '',
        krName: krInfo.name || ''
      }});
    }});
    return out;
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

    const objComments = await fetchObjectiveComments(objectiveId);
    const commentsByKrId = {{}};
    const commentsByKrKey = {{}};
    const objectiveLevelComments = [];
    objComments.forEach(function(c) {{
      if (c.krId) {{ (commentsByKrId[c.krId] = commentsByKrId[c.krId] || []).push(c); }}
      else if (c.krName) {{
        const k = krMatchKey(c.krName);
        (commentsByKrKey[k] = commentsByKrKey[k] || []).push(c);
      }} else {{ objectiveLevelComments.push(c); }}
    }});
    const objectiveCommentsAggregated = aggregateComments(objectiveLevelComments);

    const objectiveRow = {{
      objectiveId: objectiveId,
      title: objectiveTitle,
      weight: objectiveWeight,
      progress: objectiveProgress,
      owner: objective.owner || '',
      ownerName: objective.ownerName || '',
      latestProgressText: '',
      objectiveCommentsAggregated: objectiveCommentsAggregated,
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
      krDetailsUpdatesAggregated: objectiveCommentsAggregated
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
      const krTitle = (detail.content || (kr && kr.content) || textFromRichText(detail.contentRichText) || '').trim();
      const krComments = commentsByKrId[detailRow.krId]
        || commentsByKrKey[krMatchKey(krTitle)]
        || commentsByKrKey[krMatchKey(kr && kr.content)]
        || [];
      const aggregated = mergeUpdates(aggregateHistory(histories), aggregateComments(krComments));
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
    try:
        sys.exit(main())
    except Exception as exc:
        _print_cli_error(exc)
        sys.exit(1)
