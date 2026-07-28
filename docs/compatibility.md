# Compatibility contract

Machine-readable output is versioned independently from the CLI.

Current schemas:

| Output | Version |
|---|---|
| `scan` | `vaultctl.scan/v1` |
| `validate` | `vaultctl.validate/v1` |
| `graph export` | `vaultctl.graph/v1` |
| `doctor` | `vaultctl.doctor/v1` |

Consumers must check `schemaVersion` instead of inferring compatibility from
the CLI version.

## Shadow comparison

Migration from an existing tool should remain read-only until:

1. The same Markdown files produce the expected stable node IDs.
2. Every graph difference is classified.
3. Validation differences are understood.
4. Existing context/search golden cases pass after those commands exist.

Real vault data and snapshots must stay outside this repository. This project
contains only synthetic fixtures; each consumer owns its private compatibility
runner and expected results.

Strict YAML remains the default frontmatter contract. Existing line-oriented
vaults may enable `frontmatter.allowLegacyColonScalars` while migrating; the
fallback is limited to unquoted top-level scalar values containing `: `.
