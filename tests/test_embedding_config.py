from app.config import embedding_enabled


def test_embedding_requires_api_key_by_default(monkeypatch):
    monkeypatch.delenv("CEO_EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("CEO_EMBEDDING_DISABLED", raising=False)

    assert embedding_enabled() is False


def test_embedding_can_be_enabled_with_api_key(monkeypatch):
    monkeypatch.setenv("CEO_EMBEDDING_API_KEY", "secret")
    monkeypatch.delenv("CEO_EMBEDDING_DISABLED", raising=False)

    assert embedding_enabled() is True


def test_embedding_disabled_env_overrides_api_key(monkeypatch):
    monkeypatch.setenv("CEO_EMBEDDING_API_KEY", "secret")
    monkeypatch.setenv("CEO_EMBEDDING_DISABLED", "1")

    assert embedding_enabled() is False
