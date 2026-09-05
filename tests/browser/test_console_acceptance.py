from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest


pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("WORKBENCH_BROWSER_TESTS") != "1",
        reason="set WORKBENCH_BROWSER_TESTS=1 to run console browser acceptance",
    ),
]

sync_api = pytest.importorskip("playwright.sync_api")

BASE_URL = os.environ.get("CONSOLE_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
SNAPSHOT = "2026-08-31T19:00:00Z"


def _meta(total: int, page_size: int = 20) -> dict[str, object]:
    return {
        "page": 1,
        "page_size": page_size,
        "total": total,
        "next_cursor": "",
        "has_more": total > page_size,
        "snapshot_at": SNAPSHOT,
    }


def _task_items(count: int) -> list[dict[str, object]]:
    return [
        {
            "id": str(index + 1),
            "title": f"Scale task {index + 1}",
            "status": "ready",
            "category": "planning",
            "priority": "P1",
            "risk_level": "low",
            "owner": "Derek",
            "progress_count": index % 4,
            "progress_total": 4,
            "progress_ratio": (index % 4) * 25,
            "todo_count": 4,
            "current_state": "进行中",
            "next_step": "继续验收",
        }
        for index in range(count)
    ]


def _history_items(count: int) -> list[dict[str, object]]:
    return [
        {
            "id": str(index + 1),
            "occurred_at": SNAPSHOT,
            "title": f"Scale history {index + 1}",
            "type": "task",
            "kind": "task",
            "status": "done",
            "summary": "可核验摘要",
            "actor": "Derek",
            "detail_url": f"/attempts/{index + 1}",
            "input": "输入摘要",
            "output": "输出摘要",
        }
        for index in range(count)
    ]


def _feedback_items(count: int) -> list[dict[str, object]]:
    return [
        {
            "id": str(index + 1),
            "feedback_key": f"feedback-{index + 1}",
            "attempt_id": str(index + 1),
            "status": "pending",
            "processing_status": "pending",
            "rating": "3",
            "comment": f"Scale feedback {index + 1}",
            "context": "可核验上下文",
            "created_at": SNAPSHOT,
            "summary": "反馈摘要",
            "references": [],
            "batch_id": "",
            "processing_task_id": "",
        }
        for index in range(count)
    ]


def _fixture_response(path: str, total: int) -> dict[str, object] | None:
    if path.endswith("/api/console/tasks/sent-todos"):
        return {"items": [], "meta": _meta(0)}
    if path.endswith("/api/console/tasks"):
        return {
            "items": _task_items(min(total, 20)),
            "filters": {"categories": ["planning"], "task_states": ["ready"]},
            "meta": _meta(total),
        }
    if path.endswith("/api/console/history"):
        labels = [f"{index:02d}:00" for index in range(24)]
        return {
            "items": _history_items(min(total, 20)),
            "chart": {"labels": labels, "series": [{"name": "task", "data": [1] * 24}], "total": total, "range": "最近 24 小时"},
            "meta": _meta(total),
        }
    if path.endswith("/api/console/feedback"):
        return {"items": _feedback_items(min(total, 20)), "pending_count": total, "meta": _meta(total)}
    return None


def _install_scale_routes(page, total: int) -> None:
    def handle(route) -> None:
        request_path = urlsplit(route.request.url).path
        payload = _fixture_response(request_path, total)
        if payload is None:
            route.continue_()
            return
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload, ensure_ascii=False),
        )

    page.route("**/api/console/**", handle)


def _install_interaction_routes(page) -> None:
    def handle(route) -> None:
        path = urlsplit(route.request.url).path
        if path.endswith("/api/console/settings/connectors"):
            payload = {"item": {"section": "connectors", "fields": {}, "wechat": {"state": "ready"}}, "meta": {"snapshot_at": SNAPSHOT}}
        elif path.endswith("/api/console/settings/prompts"):
            payload = {"item": {"section": "prompts", "fields": {"user_template": "Reply to {{principal}} in {{conversation}}.", "developer_template": "Review for {{principal}}."}, "preview": {"user": "Reply to Derek in Friday.", "developer": "Review for Derek."}}, "meta": {"snapshot_at": SNAPSHOT}}
        elif path.endswith("/api/console/settings/audit-rules"):
            payload = {"item": {"section": "audit-rules", "fields": {"template": "Check {{principal}}."}, "preview": {"template": "Check Derek.", "consumer": "Consumer: Check Derek.", "audit": "Audit: Check Derek."}}, "meta": {"snapshot_at": SNAPSHOT}}
        elif path.endswith("/api/console/settings/agent-runtime"):
            payload = {"item": {"section": "agent-runtime", "fields": {"CEO_CODEX_MODEL": "gpt-5.6-sol", "CEO_AGENT_RUNTIME_ROUTES": "codex_api", "CEO_CODEX_API_BASE_URL": "https://api.example.test", "CEO_CODEX_API_MODEL": "MiniMax-M2.5", "CEO_CODEX_API_KEY": "saved-token"}, "secrets": ["CEO_CODEX_API_KEY"]}, "meta": {"snapshot_at": SNAPSHOT}}
        elif path.endswith("/api/console/attention"):
            payload = {"items": [{"category": "Service error", "root_cause": "database is locked", "context": "worker", "severity": "error", "count": 4, "summary": "database is locked", "error": "database is locked", "detail_label": "错误", "detail": "database is locked", "updated_at": SNAPSHOT, "records": [{"detail_url": "/attempts/12830"}]}], "meta": _meta(1)}
        elif path.endswith("/api/console/wechat/conversations"):
            payload = {"items": [{"account_id": "wx-account", "target_type": "direct", "target_id": "melody", "conversation_id": "melody", "display_name": "Melody", "trigger_mode": "every_inbound_text", "enabled": True}], "meta": _meta(1)}
        elif path.endswith("/api/console/wechat/targets"):
            payload = {"account_id": "wx-account", "items": [{"account_id": "wx-account", "target_type": "group", "target_id": "group-1", "conversation_id": "group-1", "display_name": "New group", "trigger_mode": "mention_current_account", "enabled": True}], "meta": _meta(1, 50)}
        elif path.endswith("/api/console/wechat/reply-scope") and route.request.method == "POST":
            payload = {"ok": True, "message": "已保存", "meta": {"updated_at": SNAPSHOT}}
        else:
            route.continue_()
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))

    page.route("**/api/console/**", handle)


def _install_feedback_iteration_routes(page, mutations: list[str], mutation_recorder: dict[str, bool]) -> None:
    enabled = True
    candidate = {
        "id": 2, "skill_id": 1, "revision_number": 2, "content": "---\nname: ceo-feedback-iteration\ndescription: Candidate\nmetadata:\n  managed_by: ceo-agent-service\n---\nCandidate", "sha256": "candidate-sha", "parent_revision_id": 1, "source": "user_edit", "created_at": SNAPSHOT,
    }

    def handle(route) -> None:
        nonlocal enabled
        path = urlsplit(route.request.url).path
        method = route.request.method
        if mutation_recorder["armed"] and method != "GET" and (
            path.startswith("/api/console/feedback")
            or path.startswith("/api/console/agent")
            or path.startswith("/api/console/codex")
            or path.startswith("/api/console/sessions")
        ):
            mutations.append(f"{method} {path}")
            route.fulfill(status=409, content_type="application/json", body=json.dumps({"code": "feedback_iteration_disabled", "message": "反馈迭代已关闭"}, ensure_ascii=False))
            return
        if path.endswith("/api/console/settings/skills/features"):
            payload = {"features": []}
        elif path.endswith("/api/console/settings/managed-skills"):
            payload = {"items": [{"id": 1, "name": "ceo-feedback-iteration", "display_name": "反馈迭代", "created_at": SNAPSHOT}]}
        elif path.endswith("/api/console/settings/managed-skills/1/revisions") and method == "GET":
            payload = {"items": [{"id": 1, "skill_id": 1, "revision_number": 1, "content": candidate["content"], "sha256": "base-sha", "parent_revision_id": None, "source": "baseline", "created_at": SNAPSHOT}] + ([candidate] if candidate.get("saved") else [])}
        elif path.endswith("/api/console/settings/managed-skills/1/revisions") and method == "POST":
            candidate["saved"] = True
            payload = candidate
        elif path.endswith("/api/console/settings/runtime-skill-configs/current"):
            payload = {"pending_or_active": {"id": 24, "parent_id": None, "status": "active", "created_at": SNAPSHOT, "bindings": [{"skill_id": 1, "revision_id": 1, "enabled": True, "load_order": 0, "purpose": "system"}]}, "active": {"id": 24, "parent_id": None, "status": "active", "created_at": SNAPSHOT, "bindings": [{"skill_id": 1, "revision_id": 1, "enabled": True, "load_order": 0, "purpose": "system"}]}}
        elif path.endswith("/api/console/settings/runtime-skill-configs") and method == "POST":
            payload = {"id": 25, "parent_id": 24, "status": "pending_restart", "created_at": SNAPSHOT, "bindings": [{"skill_id": 1, "revision_id": 2, "enabled": True, "load_order": 0, "purpose": "system"}]}
        elif "/api/console/settings/runtime-skill-configs/" in path and path.endswith("/load-receipts"):
            payload = {"items": []}
        elif path.endswith("/api/console/settings/feedback-iteration") and method == "GET":
            payload = {"enabled": enabled, "config_id": 25}
        elif path.endswith("/api/console/settings/feedback-iteration") and method == "POST":
            enabled = False
            payload = {"enabled": False, "config_id": 26, "status": "pending_restart"}
        elif path.endswith("/api/console/feedback"):
            payload = {"items": _feedback_items(1), "pending_count": 1, "meta": _meta(1)}
        else:
            route.continue_()
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))

    page.route("**/api/console/**", handle)


def test_feedback_iteration_candidate_activation_then_disabled_control_has_no_mutation():
    with _browser() as playwright:
        browser = _launch(playwright)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            mutations: list[str] = []
            mutation_recorder = {"armed": False}
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            _install_feedback_iteration_routes(page, mutations, mutation_recorder)
            page.goto(f"{BASE_URL}/settings?tab=skills", wait_until="domcontentloaded")
            _wait_for_root(page)
            page.get_by_role("button", name="新建修订").click()
            page.get_by_label("Skill 正文").fill("---\nname: ceo-feedback-iteration\ndescription: Candidate\nmetadata:\n  managed_by: ceo-agent-service\n---\nCandidate")
            page.get_by_role("button", name="保存修订").click()
            page.get_by_text("已保存 revision 2", exact=True).wait_for(state="visible")
            page.get_by_role("button", name="下次启动启用 revision 2").click()
            page.get_by_text("配置已更新，等待服务重启", exact=True).wait_for(state="visible")
            page.evaluate("fetch('/api/console/settings/feedback-iteration', {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify({enabled: false})})")
            page.goto(f"{BASE_URL}/user-feedback", wait_until="domcontentloaded")
            _wait_for_root(page)
            button = page.get_by_role("button", name="处理反馈")
            button.wait_for(state="visible")
            assert button.is_disabled()
            assert page.get_by_text("反馈迭代已关闭；启用后可处理未解决反馈", exact=True).is_visible()
            mutation_recorder["armed"] = True
            button.click(force=True)
            page.wait_for_timeout(100)
            assert mutations == []
            assert not page_errors, page_errors
        finally:
            browser.close()


def _install_route_matrix_routes(page) -> None:
    def handle(route) -> None:
        path = urlsplit(route.request.url).path
        if not path.startswith("/api/console/"):
            route.continue_()
            return
        meta = _meta(0)
        if path.endswith("/api/console/status"):
            payload = {
                "item": {
                    "service": {"state": "running", "ok": True},
                    "summary": {"pending": 0, "processing": 0, "failed": 0, "retryable": 0, "attention": 0},
                    "queues": [],
                    "components": [],
                    "connectors": {},
                },
                "meta": meta,
            }
        elif path.endswith("/api/console/attention"):
            payload = {"items": [], "meta": meta}
        elif "/api/console/history/" in path or "/api/console/meeting-attempts/" in path or "/api/console/oa-approvals/" in path:
            payload = {
                "item": {
                    "id": "12830",
                    "title": "验收详情",
                    "status": "done",
                    "input": "输入",
                    "decision": "决定",
                    "output": "输出",
                    "reviewer_feedback": "",
                    "corrected_reply": "",
                    "runtime": {},
                },
                "meta": meta,
            }
        elif "/api/console/codex/sessions/" in path:
            payload = {"item": {"available": True, "message": "执行记录可用", "events": [], "related_attempts": []}, "meta": meta}
        else:
            payload = {"items": [], "meta": meta}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))

    page.route("**/api/console/**", handle)


def _browser():
    return sync_api.sync_playwright()


def _launch(playwright):
    return playwright.chromium.launch(headless=True, channel="chrome")


def _wait_for_root(page) -> None:
    page.locator(".console-root").wait_for(state="visible", timeout=10_000)


def _active_focus_snapshot(page) -> dict[str, object]:
    return page.evaluate(
        """() => {
          const element = document.activeElement;
          const rect = element instanceof HTMLElement ? element.getBoundingClientRect() : null;
          return { tag: element?.tagName || '', id: element?.id || '', role: element?.getAttribute('role') || '',
            name: element?.getAttribute('aria-label') || element?.getAttribute('name') || '',
            visible: Boolean(rect && rect.width > 0 && rect.height > 0) };
        }"""
    )


@pytest.mark.parametrize(
    "viewport",
    [
        pytest.param({"width": 1280, "height": 720}, id="desktop-1280"),
        pytest.param({"width": 390, "height": 844}, id="mobile-390"),
    ],
)
def test_console_keyboard_and_aria_acceptance(viewport):
    with _browser() as playwright:
        browser = _launch(playwright)
        try:
            page = browser.new_page(viewport=viewport)
            _install_interaction_routes(page)
            for path in ["/", "/history", "/tasks", "/user-feedback", "/settings?tab=info"]:
                page.goto(f"{BASE_URL}{path}", wait_until="domcontentloaded")
                _wait_for_root(page)
                page.locator("body").focus()
                for _ in range(24):
                    page.keyboard.press("Tab")
                    snapshot = _active_focus_snapshot(page)
                    if snapshot["visible"] and snapshot["tag"] != "BODY":
                        break
                assert snapshot["visible"] and snapshot["tag"] != "BODY", path

            if viewport["width"] <= 600:
                mobile_page = browser.new_page(viewport=viewport)
                try:
                    _install_scale_routes(mobile_page, 1)
                    mobile_page.goto(f"{BASE_URL}/tasks?page_size=20", wait_until="domcontentloaded")
                    _wait_for_root(mobile_page)
                    mobile_page.locator(".tasks-table tbody tr").first.wait_for(state="visible", timeout=30_000)
                    assert mobile_page.get_by_role("link", name="查看详情 Scale task 1").is_visible()
                    assert mobile_page.locator(".tasks-table .status-success[data-status='ready']").is_visible()
                    assert mobile_page.get_by_text("4 个 TODO", exact=True).is_visible()
                    assert mobile_page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")
                finally:
                    mobile_page.close()

            page.goto(f"{BASE_URL}/settings?tab=prompts&prompt=user&view=template", wait_until="domcontentloaded")
            _wait_for_root(page)
            editor = page.locator("#prompt-template")
            editor.wait_for(state="visible", timeout=30_000)
            editor.focus()
            page.keyboard.press("Tab")
            assert page.locator("button[name='settings-save']").evaluate("element => element === document.activeElement")

            page.goto(f"{BASE_URL}/settings?tab=audit-rules&rule=template&view=template", wait_until="domcontentloaded")
            _wait_for_root(page)
            page.locator("#audit-rules-panel").wait_for(state="visible", timeout=30_000)
            assert page.get_by_role("tablist", name="Audit Rule sections").count() == 1
            assert page.get_by_role("tablist", name="Audit Rule view").count() == 1
            assert page.locator("label[for='audit-rules-template']").inner_text() == "Configurable rules"
            assert page.locator("#audit-rules-template").get_attribute("aria-invalid") == "false"

            page.goto(f"{BASE_URL}/settings?tab=agent-runtime", wait_until="domcontentloaded")
            _wait_for_root(page)
            secret_toggle = page.locator("button[aria-controls='codex-api-token']")
            secret_toggle.wait_for(state="visible", timeout=30_000)
            secret_toggle.click()
            assert secret_toggle.get_attribute("aria-pressed") == "true"
            assert page.locator("button[aria-controls='codex-api-token']").evaluate("element => element === document.activeElement")

            page.goto(f"{BASE_URL}/settings?tab=connectors&connector=wechat", wait_until="domcontentloaded")
            _wait_for_root(page)
            search = page.locator("#wechat-target-search")
            search.wait_for(state="visible", timeout=30_000)
            search.fill("New")
            search.press("Enter")
            page.get_by_text("New group", exact=True).wait_for(state="visible")
            page.locator(".wechat-target-row").last.locator("input[type=checkbox]").check()
            save = page.get_by_role("button", name="保存回复范围")
            save.click()
            page.get_by_text("回复范围已保存", exact=True).wait_for(state="visible")

            page.goto(f"{BASE_URL}/attention", wait_until="domcontentloaded")
            _wait_for_root(page)
            badge = page.locator(".attention-count-badge")
            badge.wait_for(state="visible")
            assert badge.inner_text() == "4"
            toggle = page.locator(".attention-panel .details-toggle").first
            toggle.wait_for(state="visible", timeout=30_000)
            toggle.focus()
            page.keyboard.press("Enter")
            assert toggle.get_attribute("aria-expanded") == "true"
            assert page.locator(".attention-details").is_visible()
            page.keyboard.press("Enter")
            assert toggle.get_attribute("aria-expanded") == "false"
        finally:
            browser.close()


def test_console_list_scale_is_paginated_and_interactive():
    totals = [1, 10, 100, 1000, 10000]
    measurements: list[dict[str, object]] = []
    with _browser() as playwright:
        browser = _launch(playwright)
        try:
            for total in totals:
                for path, input_id, row_selector in [
                    ("/history", "#history-search-input", ".attempt-feed .attempt-item"),
                    ("/tasks", "#task-search-input", ".tasks-table tbody tr"),
                    ("/user-feedback", "#feedback-search-input", "table[aria-label='用户反馈列表'] tbody tr"),
                ]:
                    page = browser.new_page(viewport={"width": 1280, "height": 720})
                    _install_scale_routes(page, total)
                    started = time.perf_counter()
                    page.goto(f"{BASE_URL}{path}?page_size=20", wait_until="domcontentloaded")
                    _wait_for_root(page)
                    rows = page.locator(row_selector)
                    rows.first.wait_for(state="visible", timeout=30_000)
                    visible_at = time.perf_counter()
                    before_nodes = page.locator("*").count()
                    search = page.locator(input_id)
                    search.fill("needle")
                    rows.first.wait_for(state="visible", timeout=30_000)
                    interactive_at = time.perf_counter()
                    after_nodes = page.locator("*").count()
                    measurements.append({
                        "total": total,
                        "page": path,
                        "visible_rows": rows.count(),
                        "dom_nodes": after_nodes,
                        "dom_nodes_before_filter": before_nodes,
                        "dom_content_loaded_ms": round((visible_at - started) * 1000, 1),
                        "first_interaction_ms": round((interactive_at - visible_at) * 1000, 1),
                        "scroll_height": page.evaluate("document.documentElement.scrollHeight"),
                    })
                    assert rows.count() <= 20, (total, path, rows.count())
                    assert after_nodes < 2500, (total, path, after_nodes)
                    page.close()
        finally:
            browser.close()
    print(json.dumps(measurements, ensure_ascii=False), file=sys.stderr)
    assert {measurement["total"] for measurement in measurements} == set(totals)


def test_console_remaining_route_matrix_renders_without_page_errors():
    routes = [
        "/history/attempts/12830",
        "/history/meeting-attempts/1",
        "/history/oa-approvals/1",
        "/attempts/12830",
        "/attempts/12830/execution/consumer",
        "/meeting-attempts/1",
        "/oa-approvals/1",
        "/tutorial",
        "/notifications",
        "/codex",
        "/codex/session-1",
        "/wechat/review",
        "/wechat/memory-review",
        "/wechat/deliveries",
        "/wechat/conversations",
    ]
    with _browser() as playwright:
        browser = _launch(playwright)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            _install_route_matrix_routes(page)
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            for path in routes:
                page.goto(f"{BASE_URL}{path}", wait_until="domcontentloaded")
                page.locator("#console-page-title").wait_for(state="visible", timeout=30_000)
                assert page.locator("#console-page-title").inner_text().strip(), path
                assert not page.locator("[role='alert']").is_visible(), path
            assert not page_errors, page_errors
        finally:
            browser.close()


def test_browser_qa_inventory_is_documented():
    inventory = Path(__file__).resolve().parents[2] / "docs" / "superpowers" / "plans" / "2026-08-30-ceo-agent-service-workspace-convergence.md"
    assert inventory.is_file()
    content = inventory.read_text(encoding="utf-8")
    assert "VoiceOver" in content
    assert "390px" in content
    assert "10000" in content
