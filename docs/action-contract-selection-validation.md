# Action contract lookup selection

An Audit trajectory queries a single registered action but receives all 15 operation declarations. The same behavior affects Consumer. The capability/operation metadata is already selected; only the contract body previously ignored the selection.

The lookup now retains every non-table line of the canonical source document and selects the requested capability/operation table rows. Each selected row retains its target, payload and completion-evidence cells. Capability-only lookup returns all of that capability's operations. Shared source prose, canonical file, complete-document fingerprint, mandatory lookup requirement, business validation, tool signature/allowlist, and session binding remain unchanged. Domain-specific notes outside the table are intentionally retained; no business or audit policy is removed.

Regression first: 5 failed, 12 passed on the original implementation. Initial fix: 17 passed. Expanded catalog/native role MCP/runtime context coverage: 83 passed. The operation-preservation test exercises every registered operation, including splitting the shared approve/reject row; a synthetic new capability checks selection is not tied to current names. Independent review requested accurate catalog/MCP descriptions; both are updated alongside architecture/runtime docs.

Measured canonical body: 6094 characters. Exact reply_to_message: 3997 (2097 fewer); calendar respond_calendar_event: 3947 (2147 fewer); OA approve: 3932 (2162 fewer); document capability-only: 4049 (2045 fewer). These are tool-return body changes, not Developer or Task length claims. No model parameter, full wire schema, or native output-schema setting changes. Production/readback and complete business comparison remain separate gates.
