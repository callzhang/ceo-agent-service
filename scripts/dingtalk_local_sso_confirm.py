#!/usr/bin/env /usr/bin/python3
"""Confirm the Dingteam login prompt in the already logged-in DingTalk app."""
from __future__ import annotations

import time


DINGTALK_BUNDLE_ID = "com.alibaba.DingTalkMac"
CONFIRM_URL_FRAGMENT = "login.dingtalk.com/oauth2/local_confirm.htm"
CONFIRM_APP_NAME = "叮当OKR"
CONFIRM_TIMEOUT_SECONDS = 35
RETURN_KEY_CODE = 36


def _is_dingteam_confirmation(url: str, text: str) -> bool:
    return CONFIRM_URL_FRAGMENT in url and CONFIRM_APP_NAME in text


def _attribute(element, name):
    from ApplicationServices import AXUIElementCopyAttributeValue

    error, value = AXUIElementCopyAttributeValue(element, name, None)
    return value if error == 0 else None


def _element_text(element) -> str:
    values = []
    for name in ("AXTitle", "AXDescription", "AXValue", "AXHelp"):
        value = _attribute(element, name)
        if isinstance(value, str):
            values.append(value)
    return " ".join(values)


def _subtree_text(element, *, limit: int = 200) -> str:
    parts = []
    stack = [element]
    inspected = 0
    while stack and inspected < limit:
        current = stack.pop()
        inspected += 1
        text = _element_text(current)
        if text:
            parts.append(text)
        stack.extend(_attribute(current, "AXChildren") or [])
    return " ".join(parts)


def _find_confirmation(app):
    roots = []
    focused = _attribute(app, "AXFocusedUIElement")
    if focused is not None:
        roots.append(focused)
    roots.extend(_attribute(app, "AXWindows") or [])

    stack = list(roots)
    seen = set()
    inspected = 0
    while stack and inspected < 1_000:
        element = stack.pop()
        identity = id(element)
        if identity in seen:
            continue
        seen.add(identity)
        inspected += 1
        url = _attribute(element, "AXURL")
        url_text = str(url) if url is not None else ""
        text = _element_text(element)
        children = _attribute(element, "AXChildren") or []
        if _is_dingteam_confirmation(url_text, text):
            return element
        if CONFIRM_URL_FRAGMENT in url_text:
            descendant_text = _subtree_text(element)
            if _is_dingteam_confirmation(url_text, f"{text} {descendant_text}"):
                return element
        stack.extend(children)
    return None


def main() -> int:
    from AppKit import NSRunningApplication
    from ApplicationServices import AXUIElementCreateApplication
    import Quartz

    apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_(
        DINGTALK_BUNDLE_ID
    )
    if not apps:
        raise RuntimeError("DingTalk is not running")
    pid = int(apps[0].processIdentifier())
    app = AXUIElementCreateApplication(pid)
    deadline = time.monotonic() + CONFIRM_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _find_confirmation(app) is not None:
            for key_down in (True, False):
                event = Quartz.CGEventCreateKeyboardEvent(
                    None, RETURN_KEY_CODE, key_down
                )
                Quartz.CGEventPostToPid(pid, event)
                time.sleep(0.05)
            return 0
        time.sleep(0.1)
    raise RuntimeError("Dingteam local login confirmation was not found")


if __name__ == "__main__":
    raise SystemExit(main())
