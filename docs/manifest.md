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

- `path`: vault-relative glob (`*` and `?` stay within one path segment;
  `**` may cross directories)
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

## Frontmatter compatibility

Frontmatter is strict YAML by default. A vault migrating from a line-oriented
legacy parser may opt into one narrow compatibility rule:

```json
{
  "frontmatter": {
    "allowLegacyColonScalars": true
  }
}
```

When strict parsing fails, this permits an unquoted top-level scalar containing
`: `, such as `description: Use when route: fallback`. Structured values,
nested mappings, and all other YAML errors remain strict. New vaults should
quote such scalars instead of enabling this option.

## Search and context

Search is a deterministic sum of configured zones. A zone chooses one source,
adds `weight` for each matching query token up to `countCap`, and optionally
adds `phraseWeight` for the complete query phrase.

```json
{
  "search": {
    "defaultLimit": 20,
    "maxLimit": 100,
    "stopWords": ["with"],
    "zones": [
      {
        "source": "stem",
        "weight": 12,
        "phraseWeight": 20
      },
      {
        "source": "property",
        "field": "description",
        "weight": 10,
        "phraseWeight": 16
      },
      {
        "source": "body",
        "weight": 1,
        "phraseWeight": 8,
        "countCap": 6
      }
    ]
  },
  "context": {
    "defaultLimit": 8,
    "maxLimit": 20,
    "maxCharacters": 12000,
    "snippetLines": 2,
    "snippetCharacters": 220,
    "fallbackToTitle": true
  }
}
```

Available zone sources are `title`, `stem`, `path`, `tags`, `property`,
`properties`, `headings`, and `body`. Only `property` takes a `field`.

When no search configuration is present, `vaultctl` uses a stable built-in
general-purpose scorer. Context reuses the search ranking and adds matching
body lines without exceeding the manifest's content-character budget. When a
matching body line is absent, `fallbackToTitle` controls whether the note title
is returned as a fallback snippet.
