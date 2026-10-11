from scripts.eval_meeting_audience import score


def test_exact_route_score_rejects_self_and_public_private_leak():
    case = {"expected_target":{"kind":"group","conversation_id":"pilot"},"expected_private_user_id":"manager","private_marker":"P-7"}
    result = {"action":"send","audience_scope":"business","target":{"kind":"group","conversation_id":"pilot"},"final_message":"Checklist ready.","sensitive_private_message":{"target":{"direct_user_id":"manager"},"message":"P-7 review"}}
    assert score(case,result)
    result["sensitive_private_message"]["target"]["direct_user_id"]="principal"
    assert not score(case,result)
    result["sensitive_private_message"]["target"]["direct_user_id"]="manager"
    result["final_message"]="P-7 review"
    assert not score(case,result)


def test_same_audience_score_rejects_extra_private_send():
    case = {"expected_target":{"kind":"direct","direct_user_id":"manager"},"expected_private_user_id":None}
    result = {"action":"send","audience_scope":"business","target":{"kind":"direct","direct_user_id":"manager"},"sensitive_private_message":None}
    assert score(case,result)
    result["sensitive_private_message"]={"target":{"direct_user_id":"manager"},"message":"Extra"}
    assert not score(case,result)
