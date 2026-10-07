# Processing Reaction Implementation Plan

> **For agentic workers:** Execute inline in the isolated processing-reaction worktree.

**Goal:** Show 处理中 on accepted chat inputs until delivery or terminal processing.
**Architecture:** A small progress module stores per-source state in service_state, uses native DWS commands and reads existing task/delivery facts. Worker intake starts progress; passes recover cleanup; verified System delivery removes it immediately.
**Tech Stack:** Python, SQLite, DWS, pytest.

- [ ] Add focused failing tests in tests/test_processing_reaction.py for restart, retry, replacement, provider rejection and native remove command. Run pytest on that file and confirm failure.
- [ ] Add DwsClient.remove_message_text_emotion using the published shortcut parameters --conversation-id, --msg-id, --text, --emotion-id, --emotion-name and --background-id.
- [ ] Add ProcessingReaction with start, finish and sync operations. Store intent before adding, serialize in-process writes, persist provider template IDs and retry cleanup in normal passes. Read existing sent_replies/task status; preserve pending/processing and clear missing or terminal source tasks.
- [ ] Connect accepted worker intake to start; call sync on producer/consumer passes; finish after system_executor records verified message delivery.
- [ ] Run the new tests and affected DWS, worker, agent send tests. Update architecture/runtime documents and validate lint.
- [ ] Commit scoped changes, integrate with current main, push and deploy through app.deploy. Read back PID, health, queues, Attention and History. Report native verification separately.
