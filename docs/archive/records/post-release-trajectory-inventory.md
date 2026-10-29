# Recent trajectory inventory after compact Skill release

Production commit verified directly: `9a2a23fcaad6eb73f6131d5b7a70d22ae02418cd`. The deployment readback was captured at `2026-10-14T03:02:09Z`. At this inspection, the read-only production runtime projection contained **zero attempts started after that timestamp**. Therefore none of the runs below validates the new frozen-source discovery entries in a live business task.

The eight most recent execution/audit attempts have complete native turn record ranges and recoverable saved service inputs. Native watermarks are **line indexes**, not byte offsets. Consumer Developer input is 33,251 characters; Audit Developer input is 30,832 characters. Task input ranges from 11,927 to 27,785 characters. These lengths exclude native base instructions, tool declarations and accumulated history. Successful process status does not establish business correctness.

## Confirmed repeated source reads

Consumer session `01a1383f-2704-78c3-a4bd-7013c9cbd970` contains successive attempts `354804`, `354811` and `354820`. Each reads `ceo-message-triage` once. All three returned source bodies are exactly 7,919 characters with UTF-8 SHA256 `a2538c3804386ef89a016a03a03f92f64c72cf3f4ff9f5fea0ad5b5906f3dc38`.

Attempts `354811` and `354820` also read the same `dingtalk-chat` body: 6,717 characters, SHA256 `88647ed22020bd5b0963d002246b2a8d2b5f785746b19c31e32ecf8af2d575af`. The first attempt instead uses the installed `read_skill` entry; equality to that first body has not been established here.

The second Consumer turn reads four different Skills: message triage, calendar invitation, DingTalk chat and DingTalk calendar. Its Audit (`354815`) likewise reads those four distinct Skills. There is no duplicate same-Skill invocation within either of those turns. Separate Audit source reads are not classified as duplicate Consumer reads: Audit has its own session and evidence needs.

Whole tool-output hashes differ because the native wrapper includes invocation timing and other metadata. The repeated-content conclusion uses decoded `content` only, not wrapper equality or matching names alone.

## Large input spans needing source-level review

In the saved Consumer Developer input, the literal `Pydantic Wire Contract` heading spans 9,739 characters until `Capability Instructions`; the Work Profile heading spans 8,241 until Runtime Context. These are literal heading spans, **not recovered assembly-source receipts**. They identify review targets but do not prove that every character is redundant or justify deleting fields or profile rules. Native structured-output repair remains independently held.

## Next evidence-producing comparison

Compare the next authentic same-session runs against these repeated-body observations using exact saved inputs, content hashes, call sequence and business result. If new frozen-source entries still cause identical full-body reads, inspect the complete reasoning and active assembly instructions before changing the mechanism. Preserve current source queries and full Skill contents; do not introduce automatic tool-output suppression, history assumptions or a rule that forbids rereading. No business task was triggered solely to generate this inventory.

This inventory covers input/call structure and exact read provenance. It is not a complete semantic acceptance review of every recent task or a claim that the overall trajectory optimization goal is complete.

## Native input layers and context growth

Further inspection of the same complete native ranges distinguishes a carried Developer from a fresh submission. Attempt `354804` records 33,251 characters of service Developer, 924 of native Skill instructions, 4,380 of global AGENTS, 1,076 of environment and 11,927 of Task. Attempt `354811` records environment, 106 characters of native Skill instructions and its Task; `354820` records environment and Task. Neither continuation records a freshly submitted service Developer. Recovering the saved Developer from the session prefix must not be reported as repeated injection.

The native `last_token_usage.input_tokens` at each turn's last recorded token event is 56,173, 100,632 and 125,473 respectively. The initial cold request is 22,936. These are actual request-level counters, not character estimates; accumulated `total_token_usage` is a different session counter. Context growth contains prior inputs, outputs and tools and cannot be attributed entirely to Skill reads. Cached tokens remain a part of input context.

The original session metadata also saves a 17,730-character native base instruction text, separate from the service Developer. Its Skill section requires applicable Skills for each turn, says not to carry Skills across turns unless mentioned again, and requires a complete read before task actions. The service's older Task discovery permits reuse when the frozen revision/hash is unchanged, but that older discovery did not expose the exact full-content hash. This establishes two possible drivers of repeat reads—missing comparison metadata and native per-turn reading instructions. It does not establish which drove the model's individual choices; no readable model rationale selecting that cause was found in this inspection.

Current `CONSUMER_DYNAMIC_SKILL_BODY` requires selection/application but does not explicitly require a fresh read every turn. The much stricter `render_business_skill_protocol` precondition is used in the stable contract fingerprint; `consumer_developer_sections` discards its legacy `skill_protocol` argument. Do not attribute that unused rendered precondition to the current submitted Developer.

Official configuration reference confirms that `model_instructions_file` replaces built-in instructions: https://learn.chatgpt.com/docs/config-file/config-reference . It is not an AGENTS disabling option. No such replacement, native-home change, shared-rule edit, automatic output suppression or permission change was made. Any candidate must be tested against actual native inputs and business behavior, with non-business coding prose separated from authority/permission rules; accepting the configuration key alone would not prove behavior or same-session semantics.
