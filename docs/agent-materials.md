# Agent Material Readers

Downloaded task materials remain inside the service-owned temporary material
directory. `agent_cli.read_text_file` returns bounded text for plain text, PDF,
workbook, and presentation files without granting an Agent shell access.

For PDFs, the reader returns page-indexed extracted text, the combined bounded
content, page count, and a SHA-256 digest. Encrypted, malformed, oversized, or
overly long documents are rejected with a typed material-read error instead of
being treated as readable evidence.
