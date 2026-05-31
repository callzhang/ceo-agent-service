import json
from pathlib import Path


class ThreadRegistry:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def get_session(self, thread_id: str) -> str | None:
        return self._data.get(thread_id)

    def set_session(self, thread_id: str, session_id: str) -> None:
        self._data[thread_id] = session_id
        self.path.write_text(
            json.dumps(self._data, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))
