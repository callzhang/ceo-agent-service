# Pending email reading and classification evidence

The list response contains a redacted, 280-character summary, not the message
body. The selected email now loads `message_text` from the classification detail
response, sourced from the existing `email_messages.normalized_text` snapshot.
The list remains lightweight. The reader preserves line breaks, renders plain
text rather than executable email HTML, and displays the persisted recipient
list separately in the message header.
Recipient loading, errors and absent values are explicit, and selecting another
message never displays the previous message's recipients.
Missing snapshots and failed requests have explicit
states; changing selection aborts the previous request. Attachment contents are
not read. No messages are marked read or classified by opening the reader.

Classification evidence is always expanded: one stacked horizontal probability
bar, category-colored legend with numeric probabilities, margin and model/config
identifiers. This is model evidence, not an explanation generated after the fact.

Verification: email API attachment/detail regression verifies full text is only
included in the detail response; frontend tests verify non-collapsed evidence,
the probability bar, and loading/display of text longer than the list preview.
Existing saved content is displayed as stored; this change does not reconstruct
formatting already lost during ingestion.
