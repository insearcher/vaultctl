# Manifest v1

Every vault has one `.vaultctl/manifest.json`.

```json
{
  "$schema": "https://raw.githubusercontent.com/insearcher/vaultctl/main/src/vaultctl/schemas/manifest-v1.schema.json",
  "apiVersion": "vaultctl/v1",
  "vaultId": "example-vault",
  "defaultKind": "document",
  "nodeKinds": {
    "document": {
      "selectors": [
        {"path": "notes/**"}
      ],
      "fields": {
        "tags": {"type": "list"}
      }
    }
  },
  "relations": {
    "related": {
      "field": "related",
      "cardinality": "0..*",
      "targetKinds": ["document"]
    }
  }
}
```

## Selectors

A node kind may have several selectors. Any selector may match; all conditions
inside one selector must match.

- `path`: vault-relative glob
- `type`: exact value of the frontmatter `type` field
- `tag`: required normalized tag
- `hasField`: required frontmatter field

Matching more than one kind is an error. If no kind matches, `defaultKind` is
used when explicitly configured.

## Fields

The first release supports structural field checks:

- `string`
- `integer`
- `number`
- `boolean`
- `object`
- `list`

A field may also be `required` or declare an `enum`.

## Relations

A relation maps a frontmatter field to a typed graph edge. `0..1` expects a
scalar; `0..*` expects a list. Targets are resolved as vault-relative paths,
relative Markdown links, or unambiguous note names.

The v1 manifest is intentionally small. Search policy, write policy, templates,
and projections will be added only with the feature that consumes them.
