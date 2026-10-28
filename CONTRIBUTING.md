# Contributing

Thanks for considering a contribution.

## Development

```bash
"$HOME/miniforge3/bin/python" -m pip install -e '.[dev]'
cd /path/to/ceo-agent-service
# 只运行覆盖你改动的测试文件；不要在开发树跑全量（门禁集见 tests/gate_files.txt）
"$HOME/miniforge3/bin/python" -m pytest -q tests/test_<你改动的模块>.py
```

The repository does not create or depend on a private `.venv`; every Python
entry point runs on the one shared interpreter.

## Rules

- Keep live DingTalk sends opt-in.
- Do not commit SQLite databases, chat exports, Codex sessions, local corpus
  files, tokens, robot codes, or customer data.
- Add tests for behavior changes.
- Keep prompts auditable: generated answers may cite internal evidence in local
  audit fields, but public user replies must not leak file paths or citations.
- Prefer deterministic code for transport, storage, and audit behavior; keep
  semantic decisions in the model prompt or a trained classifier.

## Pull Requests

Please include:

- what changed
- why it changed
- how you tested it
- any live DingTalk behavior that could be affected
