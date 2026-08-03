---
name: vaultctl-agent
description: Use when an agent needs to inspect, search, validate, plan, apply, or resolve conflicts in a Git-backed Markdown vault through vaultctl, including context/search, one-node mutations, receipts, drift checks, semantic merge evidence, and path-scoped Git handoff. The owning consumer skill still selects the vault and defines authority, approval, validation, and publication policy.
---

# Vaultctl Agent Workflow

Use `vaultctl` as a deterministic evidence and mutation engine. Keep vault
selection, domain policy, authorization, Git decisions, and editor/application
use in the calling agent and its consumer skill.

## Establish the consumer contract

Before invoking the CLI:

1. Identify the consumer skill or repository policy that owns this vault.
2. Resolve exactly one explicit vault path and confirm its manifest.
3. Run the consumer-owned version and provenance check. If the executable is
   absent or stale, stop and use the consumer's bootstrap workflow; never
   install or update it implicitly inside a task.
4. Establish the allowed operation, write authority, required validation, and
   Git publication policy.
5. Inspect conflicts and unrelated dirty state. Preserve unrelated files and
   stop when the intended path is already owned by another unresolved change.

A manifest capability is a technical gate, not authorization. A consumer may
authorize agent-owned writes without a per-change human confirmation; follow
that consumer policy rather than inventing either permission or an extra gate.

## Choose the narrowest lane

### Read and inspect

Pass the resolved vault explicitly:

```bash
vaultctl --vault "$vault" --format text context "<query>"
vaultctl --vault "$vault" --format json search "<query>"
vaultctl --vault "$vault" --format json query \
  --path "<area>/**" --kind "<kind>" --where status=active
vaultctl --vault "$vault" --format json scan
vaultctl --vault "$vault" --format json validate
```

Use the output as evidence. Read only the notes needed for the task and defer
source-of-truth precedence to the consumer.

Use `query` for exact derived views instead of maintaining an index file. Its
filters describe node and graph facts only; PID/session ownership, Git state,
autosave, and domain-specific orphan policy remain with the consumer.

### Plan and apply one node

Keep request, plan, and receipt files in a private temporary directory outside
the vault.

1. Read the exact current note and obtain its raw source hash.
2. Construct one `vaultctl.node-mutation-request/v1` create or update request
   for an exact vault-relative path and declared node kind.
3. Plan and inspect before any write:

   ```bash
   vaultctl --vault "$vault" node plan --request "$request" > "$plan"
   vaultctl --vault "$vault" node diff --plan "$plan"
   ```

4. Require a `ready` plan, exact intended path and diff, valid prospective
   vault, and unchanged source precondition.
5. When the consumer authorizes the operation and the manifest enables
   `node-mutation-apply`, apply the already-inspected plan:

   ```bash
   vaultctl --vault "$vault" node apply --plan "$plan" > "$receipt"
   ```

6. Require an `applied` `vaultctl.node-mutation-receipt/v1`, then run:

   ```bash
   vaultctl --vault "$vault" validate
   git -C "$vault" diff -- "$target"
   git -C "$vault" status --short
   ```

7. Confirm that only planned paths changed. Stage only explicit paths when
   consumer policy permits it; never use `git add -A`.
8. Treat commit and push as separate agent-owned decisions. Clean temporary
   artifacts after retaining any receipt required by consumer policy.

A stale plan requires replanning from current state. A `failed` or
`rolled-back` receipt requires exact target read-back and validation before
deciding whether a retry is safe; never loop blindly.

### Resolve a Markdown conflict

1. Use Git to materialize exact base, ours, and theirs inputs outside the
   vault.
2. Run `vaultctl merge plan` with the target path, three inputs, and exact
   revisions.
3. Inspect decisions and typed conflicts. A clean candidate is evidence, not
   permission to write.
4. For a clean candidate, run `vaultctl merge validate` while the current
   target still matches the plan. For an edited resolution, use the available
   editor or application CLI and then run whole-vault `validate`.
5. Resolve ambiguous body or scalar conflicts with agent reasoning or human
   input according to consumer policy.
6. Review the exact Git diff, stage only resolved paths, and follow the
   consumer's commit/push and forge rules.

The public CLI has no semantic-merge apply command. Do not hide Git operations,
editor decisions, retries, or conflict choices inside a wrapper presented as
core vaultctl behavior.

## Fail closed

Stop and report evidence when:

- the vault, manifest, consumer policy, executable provenance, or required
  capability is missing;
- an exact path escapes the vault, traverses a symlink, is conflicted, or
  changed after planning;
- prospective or actual validation fails;
- a receipt cannot prove the expected final state;
- unrelated dirty work would be staged, overwritten, or cleaned;
- authority or publication policy is ambiguous.

Never place credentials, private vault contents, or consumer-specific paths in
the plugin, request examples, logs, or committed receipts.
