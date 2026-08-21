# Agent Runtime Settings Design

Date: 2026-08-21

## Goal

Move Agent Runtime configuration out of the generic system-parameter table and
make the default Codex route and optional API fallback explicit, safe to edit,
and discoverable from Settings.

## Confirmed Decisions

- Settings gets a dedicated `Agent Runtime` tab.
- The default OAuth route exposes model and thinking effort as curated select
  controls rather than arbitrary text inputs.
- The Codex API fallback exposes an enable switch, API base URL, fallback model,
  and API token field.
- An enabled fallback is represented by `codex_oauth,codex_api` in
  `CEO_AGENT_RUNTIME_ROUTES`; disabling it leaves the OAuth route only.
- The token is persisted in `.env` as `CEO_CODEX_API_KEY`, but it is never
  rendered into HTML. The page reports only whether a non-empty value exists.
- A blank submitted token preserves an already configured token. A separate
  clear action is deliberately out of scope because removing a credential is a
  materially destructive operational change.
- API base URL is persisted as `CEO_CODEX_API_BASE_URL`, normalized to an
  absolute HTTP(S) URL without a trailing slash, and used only by `codex_api`.
- Saving configuration does not restart the service itself. The normal local
  service restart applies the new environment; this delivery includes that
  restart and a live probe/readback.

## Alternatives Considered

1. Keep the fields in `System Config` as text rows. This is smallest but does
   not distinguish an Agent Runtime from unrelated worker intervals, cannot
   safely mask the token, and permits invalid thinking values.
2. Add an `Agent Runtime` card to the System Config page. Better grouping, but
   it still conflates route selection with general service state and makes the
   settings navigation misleading.
3. Add a dedicated `Agent Runtime` tab. This keeps route configuration together,
   supports appropriate controls and token handling, and is the selected design.

## Data Flow

`Settings -> Agent Runtime` posts a dedicated form. The handler validates the
selected model/effort and fallback URL, writes only approved `.env` keys, and
preserves an existing token for an empty token input. The next service process
loads these keys through `load_runtime_config`; the Codex adapter supplies the
configured API base URL only to its service-API route.

## Validation and Safety

- Model and effort values must come from application-owned option lists.
- A fallback cannot be enabled without an existing or newly supplied token.
- The fallback URL must be an absolute `http://` or `https://` URL and cannot
  contain credentials, query text, or a fragment.
- HTML, redirects, tests, and error messages must not include the token value.

## Tests

- Runtime config accepts the configured API base URL and the adapter emits it
  into the isolated service-API provider configuration.
- The settings page renders select controls, the fallback fields, and masked
  token state, with no token value in HTML.
- A valid submission updates the approved `.env` keys and enables the route.
- Invalid choices, malformed URLs, and enabling without a token are rejected
  without writing configuration.
- The full suite and a deployed settings-page readback verify the real path.
