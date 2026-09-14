# Email readable-body presentation design

## Goal

Make Email Console body text readable without executing email HTML or changing
classification, folder, unsubscribe, or training behavior.

## Scope

The Email classification list and its detail drawer use the existing safe text
projection returned by the Email Console API. No message data is rewritten.

### List preview

- Render a derived one-line preview, separate from the subject.
- Collapse whitespace and invisible formatting characters for display only.
- Use a fixed character budget with an ellipsis so every desktop row remains
  one line and has stable height.
- Do not turn URLs into active links in the list.

### Detail drawer

- Render the complete stored safe-text body as paragraphs, retaining paragraph
  boundaries rather than showing one preformatted block.
- Render URLs as safe external links with a compact visible label. Long URLs
  must not determine the drawer width; the full URL remains available from the
  link target and tooltip.
- Recognize simple Markdown-style links already present in stored mail text
  and show their label as the link text.
- Keep quoted mail collapsed in the existing details control.
- Preserve textual content; no summarization, deletion, HTML execution,
  remote image loading, or tracking-resource request occurs.

### Unsubscribe evidence navigation

For an unsubscribe event in the selected message drawer, expose two distinct
pieces of evidence that are currently persisted but not navigable from Email.

- Show a verified `Attempt` link for every attempt whose conversation and
  trigger identity match the unsubscribe email task. The link targets the
  existing `/attempts/{id}` page, where the Consumer and Audit run detail is
  already available. A run ID alone is not presented as an Attempt link.
- The initial classification-detail response continues to omit the raw
  unsubscribe entry URL. It can contain the safe entry reference and the
  verified attempt IDs, but it must never include a token-bearing URL.
- The drawer shows whether a terminal receipt has a saved entry address. A
  user must explicitly press **显示完整地址** before a dedicated endpoint returns
  that address for this one selected classification. The result is displayed
  in the drawer with a copy control; it is not fetched for a list row, logged,
  cached, or automatically opened.
- The dedicated response is `no-store`, only returns a URL from a receipt
  whose classification, immutable action identity, action plan, account,
  message, thread, and task lineage all validate together, and returns a
  non-sensitive unavailable result for an in-flight, missing, or invalid
  receipt.
- Opening the address is not part of merely viewing evidence. If an explicit
  open control is added later, it needs an additional user confirmation because
  revisiting some provider entry URLs can perform an external unsubscribe.

## Boundaries and safety

- The backend remains the single safe-text projection boundary. The frontend
  only formats text it receives and never uses `dangerouslySetInnerHTML`.
- Links allow only `http` and `https`; every external link opens in a separate
  browsing context with `noopener noreferrer`.
- A raw unsubscribe entry URL is more sensitive than normal message text: it
  is deliberately absent from generic detail/list payloads and appears only
  after the selected-drawer reveal request. The API must not emit it in logs or
  error messages.
- Mail containing malformed link syntax remains ordinary visible text.

## Components

`EmailBody` will be a focused presentation component shared by the drawer and
the detail's quoted-body section. `emailPreview` will be a pure formatter used
only in the list. This keeps list density independent from full-body reading.
`UnsubscribeEvidence` will own the explicit entry-address fetch and render
verified Attempt links from safe IDs returned by the normal detail API.

## Verification

- Unit tests cover whitespace/invisible-character cleanup, fixed preview
  truncation, plain URLs, Markdown links, unsupported schemes, and malformed
  link text.
- Email page tests cover one-line list preview, paragraph presentation, link
  attributes, and quoted-text collapse.
- API/store tests cover a verified attempt mapping, the explicit address
  endpoint's exact-receipt validation and `no-store` response, and its
  rejection of missing, in-flight, or mismatched lineage. The existing
  regression assertion that ordinary classification detail omits the raw URL
  remains in place.
- Frontend tests cover no address request before the explicit reveal action,
  rendered copyable address after a successful reveal, and each Attempt link's
  `/attempts/{id}` target.
- Existing Email API and store tests remain unchanged except for any necessary
  presentation fixture adjustments.
