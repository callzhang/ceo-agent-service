"""Read-only MCP entrypoint for opt-in Task 6 native Codex tests."""

from __future__ import annotations

import sys
from pathlib import Path

from tests.support.task5_read_fixture_mcp import serve


if __name__ == "__main__":
    serve(Path(sys.argv[1]), Path(sys.argv[2]))
