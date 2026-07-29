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

Inspection, explicit merge planning, and whole-vault prospective validation
remain read-only. The only public write surface is the opt-in Git custom
driver. It may atomically replace Git's single `%A` file after a clean plan
and path-local candidate verification. It never stages, commits, updates a
ref, or contacts a remote.

A future write must have two explicit stages:

1. A mutation plan with exact paths, expected hashes, and a diff.
2. An apply stage with confinement, validation, atomic replacement, rollback,
   and a versioned receipt.

Git push is separate from local file atomicity. Repository manifests cannot
execute hooks or arbitrary code.

## Git-native semantic merge

Git and the hosting forge remain responsible for repositories, identity,
review, branch protection, and serialized integration. `vaultctl` owns only
schema-aware three-way planning and prospective validation.

The first merge milestones are deliberately narrow:

- one Markdown path and one base/ours/theirs triple;
- exact input revisions, source hashes, manifest digest, and engine version;
- deterministic frontmatter decisions;
- conservative body handling;
- typed conflicts with evidence;
- no general apply behavior, network access, or forge adapter.

Unknown and concurrent scalar changes fail closed. A manifest may declare a
field as a mathematical set; only that strategy can combine independent list
changes. Concurrent body changes always remain conflicts in v1.

The plan, conflict, validation, and receipt formats are independently versioned.
An internal synthetic-only transaction boundary proves one-path hash
preconditions, prospective validation, atomic replace, rollback, and receipts.
It is not exposed as a CLI command.

The separately gated custom driver reuses the same deterministic plan but
follows Git's protocol: it changes only `%A`, returns `0` for clean, `1` for a
typed conflict, and `2` for an error. Its v1 receipt contains hashes and
conflict locations, never note values. Full-vault validation runs after Git
has assembled the complete candidate because a per-file driver cannot
correctly validate paths that Git has not merged yet. Real-vault rollout
remains a later gate.
