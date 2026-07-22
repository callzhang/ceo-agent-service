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
- Feishu App Secrets, tenant/app access tokens, event credentials, or signed media URLs
- Codex session logs
- SQLite runtime databases
- exported DingTalk/Feishu chat data or message attachments
- local style corpus files
- private workspace documents

The default `.gitignore` excludes `data/`, `corpus/`, `.env`, logs, virtualenvs,
and build artifacts.

For the optional Feishu Bot channel, keep the App Secret in macOS Keychain with
service `ceo-agent-service/feishu` and account `app_secret`. The
`CEO_FEISHU_APP_SECRET` environment variable exists only as a local debugging
fallback and must not be added to `.env.example`, shell history, launchd plist
files, logs, SQLite, or audit output. Status and diagnostics may report only
`configured` or `missing`; they must never print a credential or credential
fragment. Rotate the App Secret immediately if any such material is exposed.

Feishu message text and attachments are private business data. Retain only the
normalized fields required for routing and audit, do not persist complete raw
events or access tokens, and do not download unsupported media by default.
The Feishu decision subprocess must run in the repository's hard no-tool mode:
ignore user configuration, use a read-only sandbox, expose no MCP transports or
known external-tool credentials, disable web and all tools, and fail closed on
any tool lifecycle event. Prompt-only prohibitions are not a security boundary.
Memory recall is deliberately unavailable until a recall-only global tool
allowlist can be verified without re-enabling shell, web, or other MCP tools.

## Deployment Notes

Run the worker locally or in a trusted environment with access to the operator's
authenticated `dws` and Codex CLI state. Keep `CEO_DRY_RUN=1` until you have
reviewed local audit output and explicitly accepted live-send risk.

The Feishu channel is independently disabled by default. Enabling receipt does
not enable delivery. A real Feishu send requires both
`CEO_NOT_SEND_MESSAGE=0` and `CEO_FEISHU_SENDER_ENABLED=1`, plus an approved
delivery in `confirm` mode. Keep `CEO_FEISHU_SEND_MODE=confirm` during initial
deployment; `auto` requires a separate, explicit risk decision.
