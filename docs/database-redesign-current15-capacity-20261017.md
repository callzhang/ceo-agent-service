# Current15 same-input capacity, 2026-10-17

Accepted migration correctness is separate from space acceptance. This is a derivative of the consistent-copy receipt f4bc7d65, not a new production measurement. Readonly source83c1d7, stages116→15→116→15,775.246s. Root independently verified26caller/10requirements/369runtime pins and absence of the managed temporary copy after EXIT0.

Source allocated pages: **502,599,680 B (502.60 MB)**. First target before synthetic writes: **562,970,624 B (562.97 MB)**. Same-input increase: **60,370,944 B**. Final target after current+old synthetic fixtures: 563,146,752 B; don't use that as the same-input comparison. Both source and first target have zero freelist pages. These are SQLite allocated-page bytes, not an assertion about arbitrary live WAL/main-file sidecar sizes.

| Allocation category | Source bytes | First target bytes | Delta bytes |
|---|---:|---:|---:|
| table | 400,424,960 | 440,934,400 | +40,509,440 |
| index | 101,752,832 | 119,943,168 | +18,190,336 |
| internal | 421,888 | 2,093,056 | +1,671,168 |

The category delta accounts exactly for all60,370,944B. It does not prove that serializer key bytes alone caused the table growth, or that every byte is removable. No VACUUM, retention change, original-body retirement or source mutation occurred in this proof.

| Target owner | Table bytes | Index bytes | SQLite schema/internal bytes | Total MB |
|---|---:|---:|---:|---:|
| actions | 177,684,480 | 33,947,648 | 0 | 211.632 |
| runs | 83,865,600 | 51,859,456 | 0 | 135.725 |
| tasks | 61,153,280 | 10,878,976 | 0 | 72.032 |
| email_classifications | 37,314,560 | 3,399,680 | 0 | 40.714 |
| email_messages | 34,734,080 | 1,609,728 | 0 | 36.344 |
| provider_state | 14,364,672 | 12,783,616 | 0 | 27.148 |
| business | 10,469,376 | 1,290,240 | 0 | 11.760 |
| state | 6,561,792 | 2,707,456 | 0 | 9.269 |
| configs | 7,348,224 | 1,388,544 | 0 | 8.737 |
| business_source_documents | 7,340,032 | 24,576 | 0 | 7.365 |
| sqlite_schema | 0 | 0 | 2,093,056 | 2.093 |
| email_historical_classification_history | 65,536 | 20,480 | 0 | 0.086 |
| schedules | 16,384 | 8,192 | 0 | 0.025 |
| business_legacy_links | 4,096 | 16,384 | 0 | 0.020 |
| email_historical_operations | 4,096 | 8,192 | 0 | 0.012 |
| sqlite_sequence | 4,096 | 0 | 0 | 0.004 |
| email_schema_migrations | 4,096 | 0 | 0 | 0.004 |

Remaining native families are email_classifications, email_messages, email_historical_classification_history, email_historical_operations, business_source_documents and business_legacy_links. The target has eight final core names plus the Email ledger; canonical emails and provenance are still absent. The next bounded history consolidation can reduce15→13; canonical one-row-per-email gives12; source/legacy inline or shared provenance closure gives11. This arithmetic is a plan, not implemented acceptance.

Keep the required provider current inventory, adopted business results, execution receipts and unresolved dependencies. Space work remains open: canonical Email duplication, exact-source-backed input retirement, source body/version availability and representation/index cost. The proposed30day successful-poll policy is still unconfirmed and was not applied. A one-row-per-email redesign must preserve real identities and address current training/search dependencies rather than treating normalized_text and model_text as identical.
