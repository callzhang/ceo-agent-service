# Email feedback workspace

User selected design A: queue and reading pane. Implement with existing React controls and CSS tokens, preserving feedback API semantics.

- [x] Create PendingEmailFeedback.tsx: paginated queue, authoritative total, initial selection, explicit selection then save, local errors and duplicate-submit protection. Refill after save and clamp empty last pages. Abort obsolete list requests.
- [x] Desktop independently scrolling queue and reader; mobile queue/reader views with return control. Collapsed technical evidence, single preview and attachment metadata.
- [x] Integrate pending tab and remove obsolete pending table rendering. Format ISO, SQLite and RFC timestamps.
- [x] Automated selection/save failure/success/next-row/pagination checks; frontend suite 300 tests passed; TypeScript and production build passed. Live desktop and 390px checks verified selection, readable queue, reader, category controls, and keyboard access to save. No real classification feedback was submitted during browser verification.
- [x] Live app serves rebuilt frontend assets. Existing persisted previews still contain CSS and normalized placeholders in some records, and some stored attachment names look like {23}; UI preserves source data. Original body reconstruction is not implemented in this frontend change.
