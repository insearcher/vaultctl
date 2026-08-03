# Derived node queries

`vaultctl query` filters the normalized graph without creating or updating an
index file. Its output is a disposable projection; Markdown notes and the
manifest remain the source of truth.

```bash
vaultctl --vault /path/to/vault query --kind task --where status=active
vaultctl --vault /path/to/vault query --path 'tasks/**' --path 'kb/**'
vaultctl --vault /path/to/vault query --tag operations --has-field owner
vaultctl --vault /path/to/vault query --without-incoming
```

Filters are exact and deterministic:

- repeated `--path` and `--kind` values are alternatives;
- repeated `--tag`, `--has-field`, and `--where` filters must all match;
- `--path` accepts normalized vault-relative globs with the same `*`, `?`, and
  directory-aware `**` semantics as manifest path selectors;
- `--where FIELD=VALUE` parses JSON scalars, arrays, and objects, falling back
  to a plain string when `VALUE` is not JSON;
- `--without-incoming` selects nodes with no resolved incoming graph edge;
- `--limit` is applied after stable path ordering.

JSON output uses `vaultctl.query/v1` and includes both outgoing and incoming
edges for each selected node. A no-match query returns an empty `nodes` array
and exit code `1`.

The command does not define what a session, owner, or orphan means for a
particular consumer. PID registries, agent lifecycle hooks, Git attribution,
autosave, and publication remain outside the core. Consumer skills may use
this generic projection to build an on-demand view without committing a
second source of truth.
