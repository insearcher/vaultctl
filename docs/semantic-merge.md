# Semantic merge

`vaultctl merge plan` is the Phase 0 read-only, deterministic three-way
planner for one Markdown path. It is the contract spike before any Git merge
driver or public write support.

## Command

```bash
vaultctl --vault examples/basic-vault merge plan \
  --path notes/example.md \
  --base triples/base.md \
  --ours triples/ours.md \
  --theirs triples/theirs.md \
  --base-revision aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --ours-revision bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb \
  --theirs-revision cccccccccccccccccccccccccccccccccccccccc
```

The three input files are read but never changed. Revision IDs are supplied by
the caller because the core planner is independent from a Git checkout or
forge API.

## Conservative three-way rules

For each frontmatter field and for the Markdown body:

1. identical values remain unchanged;
2. the same change on both sides is accepted;
3. when one side equals base, the other side is selected;
4. different concurrent changes produce a typed conflict;
5. only a field explicitly declared as `set` may combine concurrent list
   changes.

Set candidates keep surviving base values in their original order and append
new values in canonical order. Removal wins for a base value removed by either
side. Duplicate values are collapsed. Non-list input under a set policy fails
closed.

The body is always manual in v1. Different concurrent body edits are never
auto-merged.

## Output

Every plan contains:

- `schemaVersion` and deterministic `planId`;
- vault ID, path, engine version, and manifest digest;
- exact revision and source hash for each input;
- field/body decisions;
- typed conflicts with base/ours/theirs evidence;
- a candidate with a content digest only when the plan is clean.

The candidate is structured evidence, not an applied file. Conflict plans
always omit it by returning `null`.

## Prospective validation

Save a clean plan as JSON, then validate it without changing the vault:

```bash
vaultctl --vault examples/basic-vault merge validate --plan plan.json
```

The command verifies the plan, exact manifest/engine/current target hash,
renders the candidate over the current `ours` document, and scans the entire
prospective vault through an in-memory overlay. The result uses
`vaultctl.merge-validation/v1`.

See [the transaction boundary](transactions.md) for the validation and
synthetic apply invariants.

## Explicit non-goals

The public merge CLI does not:

- write a Markdown file;
- update the Git index, working tree, ref, or remote;
- run `git merge` or install a custom merge driver;
- connect to GitHub, Bitbucket, GitLab, or Forgejo;
- execute repository-provided code;
- invoke an LLM to resolve conflicts.

## Corpus gate

The public synthetic corpus contains 50 fictional base/ours/theirs cases:
scalars, missing versus null, additions, deletions, type mismatches, set
semantics, body conflicts, and combined changes.

The Phase 0 gate is:

- no expected conflict may produce a clean plan (`false-clean = 0`);
- every output validates against `merge-plan-v1.schema.json`;
- repeated inputs produce byte-equivalent payloads;
- declared symmetric set cases produce the same candidate when ours and theirs
  are swapped;
- the public-tree leak check remains green.
