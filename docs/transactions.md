# Prospective validation and transaction boundary

The public CLI remains read-only. This milestone adds:

- `vaultctl merge validate --plan <file>` for whole-vault prospective
  validation;
- an internal one-path transaction engine exercised only on synthetic
  fixtures;
- a versioned receipt for applied, failed, and rolled-back attempts.

There is no public `merge apply` command yet.

The internal apply function is a safety proof for future explicit,
agent-callable write primitives. It is not an autonomous writer or Git
workflow.

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

## Internal apply boundary

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

This milestone does not:

- expose a write command in the CLI;
- create, delete, rename, or modify multiple notes;
- stage or commit Git changes;
- update a Git ref or contact a remote;
- install a merge driver;
- execute repository code, hooks, templates, or network requests;
- authorize a write merely because a manifest capability is present.

Consumer rollout and human authorization remain external workflow decisions.
Real-vault write pilots require a separate gate.
