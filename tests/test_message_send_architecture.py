"""Architecture guard for service-owned human-facing text delivery."""
from __future__ import annotations

import ast
from pathlib import Path

from app.dws_client import DwsClient
from app.org_cache import CachedDwsClient, CachedOrgDirectory
from app.store import AutoReplyStore


APP_ROOT = Path(__file__).parents[1] / "app"

DINGTALK_SEND_METHODS = {
    "send_direct_message_by_bot",
    "send_group_message_by_bot",
    "send_message",
    "send_reply_to_trigger",
    "reply_message",
}

# The approved carrier set is the scanner allowlist.  The coverage registry
# below must have exactly these keys, so no new transport/facade can be allowed
# without naming the test that exercises its text/argument pair.
APPROVED_SENDER_PATHS = frozenset({
    "app/dws_client.py:DwsClient.send_message",
    "app/dws_client.py:DwsClient.reply_message",
    "app/dws_client.py:DwsClient.send_reply_to_trigger",
    "app/dws_client.py:DwsClient.send_reply_to_trigger_chunks",
    "app/dws_client.py:DwsClient.send_direct_message_by_bot",
    "app/dws_client.py:DwsClient.send_group_message_by_bot",
    "app/org_cache.py:CachedDwsClient.send_message",
    "app/org_cache.py:CachedDwsClient.reply_message",
    "app/org_cache.py:CachedDwsClient.send_reply_to_trigger",
    "app/org_cache.py:CachedDwsClient.send_direct_message_by_bot",
    "app/org_cache.py:CachedDwsClient.send_group_message_by_bot",
    "app/service_message_sender.py:ServiceMessageSender.send_dingtalk_prepared",
    "app/service_message_sender.py:ServiceMessageSender.send_dingtalk_reply_to_trigger_prepared",
    "app/service_message_sender.py:ServiceMessageSender.send_wechat_prepared",
    "app/wechat/accessibility.py:WechatSender.send",
    "app/wechat/sender_ipc.py:WechatSenderRpcService.dispatch",
    "app/wechat/sender_ipc.py:WechatSenderClient.send",
})


# Each value states the test module, its exact test function, and the public
# entry call made by that test.  Some facade tests intentionally call the safe
# convenience method, which reaches the listed prepared transport internally.
FOCUSED_SENDER_PATH_TESTS = {
    "app/service_message_sender.py:ServiceMessageSender.send_dingtalk_prepared": (
        "tests/test_service_message_sender.py",
        "test_dingtalk_prepared_dispatch_rejects_unpersisted_and_tampered_messages",
        "send_dingtalk_prepared",
    ),
    "app/service_message_sender.py:ServiceMessageSender.send_dingtalk_reply_to_trigger_prepared": (
        "tests/test_service_message_sender.py",
        "test_native_reply_facade_reuses_durable_receipt_after_partial_delivery",
        "send_dingtalk_reply_to_trigger_prepared",
    ),
    "app/service_message_sender.py:ServiceMessageSender.send_wechat_prepared": (
        "tests/test_service_message_sender.py",
        "test_wechat_prepared_dispatch_rejects_unpersisted_and_tampered_messages",
        "send_wechat_prepared",
    ),
    "app/dws_client.py:DwsClient.send_message": (
        "tests/test_message_send_architecture.py",
        "test_dws_raw_carrier_coverage_anchors",
        "send_message",
    ),
    "app/dws_client.py:DwsClient.reply_message": (
        "tests/test_message_send_architecture.py",
        "test_dws_raw_carrier_coverage_anchors",
        "reply_message",
    ),
    "app/dws_client.py:DwsClient.send_reply_to_trigger": (
        "tests/test_dws_client.py",
        "test_send_reply_to_trigger_prefers_native_reply_over_group_at_send",
        "send_reply_to_trigger",
    ),
    "app/dws_client.py:DwsClient.send_reply_to_trigger_chunks": (
        "tests/test_dws_client.py",
        "test_send_reply_to_trigger_chunks_splits_long_text_and_extracts_recall_key",
        "send_reply_to_trigger_chunks",
    ),
    "app/dws_client.py:DwsClient.send_direct_message_by_bot": (
        "tests/test_message_send_architecture.py",
        "test_dws_raw_carrier_coverage_anchors",
        "send_direct_message_by_bot",
    ),
    "app/dws_client.py:DwsClient.send_group_message_by_bot": (
        "tests/test_message_send_architecture.py",
        "test_dws_raw_carrier_coverage_anchors",
        "send_group_message_by_bot",
    ),
    "app/org_cache.py:CachedDwsClient.send_message": (
        "tests/test_message_send_architecture.py",
        "test_cached_dws_forwarder_coverage_anchors",
        "send_message",
    ),
    "app/org_cache.py:CachedDwsClient.reply_message": (
        "tests/test_message_send_architecture.py",
        "test_cached_dws_forwarder_coverage_anchors",
        "reply_message",
    ),
    "app/org_cache.py:CachedDwsClient.send_reply_to_trigger": (
        "tests/test_message_send_architecture.py",
        "test_cached_dws_forwarder_coverage_anchors",
        "send_reply_to_trigger",
    ),
    "app/org_cache.py:CachedDwsClient.send_direct_message_by_bot": (
        "tests/test_message_send_architecture.py",
        "test_cached_dws_forwarder_coverage_anchors",
        "send_direct_message_by_bot",
    ),
    "app/org_cache.py:CachedDwsClient.send_group_message_by_bot": (
        "tests/test_message_send_architecture.py",
        "test_cached_dws_forwarder_coverage_anchors",
        "send_group_message_by_bot",
    ),
    "app/wechat/accessibility.py:WechatSender.send": (
        "tests/wechat/test_accessibility.py",
        "test_verified_binding_sends",
        "send",
    ),
    "app/wechat/sender_ipc.py:WechatSenderRpcService.dispatch": (
        "tests/wechat/test_sender_ipc.py",
        "test_sender_rpc_exposes_only_bounded_accessibility_operations",
        "dispatch",
    ),
    "app/wechat/sender_ipc.py:WechatSenderClient.send": (
        "tests/wechat/test_sender_ipc.py",
        "test_sender_client_round_trip_over_owner_only_socket",
        "send",
    ),
}


def _is_wechat_raw_receiver(node: ast.AST) -> bool:
    """Identify known raw Accessibility carriers without matching callbacks."""
    if isinstance(node, ast.Name):
        return node.id in {"runner", "wechat"}
    if isinstance(node, ast.Attribute):
        return node.attr in {"runner", "wechat"}
    if isinstance(node, ast.Call):
        constructor = node.func
        return (
            isinstance(constructor, ast.Name)
            and constructor.id == "WechatSenderClient"
        ) or (
            isinstance(constructor, ast.Attribute)
            and constructor.attr == "WechatSenderClient"
        )
    return False


def _is_wechat_runner_send(node: ast.Call) -> bool:
    """Identify raw WeChat sends without matching callback attributes."""
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "send":
        return False
    return _is_wechat_raw_receiver(node.func.value)


def _dynamic_provider_method(node: ast.Call) -> tuple[ast.AST, str] | None:
    """Return a dynamic raw-provider call, never a mere getattr callback value."""
    if not isinstance(node.func, ast.Call):
        return None
    getter = node.func
    if (
        not isinstance(getter.func, ast.Name)
        or getter.func.id != "getattr"
        or len(getter.args) < 2
        or not isinstance(getter.args[1], ast.Constant)
        or not isinstance(getter.args[1].value, str)
    ):
        return None
    return getter.args[0], getter.args[1].value


def _relative(source: Path, app_root: Path) -> str:
    return source.relative_to(app_root.parent).as_posix()


class _CallOwners(ast.NodeVisitor):
    """Collect calls with their exact enclosing module/class/method target."""

    def __init__(self, relative: str) -> None:
        self.relative = relative
        self.classes: list[str] = []
        self.functions: list[str] = []
        self.calls: list[tuple[ast.Call, str]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.classes.append(node.name)
        self.generic_visit(node)
        self.classes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    def visit_Call(self, node: ast.Call) -> None:
        class_name = ".".join(self.classes) or "<module>"
        function_name = ".".join(self.functions) or "<module>"
        self.calls.append((node, f"{self.relative}:{class_name}.{function_name}"))
        self.generic_visit(node)


def _calls_with_owners(tree: ast.AST, relative: str) -> list[tuple[ast.Call, str]]:
    visitor = _CallOwners(relative)
    visitor.visit(tree)
    return visitor.calls


def _resolve_target_definition(app_root: Path, target: str) -> ast.FunctionDef | None:
    source_path, qualified_method = target.split(":", maxsplit=1)
    class_name, method_name = qualified_method.rsplit(".", maxsplit=1)
    source = app_root.parent / source_path
    if not source.is_file():
        return None
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        for member in node.body:
            if isinstance(member, ast.FunctionDef) and member.name == method_name:
                return member
    return None


def _resolve_test_function(
    repo_root: Path, path: str, name: str,
) -> tuple[ast.Module, ast.FunctionDef] | None:
    source = repo_root / path
    if not source.is_file():
        return None
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    function = next(
        (node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name),
        None,
    )
    return (tree, function) if function is not None else None


def _terminal_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _function_calls_target(
    module: ast.Module, function: ast.FunctionDef, target: str,
) -> bool:
    _source_path, qualified_method = target.split(":", maxsplit=1)
    expected_class, expected_method = qualified_method.rsplit(".", maxsplit=1)
    compatible_classes = {expected_class}
    changed = True
    while changed:
        changed = False
        for node in module.body:
            if not isinstance(node, ast.ClassDef) or node.name in compatible_classes:
                continue
            if any(_terminal_name(base) in compatible_classes for base in node.bases):
                compatible_classes.add(node.name)
                changed = True
    receiver_types: dict[str, str] = {}
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and (constructor := _terminal_name(node.value.func)) in compatible_classes
        ):
            for target_node in node.targets:
                if isinstance(target_node, ast.Name):
                    receiver_types[target_node.id] = constructor
    for node in ast.walk(function):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != expected_method:
            continue
        receiver = node.func.value
        if (
            isinstance(receiver, ast.Name)
            and receiver_types.get(receiver.id) in compatible_classes
        ):
            return True
        if (
            isinstance(receiver, ast.Call)
            and _terminal_name(receiver.func) in compatible_classes
        ):
            return True
    return False


def _coverage_violations(
    approved_paths, coverage, *, app_root: Path = APP_ROOT,
) -> list[str]:
    approved = set(approved_paths)
    covered = set(coverage)
    violations = [f"missing coverage:{target}" for target in sorted(approved - covered)]
    violations.extend(f"unexpected coverage:{target}" for target in sorted(covered - approved))
    repo_root = app_root.parent
    for target in sorted(approved & covered):
        if _resolve_target_definition(app_root, target) is None:
            violations.append(f"missing target:{target}")
            continue
        test_path, test_name, _entry_method = coverage[target]
        resolved_test = _resolve_test_function(repo_root, test_path, test_name)
        if resolved_test is None:
            violations.append(f"missing test:{target}")
        else:
            test_module, test_function = resolved_test
            if not _function_calls_target(test_module, test_function, target):
                violations.append(f"missing exact call:{target}")
    return violations


def _raw_send_violations(app_root: Path = APP_ROOT) -> list[str]:
    violations: list[str] = []
    for source in sorted(app_root.rglob("*.py")):
        relative = _relative(source, app_root)
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node, owner in _calls_with_owners(tree, relative):
            dynamic = _dynamic_provider_method(node)
            if dynamic is not None:
                receiver, dynamic_method = dynamic
                if (
                    dynamic_method in DINGTALK_SEND_METHODS
                    and owner not in APPROVED_SENDER_PATHS
                ):
                    violations.append(f"{owner}:{node.lineno}:{dynamic_method}")
                elif (
                    dynamic_method == "send"
                    and _is_wechat_raw_receiver(receiver)
                    and owner not in APPROVED_SENDER_PATHS
                ):
                    violations.append(f"{owner}:{node.lineno}:runner.send")
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            method = node.func.attr
            if method in DINGTALK_SEND_METHODS and owner not in APPROVED_SENDER_PATHS:
                violations.append(f"{owner}:{node.lineno}:{method}")
                continue
            # The Accessibility/IPC runner accepts a plain string. Only the
            # unified facade and IPC adapter may call a runner's raw send method.
            if _is_wechat_runner_send(node) and owner not in APPROVED_SENDER_PATHS:
                violations.append(f"{owner}:{node.lineno}:runner.send")
    return violations


def test_first_party_message_senders_do_not_bypass_service_message_sender():
    assert _raw_send_violations() == []


def test_dws_raw_carrier_coverage_anchors(monkeypatch) -> None:
    client = DwsClient(dws_bin="dws", ding_robot_code="robot-1")
    commands: list[list[str]] = []
    monkeypatch.setattr(client, "run_json", lambda command: commands.append(command) or {})

    client.send_message("cid-1", "body")
    client.reply_message("cid-1", "msg-1", "open-1", "body")
    client.send_direct_message_by_bot("user-1", "body")
    client.send_group_message_by_bot("cid-1", "body")

    assert [command[3] for command in commands] == ["send", "reply", "send-by-bot", "send-by-bot"]


def test_cached_dws_forwarder_coverage_anchors(tmp_path) -> None:
    class RawDws:
        def send_message(self, *args, **kwargs):
            return args, kwargs

        def reply_message(self, *args, **kwargs):
            return args, kwargs

        def send_reply_to_trigger(self, *args, **kwargs):
            return args, kwargs

        def send_direct_message_by_bot(self, *args, **kwargs):
            return args, kwargs

        def send_group_message_by_bot(self, *args, **kwargs):
            return args, kwargs

    store = AutoReplyStore(tmp_path / "cache.sqlite3")
    cached = CachedDwsClient(RawDws(), CachedOrgDirectory(store))

    assert cached.send_message("cid-1", "body")[0] == ("cid-1", "body")
    assert cached.reply_message("cid-1", "msg-1", "open-1", "body")[0] == (
        "cid-1", "msg-1", "open-1", "body",
    )
    assert cached.send_reply_to_trigger("cid-1", "msg-1", "body")[0] == (
        "cid-1", "msg-1", "body",
    )
    assert cached.send_direct_message_by_bot("user-1", "body")[0] == ("user-1", "body")
    assert cached.send_group_message_by_bot("cid-1", "body")[0] == ("cid-1", "body")


def test_each_allowed_service_message_path_has_a_focused_body_pair_test() -> None:
    assert _coverage_violations(
        APPROVED_SENDER_PATHS,
        FOCUSED_SENDER_PATH_TESTS,
    ) == []


def test_architecture_guard_rejects_a_business_module_directly_calling_dingtalk(
    tmp_path: Path,
) -> None:
    app_root = tmp_path / "app"
    app_root.mkdir()
    bypass = app_root / "business_sender.py"
    bypass.write_text(
        "def notify(dws):\n"
        "    return dws.send_message('conversation-1', 'bypassed')\n",
        encoding="utf-8",
    )

    assert _raw_send_violations(app_root) == [
        "app/business_sender.py:<module>.notify:2:send_message",
    ]


def test_architecture_guard_rejects_a_business_module_directly_calling_wechat_runner(
    tmp_path: Path,
) -> None:
    app_root = tmp_path / "app"
    app_root.mkdir()
    bypass = app_root / "business_sender.py"
    bypass.write_text(
        "def notify(runner):\n"
        "    return runner.send('Alex', 'bypassed')\n",
        encoding="utf-8",
    )

    assert _raw_send_violations(app_root) == [
        "app/business_sender.py:<module>.notify:2:runner.send",
    ]


def test_architecture_guard_rejects_a_business_module_calling_wechat_ipc_client(
    tmp_path: Path,
) -> None:
    app_root = tmp_path / "app"
    app_root.mkdir()
    bypass = app_root / "business_sender.py"
    bypass.write_text(
        "def notify():\n"
        "    return WechatSenderClient().send('Alex', 'bypassed')\n",
        encoding="utf-8",
    )

    assert _raw_send_violations(app_root) == [
        "app/business_sender.py:<module>.notify:2:runner.send",
    ]


def test_architecture_guard_rejects_dynamic_dingtalk_provider_calls(
    tmp_path: Path,
) -> None:
    app_root = tmp_path / "app"
    app_root.mkdir()
    bypass = app_root / "business_sender.py"
    bypass.write_text(
        "def notify(dws):\n"
        "    return getattr(dws, 'send_message')('conversation-1', 'bypassed')\n",
        encoding="utf-8",
    )

    assert _raw_send_violations(app_root) == [
        "app/business_sender.py:<module>.notify:2:send_message",
    ]


def test_architecture_guard_ignores_transport_definitions_callbacks_and_reads(
    tmp_path: Path,
) -> None:
    app_root = tmp_path / "app"
    app_root.mkdir()
    source = app_root / "read_only.py"
    source.write_text(
        "def send_message(text):\n"
        "    return text\n"
        "\n"
        "def inspect(reader, callback):\n"
        "    callback_handler = callback.send_message\n"
        "    return reader.read_messages(), callback_handler\n",
        encoding="utf-8",
    )

    assert _raw_send_violations(app_root) == []


def test_architecture_guard_rejects_an_unapproved_call_in_an_allowed_file(
    tmp_path: Path,
) -> None:
    app_root = tmp_path / "app"
    app_root.mkdir()
    source = app_root / "dws_client.py"
    source.write_text(
        "class DwsClient:\n"
        "    def send_message(self):\n"
        "        return self.transport.send_message('ok')\n"
        "\n"
        "    def bypass(self):\n"
        "        return self.transport.send_message('bypassed')\n",
        encoding="utf-8",
    )

    assert _raw_send_violations(app_root) == [
        "app/dws_client.py:DwsClient.bypass:6:send_message",
    ]


def test_coverage_registry_rejects_a_missing_allowed_sender_entry() -> None:
    incomplete = dict(FOCUSED_SENDER_PATH_TESTS)
    incomplete.pop("app/wechat/sender_ipc.py:WechatSenderRpcService.dispatch")

    assert _coverage_violations(APPROVED_SENDER_PATHS, incomplete) == [
        "missing coverage:app/wechat/sender_ipc.py:WechatSenderRpcService.dispatch",
    ]


def test_coverage_registry_resolves_the_exact_class_method_not_a_same_named_method(
    tmp_path: Path,
) -> None:
    app_root = tmp_path / "app"
    app_root.mkdir()
    source = app_root / "sender.py"
    source.write_text(
        "class WrongSender:\n"
        "    def send(self):\n"
        "        pass\n",
        encoding="utf-8",
    )

    assert _resolve_target_definition(
        app_root,
        "app/sender.py:ExpectedSender.send",
    ) is None


def test_coverage_registry_rejects_a_same_named_call_on_the_wrong_receiver() -> None:
    module = ast.parse(
        "class OtherSender:\n"
        "    def send(self):\n"
        "        pass\n"
        "\n"
        "def test_wrong_receiver():\n"
        "    sender = OtherSender()\n"
        "    sender.send()\n",
    )
    function = next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "test_wrong_receiver"
    )

    assert not _function_calls_target(
        module,
        function,
        "app/wechat/accessibility.py:WechatSender.send",
    )
