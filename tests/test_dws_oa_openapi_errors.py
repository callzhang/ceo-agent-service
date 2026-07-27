import pytest

from app.dws_client import DwsClient, DwsError


def test_read_oa_process_instance_openapi_raises_quota_error():
    class ApiRecordingClient(DwsClient):
        def _read_dingtalk_skill_credentials(self, config_path=None):
            del config_path
            return {
                "DINGTALK_APP_KEY": "app-key",
                "DINGTALK_APP_SECRET": "app-secret",
            }

        def _http_json(self, method, url, payload=None, *, headers=None):
            del method, url, payload, headers
            return {
                "errcode": 88,
                "errmsg": "ding talk error",
                "sub_code": "90020",
                "sub_msg": "您的企业本月api调用量已超过限制",
            }

    with pytest.raises(DwsError) as exc_info:
        ApiRecordingClient().read_oa_process_instance_openapi("proc-1")

    assert exc_info.value.code == DwsError.DINGTALK_OPENAPI_QUOTA_EXCEEDED_CODE
    assert "api调用量已超过限制" in str(exc_info.value)
