#!/usr/bin/env python3
"""Read DingTalk OA process details through official OpenAPI.

This is a read-only fallback for cases where `dws oa approval detail` fails in
the DWS/iPaaS response parser after DingTalk has already returned data.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


CONFIG_PATH = Path.home() / ".dingtalk-skills" / "config"
TOKEN_URL = "https://oapi.dingtalk.com/gettoken"
DETAIL_URL = "https://oapi.dingtalk.com/topapi/processinstance/get"


def load_config(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("'\"")
    for key in ("DINGTALK_APP_KEY", "DINGTALK_APP_SECRET"):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def request_json(url: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def get_token(app_key: str, app_secret: str) -> str:
    query = urllib.parse.urlencode({"appkey": app_key, "appsecret": app_secret})
    payload = request_json(f"{TOKEN_URL}?{query}")
    if payload.get("errcode") != 0 or not payload.get("access_token"):
        sanitized = {k: v for k, v in payload.items() if k != "access_token"}
        raise RuntimeError(f"failed to get access token: {json.dumps(sanitized, ensure_ascii=False)}")
    return str(payload["access_token"])


def get_detail(token: str, instance_id: str) -> dict[str, Any]:
    query = urllib.parse.urlencode({"access_token": token})
    payload = request_json(f"{DETAIL_URL}?{query}", {"process_instance_id": instance_id})
    if payload.get("errcode") != 0:
        raise RuntimeError(f"detail API failed: {json.dumps(payload, ensure_ascii=False)}")
    return payload


def summarize(payload: dict[str, Any], requested_instance_id: str) -> dict[str, Any]:
    process = payload.get("process_instance") or {}
    records = process.get("operation_records") or []
    tasks = process.get("tasks") or []
    fields = process.get("form_component_values") or []
    return {
        "errcode": payload.get("errcode"),
        "errmsg": payload.get("errmsg"),
        "process_instance_id": process.get("process_instance_id") or process.get("processInstanceId") or requested_instance_id,
        "title": process.get("title"),
        "business_id": process.get("business_id"),
        "status": process.get("status"),
        "result": process.get("result"),
        "originator_userid": process.get("originator_userid"),
        "originator_dept_name": process.get("originator_dept_name"),
        "create_time": process.get("create_time"),
        "finish_time": process.get("finish_time"),
        "form_count": len(fields),
        "record_count": len(records),
        "task_count": len(tasks),
        "fields": [
            {
                "name": item.get("name"),
                "component_type": item.get("component_type"),
                "value": item.get("value"),
                "ext_value": item.get("ext_value"),
            }
            for item in fields
        ],
        "remarks": [
            {
                "date": item.get("date"),
                "operation_type": item.get("operation_type"),
                "operation_result": item.get("operation_result"),
                "userid": item.get("userid"),
                "remark": item.get("remark"),
                "attachments": item.get("attachments"),
            }
            for item in records
            if item.get("remark") or item.get("attachments")
        ],
        "running_tasks": [
            {
                "taskid": item.get("taskid"),
                "userid": item.get("userid"),
                "task_status": item.get("task_status"),
                "task_result": item.get("task_result"),
                "url": item.get("url"),
            }
            for item in tasks
            if item.get("task_status") == "RUNNING"
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read DingTalk OA approval detail through OpenAPI")
    parser.add_argument("--instance-id", required=True, help="DingTalk processInstanceId")
    parser.add_argument("--config", default=str(CONFIG_PATH), help="config file with DINGTALK_APP_KEY/SECRET")
    parser.add_argument("--summary", action="store_true", help="print review-oriented summary instead of raw API payload")
    args = parser.parse_args()

    config = load_config(Path(args.config).expanduser())
    app_key = config.get("DINGTALK_APP_KEY")
    app_secret = config.get("DINGTALK_APP_SECRET")
    if not app_key or not app_secret:
        print("missing DINGTALK_APP_KEY or DINGTALK_APP_SECRET", file=sys.stderr)
        return 2

    token = get_token(app_key, app_secret)
    payload = get_detail(token, args.instance_id)
    output = summarize(payload, args.instance_id) if args.summary else payload
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
