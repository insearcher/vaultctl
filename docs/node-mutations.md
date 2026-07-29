# Typed node mutation plans

`vaultctl node` accepts one versioned create or update request, renders the
candidate in memory, validates the prospective whole vault, and returns a
versioned plan. Planning, rendering, and diffing are read-only. A separate
explicit apply command can write one ready plan when the selected manifest
opts into the technical capability.

## Create

```json
{
  "schemaVersion": "vaultctl.node-mutation-request/v1",
  "operation": "create",
  "path": "notes/new.md",
  "kind": "document",
  "document": {
    "properties": {
      "tags": ["example"],
      "related": ["[[notes/existing]]"]
    },
    "body": "# New\n"
  }
}
```

The target must be absent, vault-relative, normalized, included by the
manifest, and free of symlinked parents. Its parent directory must already
exist; `vaultctl` does not create directory structure. `kind` must be declared
by the manifest and match the candidate's classification.

## Update

Obtain `expectedSourceHash` from `scan` or by hashing the exact current bytes:

```json
{
  "schemaVersion": "vaultctl.node-mutation-request/v1",
  "operation": "update",
  "path": "notes/existing.md",
  "kind": "document",
  "expectedSourceHash": "<64 lowercase hex characters>",
  "changes": {
    "setProperties": {
      "status": "ready",
      "tags": ["existing", "reviewed"]
    },
    "removeProperties": ["draft"],
    "body": "# Existing\n\nUpdated body.\n"
  }
}
```

Every `changes` member is optional, but at least one must be present and the
result must differ from the current note. Omitting `body` preserves it.
Removing a missing property or setting and removing the same property fails
closed. Unchanged YAML values retain ordering, quoting, and comments where the
round-trip parser can preserve them.

## Plan, inspect, and render

```bash
vaultctl --vault /path/to/vault node plan --request request.json > plan.json
vaultctl --vault /path/to/vault node diff --plan plan.json
vaultctl --vault /path/to/vault node render --plan plan.json
```

`node plan` exits `0` with `state: "ready"` and `1` with `state: "invalid"`.
Both states contain the exact candidate and diff. An invalid plan exposes
schema or graph issues for an agent's correction loop.

`node render` emits exact Markdown and `node diff` emits a unified diff. Before
doing so, both commands validate the plan digest and candidate hash, then
repeat the vault ID, manifest, engine, target absence/hash, and prospective
whole-vault checks. Any drift requires a new plan.

These commands do not create directories or files, invoke an editor or
Obsidian, stage Git changes, commit, or push.

## Explicit one-path apply

A consumer that has separately reviewed this write boundary may opt its
manifest into the technical capability:

```json
{
  "capabilities": ["node-mutation-apply"]
}
```

The capability is a fail-closed compatibility gate, not user authorization.
The caller still owns approval and workflow policy.

Apply an exact ready plan explicitly:

```bash
vaultctl --vault /path/to/vault node apply --plan plan.json
```

Under a non-blocking cooperative lock, `node apply`:

1. reloads the manifest and repeats the schema, digest, vault, engine, path,
   kind, candidate, target absence/hash, diff, and prospective-validation
   checks;
2. stages the candidate in the existing target directory and fsyncs it;
3. atomically creates without overwrite or atomically replaces one file;
4. rescans the actual whole vault and compares it with the prospective result;
5. emits `vaultctl.node-mutation-receipt/v1`.

An applied receipt exits `0`. After an atomic mutation is attempted, a failure
that leaves the original state unchanged, or is successfully rolled back to
it, returns a versioned `failed` or `rolled-back` receipt and exits `1`. A
stale plan, unsafe path, missing capability, busy lock, staging failure, or
unsafe rollback fails closed and exits `2`.

The command does not create parents, delete or rename notes, mutate more than
one path, invoke Obsidian or an editor, stage Git changes, commit, push, retry,
or resolve policy and approval decisions. Real-vault enablement remains a
separate consumer rollout decision.
