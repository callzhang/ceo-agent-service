import app.oa_approval as oa_approval
from app.oa_approval import extract_oa_url


def test_oa_module_exposes_only_reference_parsing_not_legacy_agent_runner():
    assert not hasattr(oa_approval, "OaApprovalSpecHandler")
    assert not hasattr(oa_approval, "StructuredCodexRunner")


def test_oa_module_has_no_dedicated_result_parser():
    assert not hasattr(oa_approval, "OaApprovalResult")
    assert not hasattr(oa_approval, "parse_oa_approval_json")


def test_extract_oa_url_decodes_encoded_aflow_url_inside_dingtalk_card():
    encoded_url = (
        "https%3A%2F%2Faflow.dingtalk.com%2Fdingtalk%2Fmobile%2Fhomepage.htm"
        "%3FprocInstId%3Dproc-1%26taskId%3Dtask-1"
    )
    text = f'{{"pcLink":"dingtalk://dingtalkclient/page/link?url={encoded_url}"}}'

    assert extract_oa_url(text) == (
        "https://aflow.dingtalk.com/dingtalk/mobile/homepage.htm"
        "?procInstId=proc-1&taskId=task-1"
    )


def test_extract_oa_url_ignores_outer_wrapper_params_and_trailing_punctuation():
    encoded_url = (
        "https%3A%2F%2Faflow.dingtalk.com%2Fdingtalk%2Fmobile%2Fhomepage.htm"
        "%3FprocInstId%3Dproc-1%26taskId%3Dtask-1"
    )
    text = (
        f"(dingtalk://dingtalkclient/page/link?url={encoded_url}"
        "&pc_slide=false)"
    )

    assert extract_oa_url(text) == (
        "https://aflow.dingtalk.com/dingtalk/mobile/homepage.htm"
        "?procInstId=proc-1&taskId=task-1"
    )


def test_extract_oa_url_strips_sentence_period_from_direct_url():
    text = "请处理 https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1."

    assert (
        extract_oa_url(text)
        == "https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1"
    )


def test_extract_oa_url_decodes_html_escaped_query_delimiters():
    text = (
        "> 宋述评论了审批:"
        "[宋述提交的费用报销对外付款综合审批单]"
        "(https://aflow.dingtalk.com/dingtalk/mobile/homepage.htm?"
        "corpid=ding-test&amp;dd_share=false&amp;"
        "procInstId=proc-1&amp;taskId=task-1&amp;"
        "dinghash=approval#approval)"
    )

    assert extract_oa_url(text) == (
        "https://aflow.dingtalk.com/dingtalk/mobile/homepage.htm?"
        "corpid=ding-test&dd_share=false&procInstId=proc-1&taskId=task-1&"
        "dinghash=approval#approval"
    )
