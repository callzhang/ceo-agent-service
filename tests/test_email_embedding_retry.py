"""Real embedding/client/batcher failures retain their discovery retry identity."""

from contextlib import contextmanager
import ssl
from types import SimpleNamespace

import httpx
import pytest

from app.dws_client import is_transient_dependency_error
from app.email_classifier_runtime import (
    OnlineBatchCompatibility, OnlineEmbeddingBatchError, OnlineEmbeddingMicrobatcher,
)
from app.email_embedding_client import EmailEmbeddingClient, EmbeddingProtocolError
from app.email_scheduled_command import EmailMessageCheckOnceCommand, TransientEmailDiscoveryError


COMPATIBILITY = OnlineBatchCompatibility("runtime", "model", "schema", "embedding", "revision")


@contextmanager
def _endpoint(statuses):
    calls = []

    def handle(request):
        status = statuses[min(len(calls), len(statuses) - 1)]
        calls.append(request.method)
        if status == "timeout":
            raise httpx.ReadTimeout("provider timed out", request=request)
        if status == "tls":
            try:
                raise ssl.SSLCertVerificationError("invalid certificate")
            except ssl.SSLCertVerificationError as exc:
                raise httpx.ConnectError("TLS verification failed", request=request) from exc
        payload = {"data": [{"index": 0, "embedding": [1.0]}]}
        if status == "protocol":
            return httpx.Response(200, json={"not_embeddings": []})
        return httpx.Response(status, json=payload)

    with httpx.Client(transport=httpx.MockTransport(handle)) as transport:
        client = EmailEmbeddingClient(
            url="https://embedding.example.test/v1/embeddings", embedding_revision="revision",
            dimension=1, transport=transport,
        )
        batcher = OnlineEmbeddingMicrobatcher(client, maximum_wait_seconds=0.001)
        try:
            yield batcher, calls
        finally:
            batcher.close()


def _discovery(batcher, delays, persisted):
    def scan(_account, _model):
        batcher.embed_one("synthetic message", COMPATIBILITY)
        persisted.append(1)
        return {"persisted_count": 1}

    bootstrap = SimpleNamespace(
        load_enabled_accounts=lambda: ({"account_id": "mailbox"},),
        load_active_model=lambda: object(), scan_account=scan,
        record_health=lambda *_args: None,
    )
    return EmailMessageCheckOnceCommand(
        SimpleNamespace(), dependency_builder=lambda _settings: bootstrap, sleep=delays.append,
    )


def test_microbatch_failure_preserves_original_http_cause():
    with _endpoint([502]) as (batcher, calls):
        with pytest.raises(OnlineEmbeddingBatchError) as raised:
            batcher.embed_one("synthetic message", COMPATIBILITY)
        assert isinstance(raised.value.__cause__, EmbeddingProtocolError)
        assert isinstance(raised.value.__cause__.__cause__, httpx.HTTPStatusError)
        assert raised.value.__cause__.__cause__.response.status_code == 502
        assert calls == ["POST"]


def test_discovery_recovers_temporary_embedding_failure_before_persisting():
    delays, persisted = [], []
    with _endpoint([502, 502, 200]) as (batcher, calls):
        result = _discovery(batcher, delays, persisted)()
        assert result.complete
        assert result.summary == "email-message-check-once accounts=1 discovered=1 failures=0"
        assert len(calls) == 3
        assert delays == [1.0, 2.0]
        assert persisted == [1]


@pytest.mark.parametrize("status", [500, 502, 503, 504, "timeout"])
def test_exhausted_embedding_outage_remains_failed_transient_discovery(status):
    delays, persisted = [], []
    with _endpoint([status]) as (batcher, calls):
        with pytest.raises(TransientEmailDiscoveryError) as raised:
            _discovery(batcher, delays, persisted)()
        assert is_transient_dependency_error(raised.value)
        assert len(calls) == 3
        assert delays == [1.0, 2.0]
        assert persisted == []


@pytest.mark.parametrize("status", [400, 401, 403, 404, 501, 505, "protocol", "tls"])
def test_auth_configuration_and_protocol_errors_are_not_retried(status):
    delays, persisted = [], []
    with _endpoint([status]) as (batcher, calls):
        with pytest.raises(RuntimeError) as raised:
            _discovery(batcher, delays, persisted)()
        assert not is_transient_dependency_error(raised.value)
        assert len(calls) == 1
        assert delays == []
        assert persisted == []
