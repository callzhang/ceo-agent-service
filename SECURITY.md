# Security Policy

## Supported Versions

The project is early-stage. Security fixes target `main`.

## Reporting

Please report security issues through GitHub private vulnerability reporting if
enabled for the repository, or open a minimal issue that does not include
secrets or private chat content.

## Secret Handling

Never commit:

- DingTalk tokens, robot codes, webhooks, or authorization URLs
- Codex session logs
- SQLite runtime databases
- exported DingTalk chat data
- local style corpus files
- private workspace documents

The default `.gitignore` excludes `data/`, `corpus/`, `.env`, logs, virtualenvs,
and build artifacts.

## Deployment Notes

Run the worker locally or in a trusted environment with access to the operator's
authenticated `dws` and Codex CLI state. Keep `CEO_DRY_RUN=1` until you have
reviewed local audit output and explicitly accepted live-send risk.
