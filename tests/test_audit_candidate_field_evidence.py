from app.audit_candidate_field_evidence import render_candidate_field_evidence


def test_candidate_field_evidence_reports_exact_nested_scalar_values_and_types():
    candidate = {
        "proposal": {
            "actions": [
                {"target": {"task_id": "104265682397", "enabled": True}},
            ],
        },
    }

    rendered = render_candidate_field_evidence(candidate)

    assert "/proposal/actions/0/target/task_id | string | \"104265682397\"" in rendered
    assert "/proposal/actions/0/target/enabled | boolean | true" in rendered


def test_candidate_field_evidence_escapes_json_pointer_keys_and_keeps_numbers_distinct_from_booleans():
    candidate = {"a/b~c": {"count": 7, "enabled": False, "missing": None}}

    rendered = render_candidate_field_evidence(candidate)

    assert "/a~1b~0c/count | number | 7" in rendered
    assert "/a~1b~0c/enabled | boolean | false" in rendered
    assert "/a~1b~0c/missing | null | null" in rendered
