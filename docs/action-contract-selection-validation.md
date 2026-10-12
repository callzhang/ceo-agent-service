# Action contract lookup selection

An Audit trajectory queries a single registered action but receives all 15 operation declarations. The same behavior affects Consumer. The capability/operation metadata is already selected; only the contract body previously ignored the selection.

The lookup now retains every non-table line of the canonical source document and selects the requested capability/operation table rows. Each selected row retains its target, payload and completion-evidence cells. Capability-only lookup returns all of that capability's operations. Shared source prose, canonical file, complete-document fingerprint, mandatory lookup requirement, business validation, tool signature/allowlist, and session binding remain unchanged. Domain-specific notes outside the table are intentionally retained; no business or audit policy is removed.

Regression first: 5 failed, 12 passed on the original implementation. Initial fix: 17 passed. Expanded catalog/native role MCP/runtime context coverage: 83 passed. The operation-preservation test exercises every registered operation, including splitting the shared approve/reject row; a synthetic new capability checks selection is not tied to current names. Independent review requested accurate catalog/MCP descriptions; both are updated alongside architecture/runtime docs.

Measured canonical body: 6094 characters. Exact reply_to_message: 3997 (2097 fewer); calendar respond_calendar_event: 3947 (2147 fewer); OA approve: 3932 (2162 fewer); document capability-only: 4049 (2045 fewer). These are tool-return body changes, not Developer or Task length claims. No model parameter, full wire schema, or native output-schema setting changes. Production/readback and complete business comparison remain separate gates.


## Production readback

Formal deploy moved production3195cd3b to a0be28d6, PID34205 to65054. Production checkout fast-forward: 2026-10-12T03:08:24Z; new process:03:08:25Z. Health, Status, Attention and History HTTP200. Reply/Work/Meeting processing0 at first post-readback; provider actions retain3 pending, and scheduled dispatcher has2 running. These are status observations, not business-completion claims.

Two Reply attempts became failed while the deploy waited for quiet:15283 at03:07:34Z and15284 at03:08:11Z, both before the new checkout/process. Attention groups them as audit_revision_exhausted. Their full native Consumer/Audit revision ranges were read, not replayed. For15284, Audit inputs25838/25843 contain the identical canonical-valid candidate digest and string process_instance_id/task_id/target_activity_id; the final feedback nevertheless requests converting task_id to a string. This confirms a contradictory Audit assertion, not an input-type or clipping repair to make here. The original private business/Skill bodies are not copied into this record.

From a temporary directory, a stdio MCP client imported the deployed code and read every registered operation for both roles:30 exact selected-row/shared-text checks passed. No model or business action was invoked. This proves the deployed lookup transport/body; full model business acceptance remains separate. Original native model-facing read_skill outputs in the final pre-release Audit were also compared locally:37887/7044 characters match the source files exactly. Existing Skill delivery is complete in that run; no extra private-body model probe is needed for that source-delivery question.

Concise technical receipts: evals/trajectory_optimization/action_contract_selection_release.results.v1.json. No new audit validation policy, prompt patch, task replay or native-schema enablement was introduced. The remaining false Audit type feedback and business revision loop are separate trajectory problems to investigate.

Settings API readback: current Audit preview uses the selected-row lookup description (Developer29207 characters); historical run25843 retains the original full-canonical description and saved Developer28882. The historical input is not rebuilt using the current catalog. These differing lengths are current-versus-history observations, not a controlled input-size improvement claim.
