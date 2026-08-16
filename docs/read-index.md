# Incremental read index

`search`, `context`, `query`, `read`, and `neighbors` serve repeated reads
from a disposable SQLite cache instead of reparsing every note. Markdown
files and the manifest remain the only source of truth; the cache is derived
state that can be deleted at any time.

## Cache location

The cache never lives inside the vault. Writing derived state into the vault
would make a clean working tree dirty and break consumer freshness gates, so
the database is stored under the user state directory:

```
<state>/vaultctl/<key>/index.db
```

- `<state>` is `$VAULTCTL_STATE_DIR` when set, otherwise
  `$XDG_STATE_HOME/vaultctl`, otherwise `~/.local/state/vaultctl`;
- `<key>` is the first 16 hex characters of the SHA-256 of the vault root's
  real path, so distinct vaults never share a database;
- the `meta` table additionally records the `vaultId`, root, engine version,
  manifest digest, and cache schema version.

`vaultctl index status` prints the exact path, size, row counts, and
staleness for the selected vault. `vaultctl index rebuild` discards the
database and rebuilds it from a full parse.

## Incremental invalidation

Every cached read starts with a fast `os.scandir` walk that stats each
included `*.md` path (honoring manifest ignore patterns) and compares
`(mtime_ns, size)` against the `files` table:

- changed or new files are reparsed individually;
- deleted files are evicted together with their node, postings, and edges;
- any parse, classification, and field issues are cached per file.

Graph edges cannot be finalized per file because link targets resolve against
the whole vault, so after any file-level change the cached notes are
reassembled through the same engine code path as a full scan: edge
resolution, unresolved-target issues, and cycle checks run again and the
`edges` table plus the issue list are rewritten.

A mismatch of the engine version, manifest digest, or cache schema version
invalidates the whole database and triggers a full rebuild.

## Transparent fallback

The cache is disposable by design. Any SQLite, schema, or filesystem error
falls back silently to a full `scan_vault`; a corrupted database file is
recreated from scratch. Cached commands never fail because of the cache.

The global `--no-cache` flag forces a full scan and neither reads nor writes
the cache. `scan`, `validate`, and `graph export` always run a full parse.

## Two-stage search

Cached search is a candidate/rescore pipeline:

1. **Candidates** come from the `postings` table (term, path, zone, count).
   Query tokens match postings by prefix, and — when stemming is enabled —
   by Snowball stem at weight `0.8`. Each match contributes
   `zoneWeight × min(count, countCap) × idf`, where
   `idf = ln(1 + (N - df + 0.5) / (df + 0.5))`.
2. **Rescoring** loads only the top 200 candidates as full nodes and applies
   the exact manifest zone scorer, including phrase bonuses and boosts, so
   the final ranking keeps the established search semantics.

With stemming disabled and freshness weight `0`, cached results are identical
to `--no-cache` results for token queries. Candidate recall is term-based:
a query token that appears only in the middle of longer words (and shares no
stem or prefix with them) can be missed by the candidate stage, while the
full scan's substring scorer would still match it.

## Stemming

Every indexed token is stored twice: verbatim and as a `s:`-prefixed
Snowball stem — `russian` for Cyrillic tokens, `english` for Latin tokens.
A query token matches exact occurrences at weight `1.0` and stem-equivalent
occurrences at `0.8`, so «созвоны» finds «созвон» without diluting exact
matches. Stemming is on by default and can be disabled in the manifest:

```json
{
  "search": {
    "stemming": {"enabled": false}
  }
}
```

## Freshness

Cached rankings multiply each score by

```
1 + weight × 0.5^(ageDays / halfLifeDays)
```

where the age comes from the note's `updated` property, then `created`, then
the file mtime. Defaults (`halfLifeDays: 90`, `weight: 0.15`) are active
without any manifest section and can be tuned or disabled:

```json
{
  "search": {
    "freshness": {"halfLifeDays": 90, "weight": 0.15}
  }
}
```

A `weight` of `0` disables freshness and restores exact integer scores.

## Graph neighbors

`vaultctl neighbors <path-or-id>` traverses resolved edges from the cache
(`--depth` 1–3, `--direction in|out|both`, `--limit`). Each neighbor reports
its id, path, kind, title, `updated`, breadth-first distance, and the typed
edges (`field`, `direction`) that connect it to the previous ring. Without a
usable cache the same traversal runs from a full scan.
