# Product design

## Product statement

`vaultctl` is one CLI and one engine for schema-driven Markdown vaults.
Different vaults vary through manifests, schemas, taxonomies, templates, and
filesystem or Git access—not separate implementations.

## Source of truth

```text
Markdown notes + frontmatter = node and edge instances
.vaultctl/manifest.json       = schema, taxonomy, and policy
external generated cache      = disposable projection
```

A committed global graph file would become a merge hotspot and a second source
of truth. Per-note sidecars would create the same split-brain problem at a
smaller scale. Neither is part of the design.

## Node and edge model

```text
Node
  id
  path
  kind
  title
  properties
  tags
  source_hash

Edge
  source
  relation
  target
  provenance
```

Relations with cardinality `0..1` use a scalar value. Relations with
cardinality `0..*` always use a list. A relation that needs its own required
properties should be promoted to a Markdown node.

## Vault selection

A command resolves one vault from an explicit `--vault` path or the nearest
`.vaultctl/manifest.json` above the current directory. There are no target
profiles or personal/team aliases.

## Execution backends

The filesystem backend is always available. A live application CLI may be
used as a capability for operations where application semantics matter, but
the canonical model, schema, graph, validation, transactions, and Git behavior
remain owned by `vaultctl`.

## Write boundary

Write support is intentionally absent from the first release. A future write
must have two explicit stages:

1. A mutation plan with exact paths, expected hashes, and a diff.
2. An apply stage with confinement, validation, atomic replacement, rollback,
   and a versioned receipt.

Git push is separate from local file atomicity. Repository manifests cannot
execute hooks or arbitrary code.
