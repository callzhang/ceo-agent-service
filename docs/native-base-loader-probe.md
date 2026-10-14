# Native base instruction loader diagnostic

## Confirmed mechanism

Current `codex-cli 0.154.0` supports `model_instructions_file`. A native cold invocation through configured `codex_oauth` / `gpt-5.6-luna` saves the exact replacement text in session metadata. Same-session continuation with the same configuration succeeds and retains that saved base. This verifies the loader mechanism, not a business-ready replacement.

The diagnostic text was only:

> CEO_NATIVE_BASE_DIAGNOSTIC_ONLY. This file is a no-model configuration probe, not a production instruction candidate.

That marker is **not** proposed for production. The subsequent cold/resume verification did make model requests. Both user prompts were `Return exactly OK.`; no business facts, Work Profile or service Developer was submitted. Role-compatible native approval `never` and read-only sandbox remained active. Service transports were substituted with no registered source tools; all invocations made zero tool calls. Production settings, existing business sessions, CLI home and shared AGENTS were not changed.

## Receipts and controls

- Default-base cold: session `01a13872-437f-7030-8344-a9c174a7d583`, terminal exit0, final `OK`, saved native base17,730 characters.
- Marker-base cold and same-session continuation: session `01a1386f-e3bf-7010-be01-f0615f49decc`, each terminal exit0 and final `OK`, saved base117 characters matching the diagnostic file exactly.
- Native `debug prompt-input` accepts the file and rejects a nonexistent path with a read error. It does **not** expose the base marker in its output, so a successful debug call alone is insufficient evidence.
- An initial attempted cold invocation failed locally with `invalid transport in mcp_servers.agent_cli` before a model turn. The isolated role configuration still declared that server after service transports were removed. Supplying an explicitly disabled placeholder transport resolved this fixture configuration error; no production adapter change was made.
- The default and replacement cold AGENTS/environment and user-message content hashes match. Native Skill messages differ: the replacement includes924 characters, while default includes none. Consequently input counters8,580 versus5,254 are descriptive observations, **not** a clean single-variable savings measurement or business comparison. Do not publish a percentage improvement from them.

Original native files remain in the CLI's normal home. The JSON receipt preserves exact line ranges and SHA256 so future session appends do not erase the inspected evidence boundary. Task-owned local probe outputs are disposable; no private raw trajectory was copied into the repository.

## What this enables, and what remains

Native base is a separate optimization surface from Developer, Task and AGENTS. The old base's top-level spans are personality2,081, interactive collaboration4,868, work rules4,615, destructive-action rules1,281 and Skill instructions4,726 characters (plus159-character opening). Some prose is unrelated to background typed-result execution; other sections contain authority and ownership requirements. Replacing the base is not equivalent to deleting only coding prose.

Before any runtime change, prepare a source-mapped role candidate, explicitly preserve authority/permission behavior, and compare actual task trajectories and business results across calendar, mail, document and tracking scenarios, including cold/resume and Audit feedback. Native Skill rereading may be influenced by per-turn instructions, but this loader test does not demonstrate that effect. No new authorization, review policy, automatic history suppression or provider change was implemented.

Official reference: https://learn.chatgpt.com/docs/config-file/config-reference (`model_instructions_file` replaces built-in instructions; it does not disable AGENTS).
