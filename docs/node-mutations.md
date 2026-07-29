# Typed node mutation plans

`vaultctl node` exposes the read-only half of a future explicit write
contract. It accepts one versioned create or update request, renders the
candidate in memory, validates the prospective whole vault, and returns a
versioned plan. It never changes the vault.

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
manifest, and free of symlinked parents. `kind` must be declared by the
manifest and match the candidate's classification.

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
Obsidian, stage Git changes, commit, push, or provide an apply operation.
