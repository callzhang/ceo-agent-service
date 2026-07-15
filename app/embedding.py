import json
import urllib.request


class EmbeddingClient:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout_seconds: int = 5,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def __call__(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = {
            "model": self.model,
            "input": texts,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            f"{self.base_url}/v1/embeddings",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(
            request,
            timeout=self.timeout_seconds,
        ) as response:
            data = json.loads(response.read().decode("utf-8"))
        return [_embedding_values(item) for item in data.get("data", [])]


def _embedding_values(item: object) -> list[float]:
    if not isinstance(item, dict):
        return []
    embedding = item.get("embedding")
    if not isinstance(embedding, list):
        return []
    return [float(value) for value in embedding if isinstance(value, (int, float))]
