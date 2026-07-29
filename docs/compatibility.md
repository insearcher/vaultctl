# Compatibility contract

Machine-readable output is versioned independently from the CLI.

Current schemas:

| Output | Version |
|---|---|
| `scan` | `vaultctl.scan/v1` |
| `validate` | `vaultctl.validate/v1` |
| `graph export` | `vaultctl.graph/v1` |
| `doctor` | `vaultctl.doctor/v1` |
| `search` | `vaultctl.search/v1` |
| `context` | `vaultctl.context/v1` |
| node mutation request input | `vaultctl.node-mutation-request/v1` |
| `node plan` | `vaultctl.node-mutation-plan/v1` |
| `merge plan` | `vaultctl.merge-plan/v1` |
| `merge validate` | `vaultctl.merge-validation/v1` |
| internal mutation receipt | `vaultctl.receipt/v1` |

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

## Semantic merge plans

A `vaultctl.merge-plan/v1` payload binds the result to:

- the exact base, ours, and theirs revision IDs and source hashes;
- one normalized vault-relative Markdown path;
- the manifest digest and engine version;
- deterministic decisions, typed conflicts, and a candidate only when the
  state is `clean`.

Conflict plans always have `candidate: null`. A clean plan is still not
authorization to write; the current CLI consumes it only for read-only
prospective validation.

`vaultctl.merge-validation/v1` binds a rendered candidate source hash and
whole-vault digest to the plan. It is read-only and contains the prospective
validation issues and summary.

## Node mutation plans

A `vaultctl.node-mutation-request/v1` input describes exactly one create or
update. Create supplies a complete document. Update supplies the expected
current source hash and typed property/body changes.

`vaultctl.node-mutation-plan/v1` binds the candidate and unified diff to the
vault ID, manifest digest, engine version, target precondition, requested kind,
and whole-vault prospective validation. A `ready` plan has no validation
errors; an `invalid` plan retains the candidate and typed issues for the
caller's correction loop. Rendering or diffing either state repeats the live
precondition and prospective-state checks. Neither operation writes a file.

`vaultctl.receipt/v1` is emitted only by the internal synthetic transaction
boundary. It binds the plan and validation digests, exact revisions, manifest,
engine, before/final hashes, and applied/failed/rolled-back state. There is no
public apply command.
