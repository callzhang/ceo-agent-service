# Codex session history lookup

The reply worker stores Codex transcript line offsets so review pages can show
which files and commands were used for one decision. Runtime lookup must find
the transcript file quickly and must not scan the full local Codex history.

## Request path

Session lookup uses this order:

1. Read the small local `session_path_index.jsonl` file.
2. If the index misses, find files whose name already contains the session id.
3. If that misses too, return missing immediately.

The request path does not inspect every transcript file. Full transcript scans
only happen when explicitly refreshing the index.

## Index contents

Each index row stores:

- `session_id`
- relative or absolute transcript `path`
- file `mtime_ns`
- file `size`
- optional `line_count`

The file metadata is checked before using an index row. If the transcript has
changed, the row is ignored and rebuilt from the filename path when possible.

## Reading transcripts

Line counts are cached when available. When a fresh count is needed, the code
streams the file line by line.

Audit extraction reads only the requested line range with streaming iteration,
instead of loading the whole transcript into memory.
