# OA Audit OCR handoff baseline, 2026-10-20

## Selected failure and evidence boundary

At diagnosis, the production checkout was `296b6d82f`. Its OA Consumer path already
supports same-cell CodeMode transfer of attachment image bytes to OCR; Audit
still exposes the read tools directly and excludes `functions` from CodeMode.
The latest remote `main` inspected for this baseline also retains that Audit
boundary. The separately scoped OA Audit read-only CodeMode/OCR change was
subsequently approved; the baseline alone did not implement it.

Audit run 386851 for OA task 405641 had two Codex OAuth attempts, 386852 and
386876. In both complete native session trajectories,
`read_dingtalk_oa_process_attachment` completed and its raw MCP result
contained a decodable 112,311-byte rendered PDF page image. The model-visible
`function_call_output` for that read was 48,024 characters and contained a
truncation marker inside `ocr_input_base64`; the extracted value could not be
base64-decoded. The source SHA in that result identifies the original PDF, not
the rendered JPEG, so it must not be used to assert a JPEG digest match.
Both attempts ended `codex_idle_timeout`. This proves that the bytes existed at
the source-tool boundary but were not available intact through the direct
model-visible output. It does not prove that an OCR result would have changed
the business judgment.

The same Audit run later completed through Qwen. That native session attempted
nine `agent_cli.read_*` function names and received `unsupported call` for each;
it had no successful MCP read before returning an approve result. Whether the
underlying Qwen request omitted tools is unproven because the saved native
session lacks its exact provider request/tool declarations. Investigate that
provider path separately; do not use it as the cause or test oracle for this
OCR handoff change. The later SystemExecutor provider readback verified the OA
return action; that verification does not retroactively establish independent
Audit reading.

## Finite acceptance for a separately approved change

1. In an anonymous fixed OA Audit case, a source read returns a rendered image
   larger than the model-visible tool-output window. The exact bytes reach
   `ai_services.ocr_document` in the same native CodeMode call without printing
   or recopying base64 through a prompt. The result preserves OCR text, the
   original one-based PDF page number, and the original PDF source identity.
2. The OA Audit session retains read-only sandboxing and its existing MCP tool
   allowlist. Other Audit task types and OA Consumer keep their current command
   and instructions. No candidate acceptance rule, external action, retry,
   route fallback or historical task replay is changed.
3. A regression for the current OA Audit command fails before the tool-boundary
   change and passes after it. Run focused role-command/prompt tests and the
   anonymous native cross-MCP case; a process exit or valid JSON alone does
   not count as behavior verification.
4. After review and the repository's normal release path, read back the
   production commit, PID, health, queues, Attention and History. A natural OA
   Audit case with OCR is needed before claiming production business behavior;
   installation and health alone prove only the deployed mechanism.

## Approved implementation and local verification

Derek approved a separate change limited to OA Audit: compose its declared
`agent_cli` reads and `ai_services.ocr_document` in CodeMode, exclude built-in
namespaces there, and leave Audit judgment rules, action permissions and other
task types unchanged. The service selects this path from its existing typed OA
task context and records that choice in the saved invocation facts; Settings
current preview uses the same OA Audit instruction assembly. Other configured
MCP namespaces stay direct-only for OA Audit. The existing read-only sandbox
and tool allowlists remain in force.

The command/prompt regressions failed before implementation because the OA
Audit parameter did not exist; after implementation, 65 focused role-boundary,
Audit runner and Settings preview tests passed. Ruff passed for the changed
Python files. An anonymous native Codex OAuth case used local fake source and
OCR MCP servers, a 197,107-byte PNG, and an extra fake memory MCP namespace.
The OCR server wrote a receipt for the exact image byte count and SHA-256. The
final Agent message contained the OCR marker, original PDF page number `3`,
and synthetic source SHA; it did not contain the image base64. Only source and
OCR tools were called. Native JSONL still records base64 in MCP result and
tool-argument events; that is distinct from printing it in the Agent message.
The temporary fixture, image, credential symlink and receipts were removed
after the run. This proves native cross-MCP transport under the generated OA
Audit role command, not a production OA judgment.

The repository's `AGENTS.md` requires Audit/review/safety-policy changes to be
proposed and confirmed as a separate, explicitly scoped change; that
confirmation is recorded above. The concurrently claimed direct OCR namespace
visibility work remains independent. No production records or external OA
actions were replayed for this fixed case.
