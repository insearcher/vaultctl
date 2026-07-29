# Prospective validation and transaction boundary

The transaction surfaces are deliberately narrow:

- `vaultctl node plan --request <file>` for a typed one-note create/update
  candidate and whole-vault prospective validation;
- `vaultctl node render --plan <file>` and `node diff` for exact, stale-checked
  read-only evidence;
- `vaultctl node apply --plan <file>` for an explicit capability-gated,
  one-path local create or update with rollback and a versioned receipt;
- `vaultctl merge validate --plan <file>` for whole-vault prospective
  validation;
- an internal semantic-merge transaction engine exercised only on synthetic
  fixtures.

There is no public semantic-merge apply command.

The public node apply command is a deterministic local primitive. It is not an
autonomous writer or Git workflow.

## Prospective validation

`merge validate` accepts a versioned clean merge plan. Before scanning, it
checks:

- the plan schema and deterministic `planId`;
- the candidate content digest;
- exact vault ID, manifest digest, and engine version;
- the current target's raw hash against the plan's `ours` input;
- path confinement, regular-file status, and a deny-by-default symlink rule.

The candidate is rendered over the current `ours` document. Unchanged
round-trip YAML values, ordering, quoting, and comments are retained where
possible. The rendered bytes are parsed again and must reproduce the exact
structured candidate.

The scanner then overlays those bytes in memory and validates the entire
prospective node/edge graph. No vault file is changed. The versioned result
contains the rendered source hash, a whole-vault digest, counts, and issues.

## Public node apply boundary

`node apply` supports one ready create or update plan. It requires the
manifest capability `node-mutation-apply`, an existing parent for create, and
an existing regular target for update.

The sequence is:

1. acquire a non-blocking cooperative lock on the manifest file;
2. repeat all plan, path, manifest, engine, candidate, target, diff, and
   prospective-validation checks;
3. stage bytes in the target directory and fsync them;
4. atomically create without overwrite or replace only the planned path;
5. rescan the actual vault and compare it with the prospective result;
6. emit an applied receipt, or restore the exact original state and emit a
   failed/rolled-back receipt.

Rollback proceeds only while the target still contains the exact candidate.
An unexpected concurrent target change is preserved and reported as an unsafe
rollback instead of being overwritten.

`vaultctl.node-mutation-receipt/v1` binds the plan and validation digests,
manifest and engine, exact before/final file states, and candidate source hash.

## Internal semantic-merge apply boundary

The internal transaction function deliberately supports only one existing
Markdown path. It additionally requires the manifest capability
`semantic-merge-apply`.

The sequence is:

1. acquire a non-blocking cooperative lock on the manifest file;
2. repeat all plan, path, manifest, engine, and current-hash checks;
3. render and prospectively validate the candidate;
4. stage bytes in the target directory and fsync them;
5. atomically replace only the planned path;
6. rescan the actual vault and compare it with the prospective digest;
7. emit an applied receipt, or restore the original bytes and emit a
   failed/rolled-back receipt.

The receipt binds the plan digest, prospective-validation digest, exact input
revisions, manifest digest, engine version, and before/final raw hashes.

## Safety boundary

These boundaries do not:

- expose semantic-merge apply in the CLI;
- delete, rename, create parents, or modify multiple notes;
- stage or commit Git changes;
- update a Git ref or contact a remote;
- install a merge driver;
- execute repository code, hooks, templates, or network requests;
- authorize a write merely because a manifest capability is present.

Consumer rollout and human authorization remain external workflow decisions.
Real-vault write pilots require a separate gate.
