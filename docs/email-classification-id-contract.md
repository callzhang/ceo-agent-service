# Email classification identifiers

The console list, detail and feedback response expose classification `id` as a decimal string. SQLite and Python retain integer IDs; route parameters are parsed as integers by FastAPI. JavaScript must never coerce these identifiers to Number.

Incident: persisted ID 8423079112545370073 was rounded in the browser to 8423079112545370000, causing feedback to return 404. The classification still existed and remained pending. This is an API representation change, not a database migration or a classification policy change.

Regression coverage: backend list/detail/feedback with a 19-digit identifier and actual persisted human confirmation; frontend JSON mapping and exact feedback URL. Backend Email API suite: 51 passed. Frontend API/Email page suites: 32 passed. TypeScript and production build passed.
