# vaultctl

`vaultctl` is a schema-driven command-line interface for Markdown vaults.
Notes remain ordinary Markdown files; a declarative manifest describes node
kinds, fields, tags, and typed relations.

It is designed as a deterministic toolbox for agents and humans, not as an
autonomous workflow orchestrator. The caller owns Git, editing, approval, and
retry decisions; `vaultctl` supplies structured evidence and validation.

> **Status:** pre-alpha. Most commands are read-only. The only public mutation
> is an explicit, capability-gated `node apply` for one ready typed create or
> update plan. It does not own Git or any surrounding workflow.

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

## Agent plugin

This repository is also the public `insearcher` marketplace for a cross-runtime
plugin named `vaultctl`. The plugin contains the generic `vaultctl-agent` skill:
it teaches an agent how to compose deterministic CLI evidence with an owning
consumer's vault selection, authority, validation, editor, and Git policy. It
does not bundle the executable, install dependencies, select a vault, or grant
write permission.

Codex:

```bash
codex plugin marketplace add https://github.com/insearcher/vaultctl.git \
  --ref main \
  --sparse .agents/plugins \
  --sparse plugins/vaultctl
codex plugin add vaultctl@insearcher
```

Claude Code:

```bash
claude plugin marketplace add https://github.com/insearcher/vaultctl.git \
  --scope user \
  --sparse .claude-plugin plugins/vaultctl
claude plugin install vaultctl@insearcher --scope user
```

Consumers should pin and verify the CLI independently, then compose their own
policy skill with `$vaultctl:vaultctl-agent`.

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
| `node plan` | Plan and prospectively validate one typed create or update |
| `node render` | Emit a current plan's exact candidate Markdown |
| `node diff` | Emit a current plan's unified diff |
| `node apply` | Explicitly apply one ready plan and emit a versioned receipt |
| `merge plan` | Produce a fail-closed semantic plan for one three-way Markdown merge |
| `merge validate` | Validate a clean plan against the prospective whole vault |
| `doctor` | Check vault discovery and available execution backends |

All machine-readable output includes a schema version. See
[the compatibility contract](docs/compatibility.md).

`merge plan` is intentionally not an apply command. It reads a base/ours/theirs
triple, records exact revisions and content hashes, applies only declarative
manifest policy, and returns either a candidate or typed conflicts. See
[the semantic merge contract](docs/semantic-merge.md).

See [agent workflows](docs/agent-workflows.md) for the intended boundary
between Git, an agent or human operator, optional editor/application tools,
and `vaultctl`.

`node plan`, `node render`, and `node diff` are read-only. The caller supplies
a versioned JSON request; `vaultctl` binds the result to the selected vault,
manifest, engine, exact source precondition, candidate, diff, and whole-vault
prospective validation. `node apply` repeats those checks under a cooperative
lock, performs one local atomic create or update, validates the resulting
vault, and emits a receipt. See
[typed node mutation plans](docs/node-mutations.md).

The semantic-merge apply boundary remains internal and is tested only on
synthetic vaults. See
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
