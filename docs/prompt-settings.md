# Prompt Settings

The Workbench `Prompts` section owns all editable agent text:

- `Developer Prompt`
- `User Prompt`
- `Distilled work profile`

The work profile is not a separate settings section. Its source is returned as
`fields.profile` from `GET /api/console/settings/prompts`; its exact runtime
injection is returned as `preview.profile`.

To update one prompt, send `POST /api/console/settings/prompts` with a
`prompt` of `developer`, `user`, or `profile`, and the edited value in
`fields.template`. Saving `profile` updates the work profile consumed by new
Consumer and Audit runs. Runs already in progress retain the context captured
when they started.
