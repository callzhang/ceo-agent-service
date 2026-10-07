import importlib.util
from pathlib import Path
import sys


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
spec = importlib.util.spec_from_file_location("audience_eval", SCRIPT_DIR / "eval_message_audience.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_exact_delivery_oracle_rejects_self_copy_and_cross_audience_content():
    case = {"expected_consumer": "proposal", "expected_deliveries": [
        {"target": {"conversation_id": "group"}, "content": "public"},
        {"target": {"open_dingtalk_id": "counterpart"}, "content": "private"},
    ]}
    actions = [
        {"capability": "dingtalk-chat", "operation": "send_message", **delivery,
         "payload": {"content": delivery["content"]}}
        for delivery in case["expected_deliveries"]
    ]
    result = {"outcome": "proposal", "proposal": {"actions": actions}}
    assert module.delivery_matches(case, result)
    actions[1]["target"] = {"open_dingtalk_id": "principal"}
    assert not module.delivery_matches(case, result)
    actions[1]["target"] = {"open_dingtalk_id": "counterpart"}
    actions[0]["payload"]["content"] = "public private"
    assert not module.delivery_matches(case, result)


def test_gap_and_refusal_oracle_rejects_external_actions():
    for outcome in ("needs_human", "failed"):
        case = {"expected_consumer": outcome, "expected_deliveries": []}
        assert module.delivery_matches(case, {"outcome": outcome, "proposal": None})
        assert not module.delivery_matches(case, {"outcome": outcome, "proposal": {"actions": [{}]}})
