# vaultctl

`vaultctl` is a schema-driven command-line interface for Markdown vaults.
Notes remain ordinary Markdown files; a declarative manifest describes node
kinds, fields, tags, and typed relations.

> **Status:** pre-alpha. Inspection, planning, and prospective validation are
> read-only. The only public write surface is an opt-in Git custom driver that
> may replace Git's single `%A` merge file; it does not provide a general vault
> write command.

## Install

Install the current development version directly from GitHub:

```bash
uv tool install git+https://github.com/insearcher/vaultctl.git
vaultctl --version
```

Automated consumers should pin a full commit SHA:

```bash
uv tool install \
  'vaultctl @ git+https://github.com/insearcher/vaultctl.git@<full-commit-sha>'
```

## Why

Markdown vaults are easy to edit and review, but every automation tends to
reimplement frontmatter parsing, graph extraction, validation, and discovery.
`vaultctl` provides those mechanisms once while keeping each vault's schema
and policy in the vault itself.

## Quick start

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .

vaultctl --vault examples/basic-vault scan
vaultctl --vault examples/basic-vault validate
vaultctl --vault examples/basic-vault graph export
vaultctl --vault examples/basic-vault search "release plan"
vaultctl --vault examples/basic-vault context "release plan"
vaultctl --vault examples/basic-vault doctor
```

When `--vault` is omitted, `vaultctl` walks upward from the current directory
until it finds `.vaultctl/manifest.json`.

## Model

- One Markdown file is one node.
- Frontmatter contains properties and declared outgoing relations.
- Wiki links and Markdown links also become graph edges.
- `.vaultctl/manifest.json` defines the schema.
- Generated indexes are disposable and are not a source of truth.

The manifest is data, not code. It cannot execute hooks, scripts, or local
modules.

## Commands

| Command | Purpose |
|---|---|
| `scan` | Normalize Markdown files into versioned node and edge JSON |
| `validate` | Check the manifest, note schemas, and graph relations |
| `graph export` | Export a compact versioned graph |
| `search` | Rank notes with manifest-defined zones |
| `context` | Return ranked notes and snippets within an output budget |
| `merge plan` | Produce a fail-closed semantic plan for one three-way Markdown merge |
| `merge validate` | Validate a clean plan against the prospective whole vault |
| `merge driver` | Resolve one Git custom-driver triple and update only `%A` |
| `doctor` | Check vault discovery and available execution backends |

All machine-readable output includes a schema version. See
[the compatibility contract](docs/compatibility.md).

`merge plan` is intentionally not an apply command. It reads a base/ours/theirs
triple, records exact revisions and content hashes, applies only declarative
manifest policy, and returns either a candidate or typed conflicts. See
[the semantic merge contract](docs/semantic-merge.md).

The Git driver is an explicit, locally configured integration and remains
limited to synthetic/throwaway repositories in this pre-alpha phase. See
[the Git merge driver](docs/git-merge-driver.md).

The separate one-path transaction boundary is still internal and tested only
on synthetic vaults. See
[prospective validation and transactions](docs/transactions.md).

## Development

```bash
python -m pip install -e '.[dev]'
ruff check .
pytest
python scripts/check_public_tree.py
```

Only synthetic fixtures belong in this repository. See
[CONTRIBUTING.md](CONTRIBUTING.md) before adding examples or tests.

## License

MIT
