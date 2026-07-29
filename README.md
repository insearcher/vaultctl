# vaultctl

`vaultctl` is a schema-driven command-line interface for Markdown vaults.
Notes remain ordinary Markdown files; a declarative manifest describes node
kinds, fields, tags, and typed relations.

> **Status:** pre-alpha and read-only. The current release can scan, validate,
> export a graph, search, and assemble bounded context. It does not modify
> vault contents.

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
| `doctor` | Check vault discovery and available execution backends |

All machine-readable output includes a schema version. See
[the compatibility contract](docs/compatibility.md).

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
