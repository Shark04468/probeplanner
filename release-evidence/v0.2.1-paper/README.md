# v0.2.1 paper validation evidence

`ProbePlanner-v0.2.1-paper-immutable-evidence.zip` preserves the original v4,
post-hoc remediation, historical regression, v5, and exact-oracle records.
`IMMUTABLE_EVIDENCE_MANIFEST.json` binds every ZIP member by SHA-256.

Historical absolute paths inside the ZIP are retained only because changing
them would alter frozen evidence. They are provenance fields, not executable
configuration. Active release code accepts paths through arguments or
environment variables.

The archive intentionally excludes cache directories, smoke outputs, and
checkpoint files.
