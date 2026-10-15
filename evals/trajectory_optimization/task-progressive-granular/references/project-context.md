4. `ProjectContext` has goal, scope, one `overall_owner` with the responsible
   result, other `responsibilities`, and source-backed facts. Unknown overall
   owner is null (待明确), not a concatenation of people or several overall owners.
   A bare responsibility clause (a person being responsible for a business area)
   is ProjectContext only, not a source Task or candidate. Create a Task only
   for an explicitly stated, independently completable deliverable/action, or
   a separate actionable suggestion required by a sourced material Project risk.
   The clause `X负责Y` by itself remains a Project responsibility, even when Y is
   a distinct business deliverable (for example, 王五负责商务对账). Create a
   source Task only when the source also states a concrete action/expected result
   or provides a real action-item record; a duty-area description is not enough.
   A specific action such as “李四负责核实客户付款排期并反馈” is executable
   and remains a source-origin Task; without an authorized meeting action record,
   keep it a candidate. This differs from a bare duty such as “王五负责商务对账”.
   If overall-owner evidence conflicts, keep `overall_owner` null and record the
   competing claims and challenge as sourced facts. Do not reclassify competing
   overall-owner candidates as responsibilities; that list contains only
   independently evidenced, distinct work duties. State plainly in a Project fact
   that the owner remains in conflict. Preserve unchanged separate deliverable
   owners and their citations.
   State explicitly in a Project fact: 总体负责人存在冲突，仍待确认. Do not
   substitute only “尚未形成一致确认” or “仍需核实”; preserve the competing
   source claims as separate evidence.
   A person explicitly identified as the Project's overall accountable owner
   belongs in `overall_owner`, not `responsibilities`. For example, “张三总负责
   交付验收” identifies the overall role; keep “总” out of the person's name.
   Each person has a distinct responsibility and original evidence. Keep unchanged
   roles' and facts' historical references when updating a snapshot. Whenever new
   facts or roles are learned, read the saved context and return the complete current
   snapshot, retaining prior valid items with their citations; do not return only
   this source's delta. Each dated fact names
   both its date type and date value; ordinary facts leave both unset.
   Keep personal name separate from a trailing Chinese rank/honorific such as
   “总”; store the name only and express the rank/responsibility in the role.
