#!/usr/bin/env python3
"""Fetch live Dingteam (叮当OKR) data via direct server-side HTTP calls.

Unlike ``dingteam_okr_live_source.py`` (which injects an extraction script into
the page and depends on the page's webpack module + DOM polling), this script
touches the authorized ``dingokr.dingteam.com`` Chrome tab only ONCE — to read
the current session auth headers from a real request — and then calls the
private ``/data/okr/...`` JSON API directly with urllib. No cookies, localStorage,
or profile files are read; the auth token is obtained via the page's own outgoing
request headers, the same trust boundary the page itself uses.

Output matches the dingtang-okr-review skill contract: ``processed.objectives``
and ``processed.okrRows`` (each KR row carries parent O + weights/progress +
``krDetailsUpdatesAggregated``).
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE = "https://dingokr.dingteam.com"

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


def _run_js(script: str) -> str:
    completed = subprocess.run(
        ["osascript", "-e", APPLESCRIPT, script],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout.strip()


def _inject(page_script: str) -> str:
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


def capture_auth_headers(timeout_seconds: float = 12.0) -> dict[str, str]:
    """Read the session auth headers from one real /data/okr/ request."""
    attr = f"data-okr-auth-{uuid.uuid4().hex}"
    page = """
(async function(){
  const ATTR = %s;
  function done(o){ document.documentElement.setAttribute(ATTR, JSON.stringify(o)); }
  try {
    let captured = null;
    function capture(url, headers) {
      if (!/\\/data\\/okr\\//.test(String(url || '')) || captured) return;
      const normalized = {};
      if (headers) {
        if (headers.forEach) {
          headers.forEach(function(v, k){ normalized[k] = v; });
        } else {
          Object.keys(headers).forEach(function(k){ normalized[k] = headers[k]; });
        }
      }
      if (Object.keys(normalized).length) captured = normalized;
    }
    const of = window.fetch;
    window.fetch = function(input, init){
      try {
        const url = (typeof input==='string')?input:(input&&input.url);
        capture(url, init && init.headers);
      } catch(e){}
      return of.apply(this, arguments);
    };
    const xhrOpen = XMLHttpRequest.prototype.open;
    const xhrSetRequestHeader = XMLHttpRequest.prototype.setRequestHeader;
    const xhrSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function(method, url) {
      this.__codexOkrUrl = url;
      this.__codexOkrHeaders = {};
      return xhrOpen.apply(this, arguments);
    };
    XMLHttpRequest.prototype.setRequestHeader = function(name, value) {
      if (this.__codexOkrHeaders) this.__codexOkrHeaders[name] = value;
      return xhrSetRequestHeader.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function() {
      try { capture(this.__codexOkrUrl, this.__codexOkrHeaders); } catch (e) {}
      return xhrSend.apply(this, arguments);
    };
    const objectiveItems = Array.from(document.querySelectorAll('[data-testid^=ObjectiveItem]'));
    const target = objectiveItems.reverse().find(function(element) {
      return element.querySelector('[data-testid^=Editable]');
    }) || objectiveItems[0];
    if (!target) throw new Error('No objective item available to trigger an OKR request');
    const closeButtons = Array.from(document.querySelectorAll('[aria-label=close]'));
    if (closeButtons.length) closeButtons[closeButtons.length - 1].click();
    setTimeout(function(){ target.click(); }, 100);
    setTimeout(function(){
      window.fetch = of;
      XMLHttpRequest.prototype.open = xhrOpen;
      XMLHttpRequest.prototype.setRequestHeader = xhrSetRequestHeader;
      XMLHttpRequest.prototype.send = xhrSend;
      done(captured || {__none:true});
    }, 1800);
  } catch(e){ done({__err:String(e)}); }
})();
""" % json.dumps(attr)
    _run_js(_inject(page))
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        raw = _run_js(
            "document.documentElement.getAttribute(" + json.dumps(attr) + ") || ''"
        )
        if raw:
            data = json.loads(raw)
            if data.get("__none") or data.get("__err"):
                raise RuntimeError(f"could not capture Dingteam auth headers: {data}")
            return {k: v for k, v in data.items() if v is not None}
        time.sleep(0.4)
    raise TimeoutError("timed out capturing Dingteam auth headers")


def _post(path: str, body: dict, headers: dict[str, str]) -> dict:
    req_headers = dict(headers)
    req_headers["Content-Type"] = "application/json"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    for attempt in range(3):
        request = Request(BASE + path, data=data, method="POST", headers=req_headers)
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"Dingteam API {path} failed: HTTP {exc.code} {detail}") from exc
        except (TimeoutError, URLError) as exc:
            if attempt == 2:
                raise RuntimeError(f"Dingteam API {path} failed after 3 attempts: {exc}") from exc
            time.sleep(1.5 * (attempt + 1))
    raise AssertionError("unreachable")


def _unwrap(payload):
    if isinstance(payload, dict) and isinstance(payload.get("data"), (dict, list)):
        return payload["data"]
    return payload


def _as_list(payload, key="list"):
    data = _unwrap(payload)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get(key), list):
        return data[key]
    return []


# --- helpers ported from the page extraction script ---

def text_from_rich_text(raw) -> str:
    if not raw or not isinstance(raw, str):
        return ""
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return ""
    parts: list[str] = []

    def visit(node):
        if not isinstance(node, dict):
            return
        if isinstance(node.get("text"), str):
            parts.append(node["text"])
        children = node.get("children")
        if isinstance(children, list):
            for child in children:
                visit(child)

    if isinstance(parsed, list):
        for node in parsed:
            visit(node)
    joined = "\n".join(parts)
    joined = re.sub(r"\s+\n", "\n", joined)
    joined = re.sub(r"\n\s+", "\n", joined)
    return joined.strip()


def normalized_period(value: str) -> str:
    s = str(value or "").lower()
    s = re.sub(r"\s+", "", s)
    s = s.replace("年", "").replace("第", "")
    s = re.sub(r"一季度|1季度|q1", "q1", s)
    s = re.sub(r"二季度|2季度|q2", "q2", s)
    s = re.sub(r"三季度|3季度|q3", "q3", s)
    s = re.sub(r"四季度|4季度|q4", "q4", s)
    s = s.replace("季", "")
    return s


def progress_percent(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value if value is not None else ""
    return math.floor(value + 0.5) / 100


def format_timestamp(value) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return ""
    dt = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def progress_change_text(history: dict) -> str:
    values = history.get("colorContents")
    if not isinstance(values, list):
        return ""
    return "；".join(
        str(item["content"]) for item in values
        if isinstance(item, dict) and item.get("content")
    )


def aggregate_history(histories) -> str:
    if not isinstance(histories, list) or not histories:
        return "[未撰写进度]"
    lines = []
    for history in histories:
        if not isinstance(history, dict):
            continue
        timestamp = format_timestamp(history.get("createAt")) or "时间未知"
        change = progress_change_text(history) or "进度未注明"
        content = text_from_rich_text(history.get("singleContent")) or "未填写说明"
        lines.append(f"{timestamp} | {change} | {content}")
    return "\n".join(lines)


def comment_text(raw) -> str:
    """Parse a comment's richTextContent (text + @mentions) into plain text."""
    if not raw or not isinstance(raw, str):
        return ""
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return ""
    parts: list[str] = []

    def visit(node):
        if not isinstance(node, dict):
            return
        if node.get("type") == "at" and node.get("atName"):
            parts.append("@" + str(node["atName"]))
        elif isinstance(node.get("text"), str):
            parts.append(node["text"])
        children = node.get("children")
        if isinstance(children, list):
            for child in children:
                visit(child)

    if isinstance(parsed, list):
        for node in parsed:
            visit(node)
    return re.sub(r"[ \t]+", " ", "".join(parts)).strip()


def _kr_match_key(text) -> str:
    return re.sub(r"\s+", "", str(text or ""))[:20]


def fetch_objective_comments(objective_id: str, headers: dict[str, str]) -> list[dict]:
    """Fetch the objective's comment/进展 records (the right-side discussion).

    These carry real KR progress narrative even when the numeric progress is 0,
    so they are essential scoring evidence.
    """
    payload = _post(
        "/data/okr/objective/findCommentList/v2",
        {
            "objectiveId": objective_id,
            "pageNo": 1,
            "pageSize": 100,
            "sort": False,
            "logTypeCells": [],
            "krId": "",
            "commentId": "",
        },
        headers,
    )
    items = _as_list(payload)
    comments = []
    for item in items:
        if not isinstance(item, dict) or item.get("type") != 5:  # 5 = 评论/进展
            continue
        text = comment_text(item.get("richTextContent"))
        if not text:
            continue
        kr_info = item.get("krInfo") if isinstance(item.get("krInfo"), dict) else {}
        creator = item.get("creator") if isinstance(item.get("creator"), dict) else {}
        comments.append({
            "createAt": item.get("createAt"),
            "author": creator.get("name") or creator.get("userName") or "",
            "text": text,
            "krId": kr_info.get("krId") or "",
            "krName": kr_info.get("name") or "",
        })
    return comments


def aggregate_comments(comments) -> str:
    lines = []
    for comment in comments:
        timestamp = (format_timestamp(comment.get("createAt")) or "")[:10] or "时间未知"
        author = comment.get("author") or "?"
        lines.append(f"{timestamp} | {author} | {comment.get('text', '')}")
    return "\n".join(lines)


def _merge_updates(history_agg: str, comment_agg: str) -> str:
    pieces = []
    if history_agg and history_agg != "[未撰写进度]":
        pieces.append(history_agg)
    if comment_agg:
        pieces.append(comment_agg)
    return "\n".join(pieces) if pieces else "[未撰写进度]"


def fetch_with_headers(user_id: str, period_label: str, headers: dict[str, str]) -> dict:
    periods_payload = _post(
        "/data/okr/person/period/list", {"userId": user_id}, headers
    )
    periods = _as_list(periods_payload)
    period_key = normalized_period(period_label)
    period = next(
        (p for p in periods if normalized_period(p.get("name")) == period_key), None
    )
    if period is None:
        raise RuntimeError(f"Dingteam OKR period not found: {period_label}")

    list_payload = _post(
        "/data/okr/objective/showListView/v2",
        {
            "mainId": period["okrId"],
            "type": 0,
            "search": {"userIds": [user_id], "pageNo": 1, "pageSize": 9999},
        },
        headers,
    )
    objective_list = _as_list(list_payload)

    processed_objectives = []
    okr_rows = []
    objective_details = []
    objective_progress_histories = []

    for objective in objective_list:
        objective_id = objective.get("id")
        objective_title = objective.get("name") or text_from_rich_text(
            objective.get("nameRichText")
        )
        objective_weight = objective.get("weight", "")
        objective_progress = progress_percent(objective.get("progress"))
        kr_cells = objective.get("krCells") if isinstance(objective.get("krCells"), list) else []

        # Comment/进展 records (right-side discussion) — real progress evidence
        # even when numeric progress is 0. Group by KR (id, then name) / objective.
        comments = fetch_objective_comments(objective_id, headers)
        comments_by_kr_id: dict[str, list] = {}
        comments_by_kr_key: dict[str, list] = {}
        objective_comments: list = []
        for comment in comments:
            if comment.get("krId"):
                comments_by_kr_id.setdefault(comment["krId"], []).append(comment)
            elif comment.get("krName"):
                comments_by_kr_key.setdefault(
                    _kr_match_key(comment["krName"]), []
                ).append(comment)
            else:
                objective_comments.append(comment)

        objective_row = {
            "objectiveId": objective_id,
            "title": objective_title,
            "weight": objective_weight,
            "progress": objective_progress,
            "owner": objective.get("owner", ""),
            "ownerName": objective.get("ownerName", ""),
            "objectiveCommentsAggregated": aggregate_comments(objective_comments),
            "keyResults": [],
        }
        okr_rows.append({
            "level": "O",
            "objectiveId": objective_id,
            "objectiveTitle": objective_title,
            "objectiveWeight": objective_weight,
            "objectiveProgress": objective_progress,
            "krId": "",
            "krTitle": "",
            "krWeight": "",
            "krProgress": "",
            "krDetailsUpdatesAggregated": aggregate_comments(objective_comments),
        })

        for kr in kr_cells:
            kr_id = kr.get("id")
            detail = _unwrap(_post(
                "/data/okr/objective/findKrDetail",
                {"objId": objective_id, "krId": kr_id},
                headers,
            ))
            if not isinstance(detail, dict):
                detail = {}
            progress_history = _post(
                "/data/okr/objective/log/progressHistory",
                {"objectiveId": objective_id, "krId": kr_id},
                headers,
            )
            histories = _as_list(progress_history, key="histories")
            kr_title = (
                detail.get("content")
                or kr.get("content")
                or text_from_rich_text(detail.get("contentRichText"))
                or ""
            ).strip()
            kr_comments = (
                comments_by_kr_id.get(kr_id)
                or comments_by_kr_key.get(_kr_match_key(kr_title))
                or comments_by_kr_key.get(_kr_match_key(kr.get("content")))
                or []
            )
            aggregated = _merge_updates(
                aggregate_history(histories), aggregate_comments(kr_comments)
            )
            kr_weight = detail.get("weight", kr.get("weight", ""))
            kr_progress = progress_percent(
                detail.get("progress", kr.get("progress"))
            )
            kr_deadline = detail.get("deadline", kr.get("deadline"))

            objective_row["keyResults"].append({
                "keyResultId": kr_id,
                "title": kr_title,
                "weight": kr_weight,
                "progress": kr_progress,
                "deadline": kr_deadline,
                "progressUpdatesAggregated": aggregated,
            })
            okr_rows.append({
                "level": "KR",
                "objectiveId": objective_id,
                "objectiveTitle": objective_title,
                "objectiveWeight": objective_weight,
                "objectiveProgress": objective_progress,
                "krId": kr_id,
                "krTitle": kr_title,
                "krWeight": kr_weight,
                "krProgress": kr_progress,
                "krDeadline": kr_deadline,
                "krDetailsUpdatesAggregated": aggregated,
            })
            objective_details.append({
                "objectiveId": objective_id,
                "keyResultId": kr_id,
                "payload": detail,
            })
            objective_progress_histories.append({
                "objectiveId": objective_id,
                "keyResultId": kr_id,
                "payload": progress_history,
            })

        processed_objectives.append(objective_row)

    return {
        "source": {
            "system": "叮当OKR Dingteam Web (direct API)",
            "appId": "40707",
            "suiteId": "9242001",
            "capturedAt": datetime.now(tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            ),
        },
        "userId": user_id,
        "periodLabel": period_label,
        "period": period,
        "periods": periods,
        "objectiveList": objective_list,
        "objectiveDetails": objective_details,
        "objectiveProgressHistories": objective_progress_histories,
        "processed": {"objectives": processed_objectives, "okrRows": okr_rows},
    }


def fetch(user_id: str, period_label: str) -> dict:
    """Capture auth headers from the live Chrome tab, then fetch via direct HTTP."""
    return fetch_with_headers(user_id, period_label, capture_auth_headers())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--period-label", required=True)
    args = parser.parse_args()
    result = fetch(args.user_id, args.period_label)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
