from app.config import (
    embedding_api_key,
    embedding_base_url,
    embedding_enabled,
    embedding_model,
    embedding_timeout_seconds,
)
from app.embedding import EmbeddingClient


def test_embedding_uses_default_jina_provider_config(monkeypatch):
    monkeypatch.delenv("CEO_EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("CEO_EMBEDDING_BASE_URL", raising=False)
    monkeypatch.delenv("CEO_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("CEO_EMBEDDING_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("CEO_EMBEDDING_DISABLED", raising=False)

    assert embedding_enabled() is True
    assert embedding_base_url() == "https://embed.preseen.ai/v1"
    assert embedding_model() == "jinaai/jina-embeddings-v5-text-small"
    assert embedding_api_key()
    assert embedding_timeout_seconds() == 120


def test_embedding_can_be_enabled_with_api_key(monkeypatch):
    monkeypatch.setenv("CEO_EMBEDDING_API_KEY", "secret")
    monkeypatch.delenv("CEO_EMBEDDING_DISABLED", raising=False)

    assert embedding_enabled() is True


def test_embedding_disabled_env_overrides_api_key(monkeypatch):
    monkeypatch.setenv("CEO_EMBEDDING_API_KEY", "secret")
    monkeypatch.setenv("CEO_EMBEDDING_DISABLED", "1")

    assert embedding_enabled() is False


def test_embedding_client_accepts_openai_versioned_base_url():
    client = EmbeddingClient(
        base_url="https://embed.preseen.ai/v1",
        model="jinaai/jina-embeddings-v5-text-small",
        api_key="secret",
    )

    assert client.embeddings_url == "https://embed.preseen.ai/v1/embeddings"
