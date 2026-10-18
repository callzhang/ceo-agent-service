# Email visible-body search after body retirement: bounded source analysis

**Status:** source research only, unselected. This note narrows unit B of `docs/superpowers/plans/2026-10-18-email-body-training-lifecycle-source-plan.md` to the Console classification list/search and its body display. It does not authorize an implementation, a physical body retirement, or a provider claim. The approved stage-4 target is 10 core tables plus migration ledger, with completed accurately retrievable original bodies read from their source instead of kept as long-term DB copies. Active recovery/uncertain effects, decisions, receipts and current model inputs retain their own actual dependency lifetimes (`docs/superpowers/specs/2026-10-11-database-redesign-design.md:165–174`).

## Current exact contract

`GET /api/console/email/classifications` accepts `q` (maximum 500 characters), `status`, page/page_size, and category/action/source filters (`app/web_api/email.py:1300–1388`). The API trims `q` before passing it to either `EmailStore.list_classifications` or, for the `unsubscribe` tab, `list_unsubscribe_classifications`. Both call the **same** `_classification_search` (`app/email_store.py:13203–13269`, `13385–13402`, `13404–13489`). The Store itself performs `q.strip().casefold()`; a blank result adds no search clause.

For nonblank `q`, one classification matches when **any** of the following contains the complete query as a Python-Unicode-casefolded, contiguous substring, using SQLite `instr(..., ?) > 0`:

1. `classifications.sender`;
2. `classifications.subject`;
3. the *rendered visible body* of the `EMAIL_MESSAGE_SQL` row with the same `(account_id, stable_message_identity)`, obtained by `Parser().parsestr(normalized_text or "")`, `_display_message_body`, then `.casefold()`.

This is a literal substring, not `LIKE`, FTS token search, whole-word search, regex, a match against `preview`, `model_text`, recipients, MIME headers as separate fields, or a match against raw `normalized_text`. The `EXISTS` means a missing joined message supplies no body hit. Python `casefold` can expand characters (the probe below shows `Straße` matching `STRASSE`); SQLite `instr` then tests the transformed strings. The three query parameters are identical (`app/email_store.py:13385–13402`). The same predicate is included in both the **SQL count** and **SQL page** query; category/action/source and status filtering is applied to both. Normal list status `all` means `processed` plus `pending_feedback`, a specific status narrows it, and `unsubscribe` first limits to the union of durable claim/receipt and, when task tables exist, queued unsubscribe task identities. Pages are ordered `updated_at DESC, id DESC`, then `LIMIT/OFFSET` (`13203–13269`, `13404–13489`). `tests/test_email_web_api.py:1109–1134` already asserts literal `%`, injection-like text, Unicode body search, case-insensitive sender/subject hits, total before page slicing, and page 2; `:2561–2579` asserts unsubscribe rows and totals share the predicate.

For list rows, the Store selects `messages.normalized_text` and renders `message_text` with the **same** `Parser` + `_display_message_body` after the SQL page has been selected; detail uses it too (`app/email_store.py:13244–13269`, `13461–13489`, `13638–13668`). Thus count membership, page order, visible body and detail are related contracts. Today a null/missing joined body becomes `""` for display; once body is intentionally retired, treating source unavailability as that empty body would falsely report an empty original.

## Render transform and finite synthetic probe

`_display_message_body` takes a parsed message payload. A non-string payload returns `""`. `Content-Type: text/html` uses `html_to_text`; other string payloads use `visible_email_text` (`app/email_store.py:241–247`). Both functions use the real `_HTMLTextExtractor` in `app/email_html_text.py:9–91`, with hidden `head`, `script`, `style`, `template` removed. The HTML path compresses whitespace. The plain path removes a recognized leading stylesheet and truncated internal redaction markup, and when it sees valid HTML tags it projects block text; without markup it preserves the plain payload. The normal scan writer stores `From/To/Cc/Subject`, blank line, then `markdownBody` or `textBody` (`app/email_classifier_scan.py:1075–1093`); other writer paths can store body text directly (`app/email_worker.py:296–303`, `app/email_training_labeler.py:266–273,373`). Consequently the current searchable body is specifically the *stored-form render*, and the header-like prefix of a normal scan is parsed away. Current provider reconstruction does not by itself prove byte or render parity for all historical rows.

I imported the actual functions without constructing `EmailStore`, used `sqlite3.connect(':memory:')`, registered the actual `_classification_search` functions, and evaluated `instr(email_visible_body(?), ?) > 0` on synthetic text. Results:

| Synthetic stored `normalized_text` form | Actual `_display_message_body` | Search implication |
| --- | --- | --- |
| `Alpha\n\nBETA` | `Alpha\n\nBETA` | plain newlines retained; casefolded substring applies |
| `From: ghost@example.test\nSubject: Ghost\n\nActual body` | `Actual body` | `ghost` does **not** hit body alone; separate classification sender/subject may still hit |
| `Content-Type: text/html; charset=utf-8\n\n<html><head><style>.hiddenneedle{}</style></head><body><p>Hello <strong>world</strong>.</p></body></html>` | `Hello world.` | `lo wo` hits; `hiddenneedle` does **not** hit body |
| `Hel<strong>lo</strong> world` | `Hello world` | `lo wo` hits even though that contiguous substring is absent from the stored text |
| `/* CSS */\n@media screen { .hiddenneedle {} }\nVisible text` | `Visible text` | `hiddenneedle` does **not** hit body |
| `MIME-Version: 1.0\nContent-Type: multipart/alternative; boundary=x\n\n--x...` | `""` | parsed multipart payload is non-string, so an apparent enclosed text part does **not** hit body |
| `Straße` | `Straße` | query ` STRASSE ` becomes `strasse` and hits after casefold |

The probe is a normalization/SQLite-function control, not a public Store test or provider test. Existing public render tests cover HTML style and stylesheet removal (`tests/test_email_store.py:930–985`); they do not settle every search case above. The MIME and markup examples are **observed code behavior on synthetic inputs**, not claims that those forms exist in production rows. The `lo wo` example establishes why a candidate generator based only on raw contiguous text is not complete for this local contract. A provider `SEARCH TEXT` implementation is not proven either complete or equivalent by these inputs: server search and MIME interpretation vary outside this code, and this unit made no provider request.

## Source and index boundary

The current readonly IMAP adapter's `fetch_uid_batch` issues UID/UNSEEN/SINCE searches, slices to `limit`, and fetches bounded decoded text sections (`app/email_imap_readonly.py:447–504`, `516–576`). It exposes no Console substring search and no public exact-body-by-locator operation. Action `find_states` searches **Message-ID headers** and fetches headers for state/readback, which is not a body query (`app/email_provider_actions.py:599–640`). The canonical `emails` DDL still requires `message_normalized_text NOT NULL`; its four listed indexes cover classification status/order and account/locator, not a body substring expression (`app/email_canonical_definitions.py:87`, `109–114`). These existing indexes are useful to enumerate filtered classifications in stable order, but do not answer body membership. An FTS table or persistent normalized search body would be a new long-term original-derived copy and cannot be silently introduced as an index-shaped workaround. A new privacy-bearing search structure or changed search semantics would need a separate explicit design choice.

## Complete path before selecting retirement

The source-derived complete algorithm is an **exhaustive, identity-checked resolver over the finite filtered classification set** for nonblank `q`:

1. With a short DB read, enumerate classification IDs, account/stable identity, locator and sender/subject in current status/category/action/source scope and `updated_at DESC, id DESC` order. Preserve current unsubscribe eligibility. Close the DB connection before provider I/O. Do not take a first SQL page and search only those rows: later body hits would be omitted from both total and early pages.
2. Mark sender/subject matches locally with exact `casefold`/substring. For every remaining candidate, obtain the **exact current original** through the separately specified identity-checked source reader, render with the existing `Parser` + `_display_message_body` contract or a proved equivalent projection, then test the same casefolded substring. Deduplicate the OR result by classification ID. A candidate source may be a validated version-bound current observation only if it actually reconstructs this readable body; the training snapshot's normalized model input is not automatically that source (`source-plan.md:9–13,20`).
3. Count all matches, then page the ordered matched IDs. Resolve the page's displayed body with the same identity/provenance rules and return explicit unavailable/error state where appropriate. Since provider data can change during a scan, recheck identity/locator and candidate-set version before claiming exact total/page parity. If a body-bearing candidate cannot be resolved, the server cannot know whether it matched or what the exact total is; an explicit incomplete/error response is required until a product contract for partial search is approved. A silent metadata-only result or `""` body is not parity.

This algorithm is logically complete **only for candidates whose exact source and equivalent rendering are available**. Its cost is at least one eligibility enumeration plus source reads for every filtered classification that does not already match sender/subject; provider latency, rate limits, folder inventory, moving/deleted mail and consistency over pagination remain unmeasured. It can be implemented and differential-tested while the DB body still exists. The bounded next implementation unit is a read-only search coordinator/fake-source test slice for the two list methods, keeping the DB body as the oracle: compare matched IDs, total, order, pagination and displayed body over the synthetic plain/HTML/MIME/casefold corpus and unavailable/identity-mismatch cases; measure filtered-row count/source-call count/latency on an authorized isolated rehearsal. Only after a completeness and performance gate should body retirement for those rows be selected. For unavailable exact sources, retain the current body while it is still needed for the existing search contract or explicitly change the product search contract in a separate decision.

A provider prefilter (including `SEARCH TEXT`) could later reduce source calls **only if** a finite differential proof establishes that its candidate set is a superset of *every* current visible-body match across supported providers and stored-body forms, with stale locator/move handling. Verify candidates with the local renderer after retrieval; never equate provider hits with final matches. This is a conditional optimization, not a proven current option. A metadata-only search is a behavior downgrade; a durable full-text/body shadow conflicts with the approved body-retirement objective.

## Evidence boundary and pin

Read-only source inspection and the one in-process `:memory:` normalization/SQLite-function probe were the only execution in this unit. No real database, mailbox, provider, network, native CLI, copy, Store constructor, runtime code/test change, stage, commit, push or deploy was used. No body availability, production query distribution, search latency, provider-search superset, or physical retirement is claimed.

At the source read, `git rev-parse HEAD` was `8eae51e05af4a47032622680aaec2b689a6e13f8`. SHA-256 of `app/email_store.py` was `0f8b56c593dce0ca6912394fa01da347e302608cc387ded7f97fcf40feedfdf1`; because this working tree is concurrent, use the more local AST pins before implementation: `_display_message_body` `8b759d6a5b4c6a0c`, `_classification_search` `b2cc8c9cd16d7dee`, `list_classifications` `5c5ee129a3078282`, `list_unsubscribe_classifications` `f7b25ba4afd48b7a`, `get_classification` `a0cd3169fbaec01b`; `visible_email_text` `545aa0ee0f3c5966`, `html_to_text` `3c099cc43dd6b6a7`, `_normalized_message_text` `99d7673cde96dfde`. Pins are SHA-256 prefixes of `ast.dump(node, include_attributes=False)` and describe only the named functions.


Current implementation boundary: metadata-only candidate DAO and source-backed
Console list/detail/search coordinator now exist, with differential tests and an
actual fake-IMAP page/detail bridge (current acceptance batch 50 passed/55.96s).
They are not selected by registered Console routes. Final-copy operation counts
show 9,261 source body checks for the literal query 合同 in the 9,287-row all
scope after local sender/subject matches. Current per-message sessions make this
an unresolved performance/product choice; no real provider latency was measured.
Derek has been asked whether to retain sender/subject search only or require the
full source-backed search with further batching/latency work. No answer has been
inferred and no search contract or original-body lifecycle changed yet.
