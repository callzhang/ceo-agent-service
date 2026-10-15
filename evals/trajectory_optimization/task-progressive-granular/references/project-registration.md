## Project context first, actual Tasks and suggestions distinguished

1. Read related original meetings, management/department/project weekly reports,
   messages and emails together with existing Project context and Tasks.
   Weekly reports are neither the sole information source nor a prerequisite.
   Source authority, authorship, exact version and time matter; repetition is not
   independent confirmation. Preserve conflicting facts and their evidence/times
   instead of silently choosing a convenient source.
2. A Project has a real goal and scope, not just a department, customer/topic,
   small Task, todo bundle or similarity cluster. Register only a confirmed
   report Project registry entry or explicit meeting Project decision; quote the
   defining passage in `registration.source_excerpt` and set its real authority.
   Preserve the exact source-defined title, reference and report period. A Project
   title contains only the entity name, not a status or action attached in the
   same clause. For example, in “甲客户一期交付项目正式启动”, keep
   “甲客户一期交付项目” as the title and store “正式启动” as a fact when relevant.
   Adopt the exact current authoritative Project definition with registration to register or reuse.
   A different stored name cannot replace that definition merely because the action uses its shorter name.
   Task/action sections are not Project registries. Chats/emails may supplement
   known Project facts but do not by themselves create official Project identity.
   Do not infer aliases or identity from rank or topical similarity. A complete
   source-stated customer prefix may be emitted only as `crm_customer_label`
   when it is clearly separate from the Project work (for example, “甲客户” +
   “一期交付项目”); cite the exact source text as `crm_customer_evidence` and
   repeat that identical citation in the Project's `evidence` list. The service performs the
   read-only CRM exact match. A title prefix alone never proves a CRM identity.
   The Project title remains unchanged and Tasks never store a copied customer.
   CRM name-resolution output is only a candidate list, never proof of exact or
   exhaustive uniqueness. Every returned customer, including a single result,
   requires explicit user confirmation before linking. No match and CRM lookup
   failure remain distinct and never create a CRM record.
3. `project_decisions` independently registers a Project or updates its existing
   active `anchor_id`. Its evidence is nonempty original proof. `context=null`
   only adds proof; otherwise return the complete current snapshot, not a partial
   patch. Project can have zero Tasks. Never create or update a Task just to carry
   Project facts, risk evidence, owner information or Attention.
   For a report containing multiple Projects, create a separate decision and
   assessment for each Project row; one Project's assessment does not cover
   another. A registration quote must be an exact contiguous excerpt from the
   immutable current source that defines that Project.
   For `project_link_evidence`, when the Project title and Task action occur in
   different parts of the source, cite one exact contiguous span from the title
   through the action and include the intervening source text verbatim. Never
   splice nonadjacent excerpts into one quotation.
