from app.web_api.registration import current_approval_history_summary


def test_replaces_stale_uncertain_summary_with_verified_current_effect_time():
    summary = current_approval_history_summary(
        status_text="done",
        display_summary=(
            "Action oa-comment was not verified\nexternal_action_uncertain"
        ),
        detail_payload={
            "status": {
                "raw": "done",
                "message": "已审核方案的全部动作已核验完成。",
                "attention": {
                    "external_effect": (
                        "本轮外部动作已完成（本轮操作时间：2026-10-19T18:11:53.364Z）"
                    ),
                },
            },
        },
    )

    assert summary == (
        "已审核方案的全部动作已核验完成。；"
        "本轮外部动作已完成（本轮操作时间：2026-10-19T18:11:53.364Z）"
    )
    assert "external_action_uncertain" not in summary


def test_projects_reused_receipt_wording_and_original_operation_time():
    summary = current_approval_history_summary(
        status_text="done",
        display_summary="old external_action_uncertain",
        detail_payload={
            "status": {
                "raw": "done",
                "message": "已审核方案的全部动作已核验完成。",
                "attention": {
                    "external_effect": (
                        "复用了之前已核验的外部动作（原操作时间："
                        "2026-10-17T06:30:49.588Z）"
                    ),
                },
            },
        },
    )

    assert "复用了之前已核验的外部动作" in summary
    assert "2026-10-17T06:30:49.588Z" in summary


def test_keeps_legacy_summary_if_current_detail_status_disagrees():
    old_summary = "old external_action_uncertain"

    assert current_approval_history_summary(
        status_text="done",
        display_summary=old_summary,
        detail_payload={
            "status": {
                "raw": "failed",
                "message": "已审核方案执行失败。",
                "attention": {"external_effect": ""},
            },
        },
    ) == ""


def test_does_not_replace_done_history_summary_when_detail_is_uncertain():
    old_summary = "old external_action_uncertain"

    assert current_approval_history_summary(
        status_text="done",
        display_summary=old_summary,
        detail_payload={
            "status": {
                "raw": "uncertain",
                "message": "外部动作仍待核验。",
                "attention": {"external_effect": "当前外部动作未确认。"},
            },
        },
    ) == ""


def test_keeps_legacy_summary_for_non_uncertain_rows():
    assert current_approval_history_summary(
        status_text="done",
        display_summary="已核验完成",
        detail_payload={
            "status": {
                "raw": "done",
                "message": "other",
                "attention": {"external_effect": ""},
            },
        },
    ) == ""


def test_projects_current_uncertain_effect_without_claiming_completion():
    summary = current_approval_history_summary(
        status_text="done",
        display_summary="legacy external_action_uncertain",
        detail_payload={
            "status": {
                "raw": "done",
                "message": "已审核方案没有全部核验完成；外部结果仍待核验。",
                "attention": {
                    "external_effect": "当前外部动作仍未确认。",
                },
            },
        },
    )

    assert "仍待核验" in summary
    assert "仍未确认" in summary
    assert "全部动作已核验完成" not in summary
