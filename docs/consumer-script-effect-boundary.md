# Consumer Skill script boundary

Consumer may prepare local artifacts and write a report document through the
task-bound `consumer_document_write` tool. A message, email, OA decision, OKR
score submission, or other action directly affecting another person remains a
structured candidate for Audit and the reviewed SystemExecutor. Neither the
Consumer nor Audit role MCP catalog exposes those dispatch operations.

Codex Consumer shell execution runs in its task workspace with network access
disabled. Claude role turns expose only the read-only built-in tools. The
`consumer_run_skill_script` MCP tool runs outside those native sandboxes, so a
selected Skill alone is insufficient permission to run every Python file in
its package. In particular, `ceo-wechat/scripts/sender.py` can invoke sender
approval and must not be callable by Consumer.

The tool now requires a `consumer-scripts.json` manifest in the service-managed
Skill source. The manifest names reviewed entrypoints and pins the SHA-256 of
the complete `scripts/` tree and Python helpers elsewhere in the package
(excluding tests). At invocation, the
service compares that digest with both its checked-out source and the installed
Skill package. A missing manifest, unlisted entrypoint, or changed source fails
closed before the subprocess starts. The task's frozen selected-Skill binding
and generation check still apply. The current manifests expose only the
existing daily report, weekly report, and weekly OKR report scripts; document
creation by those scripts remains permitted, and final report publication
continues through `consumer_document_write`.

When adding or changing a Consumer script, review the whole executable Python
package for external effects, update its manifest digest in the same change,
and verify that the installed package matches before releasing the runtime.
No generic command or Skill-script path grants a direct send or approval.
