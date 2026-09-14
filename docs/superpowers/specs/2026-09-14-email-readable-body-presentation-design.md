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

## Boundaries and safety

- The backend remains the single safe-text projection boundary. The frontend
  only formats text it receives and never uses `dangerouslySetInnerHTML`.
- Links allow only `http` and `https`; every external link opens in a separate
  browsing context with `noopener noreferrer`.
- Mail containing malformed link syntax remains ordinary visible text.

## Components

`EmailBody` will be a focused presentation component shared by the drawer and
the detail's quoted-body section. `emailPreview` will be a pure formatter used
only in the list. This keeps list density independent from full-body reading.

## Verification

- Unit tests cover whitespace/invisible-character cleanup, fixed preview
  truncation, plain URLs, Markdown links, unsupported schemes, and malformed
  link text.
- Email page tests cover one-line list preview, paragraph presentation, link
  attributes, and quoted-text collapse.
- Existing Email API and store tests remain unchanged except for any necessary
  presentation fixture adjustments.
