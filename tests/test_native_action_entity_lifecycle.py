"""Focused guards for the current Action Entity native acceptance harness."""

from scripts.validate_native_action_entity_lifecycle import _acceptance_instruction, _stable_inverse_facts


def test_native_action_case_requests_system_effect_without_contradicting_sink():
    instruction = _acceptance_instruction()

    assert 'send_group_message' in instruction
    assert '合成群的完整成员、角色和接收适配信息' in instruction
    assert '系统动作注册表仅声明 send_group_message' in instruction
    assert 'SystemExecutor' in instruction
    assert '本地内存测试替身' in instruction
    assert '没有真实 provider 客户端或网络路径' in instruction
    assert '不要调用任何直接发送工具' in instruction
    assert '不要发送' not in instruction
    assert '禁止发送' not in instruction


def test_inverse_fact_comparison_preserves_business_facts_while_ignoring_only_local_owner_ids():
    before = {"sent_replies": [{"id": 7, "conversation_id": "synthetic", "reply_text": "approved body",
                               "send_result_json": '{"message_id":"native-receipt"}', "_action_id": 101}],
              "outbound_postfixes": []}
    after = {"sent_replies": [{"id": 7, "conversation_id": "synthetic", "reply_text": "approved body",
                              "send_result_json": '{"message_id":"native-receipt"}', "_action_id": 900}],
             "outbound_postfixes": [{"channel": "dingtalk", "delivery_key": "test-cold-write",
                                     "final_body": "old runtime preparation"}]}

    assert _stable_inverse_facts(before) == _stable_inverse_facts(after, exclude_delivery_key="test-cold-write")
    after["sent_replies"][0]["send_result_json"] = '{"message_id":"different-receipt"}'
    assert _stable_inverse_facts(before) != _stable_inverse_facts(after, exclude_delivery_key="test-cold-write")
