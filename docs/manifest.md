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
    "fallbackToTitle": true,
    "outputFields": ["status", "updated"]
  }
}
```

Available zone sources are `title`, `firstHeading`, `stem`, `path`, `tags`,
`property`, `properties`, `headings`, and `body`. `firstHeading` uses the first
Markdown heading regardless of level and falls back to a humanized filename
stem where hyphens and underscores become spaces.
`tags` includes both frontmatter tags and inline Markdown tags. Only
`property` takes a `field`.

The built-in stop-word set is enabled by default. Set
`"useDefaultStopWords": false` when a vault must replace it completely with
its own `stopWords` list; otherwise configured words extend the defaults.

Search policy may add a fixed boost after a node has matched at least one
query zone. Each boost selects either one manifest node kind or one
vault-relative path glob:

```json
{
  "search": {
    "boosts": [
      {
        "kind": "guide",
        "weight": 4
      },
      {
        "path": "notes/**/README.md",
        "weight": 5
      }
    ]
  }
}
```

Boosts never create a hit by themselves. They only refine deterministic
ordering among nodes that already match the query.

When no search configuration is present, `vaultctl` uses a stable built-in
general-purpose scorer. Context reuses the search ranking and adds matching
body lines without exceeding the manifest's content-character budget. When a
matching body line is absent, `fallbackToTitle` controls whether the note title
is returned as a fallback snippet.

Context may also group ranked notes without changing search scoring:

```json
{
  "context": {
    "grouping": {
      "fields": ["topic", "ticket"],
      "pathToken": "ticket",
      "keyCase": "upper",
      "statusField": "status",
      "inactiveStatuses": ["archived", "superseded"],
      "freshnessFields": ["updated", "created"],
      "notesPerGroup": 2
    }
  }
}
```

The first non-empty configured field becomes the group key. The built-in
`ticket` path token recognizes conventional ticket IDs with at least two
digits case-insensitively, stops before a descriptive suffix, and also
recognizes `adhoc-YYYY-MM-DD-*` IDs. `keyCase` optionally normalizes both
explicit and path-derived keys; notes without either key source fall back to
their own path.
Groups rank by their best search hit. Within a group, non-inactive notes come
first, followed by the freshest ISO date, score, and path. The response keeps
both the freshness-selected `representative` and a distinct relevance-selected
`topMatch` when needed. `outputFields` is an explicit allowlist of frontmatter
properties to project into context hits; unspecified properties stay internal.

## Semantic merge policy

Merge policy is declarative and optional:

```json
{
  "merge": {
    "defaultFieldStrategy": "manual",
    "fields": {
      "status": {"strategy": "scalar"},
      "tags": {"strategy": "set"},
      "related": {"strategy": "set"}
    },
    "bodyStrategy": "manual"
  }
}
```

Phase 0 supports three field strategies:

- `manual`: ordinary three-way behavior; different concurrent changes conflict.
- `scalar`: the same conservative behavior, with explicit scalar intent in the
  plan.
- `set`: list values are treated as unordered unique JSON values. Independent
  additions and removals are combined deterministically; a non-list value
  produces a typed conflict.

All strategies accept an unchanged side or the same change on both sides.
`defaultFieldStrategy` may be `manual` or `scalar`; set behavior must always be
opted into per field. `bodyStrategy` is fixed to `manual` in manifest v1.

The manifest cannot name commands, hooks, modules, or merge-driver
executables. It describes policy only.

The internal synthetic transaction boundary additionally checks for the
capability ID `semantic-merge-apply`. This is a technical fail-closed gate,
not user authorization and not a public write interface. Consumer manifests
should not enable it before a separately reviewed real-vault pilot.
