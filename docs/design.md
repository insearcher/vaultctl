# Product design

## Product statement

`vaultctl` is one CLI and one engine for schema-driven Markdown vaults.
Different vaults vary through manifests, schemas, taxonomies, templates, and
filesystem or Git access—not separate implementations.

The product is agent-first and tool-oriented. It exposes deterministic,
versioned primitives that an agent or human can compose with Git, an editor,
and an optional application CLI. It does not own the surrounding workflow or
make hidden orchestration decisions.

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
the canonical model, schema, graph, validation, and safe local mutation
contracts remain owned by `vaultctl`. Git commands and repository workflow
remain under the caller's control.

## Agent-first orchestration

The normal integration boundary is an agent skill or human procedure:

1. inspect repository and conflict state with ordinary Git tools;
2. call `vaultctl` for normalized data, a semantic plan, or validation;
3. decide whether to accept a deterministic candidate or edit the note using
   filesystem/editor/application tools;
4. validate the resulting vault with `vaultctl`;
5. stage, commit, push, retry, or ask for approval using ordinary Git and
   forge tools.

Consumer-owned skills and prompts define sequencing, authority, and domain
policy. They may combine `vaultctl` with Git or an application CLI, but those
workflows do not become hidden behavior inside the core.

The core does not install Git hooks or merge drivers, run Git automatically,
commit or push, invoke an LLM, or encode agent orchestration. A custom driver,
forge resolver, merge queue, or autonomous promoter may be reconsidered only
if observed agent workflows demonstrate a concrete need.

## Write boundary

The public CLI remains read-only. Semantic merge starts with planning and
whole-vault prospective validation; neither updates a working tree, index,
branch, or remote.

A future public write must be explicitly invoked by the caller and have two
stages:

1. A mutation plan with exact paths, expected hashes, and a diff.
2. An apply stage with confinement, validation, atomic replacement, rollback,
   and a versioned receipt.

Git push is separate from local file atomicity. Repository manifests cannot
execute hooks or arbitrary code.

## Agent-mediated semantic merge

Git and the hosting forge remain responsible for repositories, identity,
review, branch protection, and integration. An agent or human owns conflict
resolution and SCM actions. `vaultctl` supplies schema-aware three-way
planning and prospective validation as optional tools in that workflow.

The first merge milestone is deliberately narrow:

- one Markdown path and one base/ours/theirs triple;
- exact input revisions, source hashes, manifest digest, and engine version;
- deterministic frontmatter decisions;
- conservative body handling;
- typed conflicts with evidence;
- no apply behavior, Git driver, network access, or forge adapter.

Unknown and concurrent scalar changes fail closed. A manifest may declare a
field as a mathematical set; only that strategy can combine independent list
changes. Concurrent body changes always remain conflicts in v1.

The plan, conflict, validation, and receipt formats are independently
versioned. An internal synthetic-only transaction boundary proves one-path
hash preconditions, prospective validation, atomic replace, rollback, and
receipts. It is not exposed as a CLI command.

The next product milestone is explicit agent-callable write primitives for
existing create/update/relations workflows, not an automatic Git driver.
Real-vault writes remain separately gated.
