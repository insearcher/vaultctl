# Git merge driver

`vaultctl merge driver` is a pre-alpha integration with Git's custom merge
driver protocol. It is currently intended only for synthetic and throwaway
repositories.

## Contract

Git supplies four values:

- `%O`: temporary file containing the common ancestor;
- `%A`: temporary file containing the current side;
- `%B`: temporary file containing the other side;
- `%P`: repository-relative path being merged.

The driver parses the three Markdown files, creates the same versioned
semantic `MergePlan` as `merge plan`, and:

- atomically replaces only `%A` when the plan is clean and changes content;
- leaves all three inputs byte-identical when the plan conflicts;
- never changes the index, `HEAD`, another path, a ref, or a remote;
- returns `0` for `applied` or `unchanged`, `1` for `conflict`, and `2` for a
  validation or execution failure;
- emits a `vaultctl.merge-driver/v1` receipt containing hashes and conflict
  locations, not note values.

The driver uses each input's SHA-256 content hash as its immutable revision ID.
Explicit `merge plan` callers may continue to supply Git commit, tree, or blob
IDs.

Concurrent body edits remain a conservative typed conflict in this version.
Native line merging for independent body edits is a later, separately tested
extension.

## Local throwaway setup

Track only the attribute selection:

```gitattributes
*.md merge=vaultctl
```

Configure the executable in that repository's local Git config:

```bash
git config --local merge.vaultctl.name \
  'vaultctl semantic Markdown merge'
git config --local merge.vaultctl.driver \
  'vaultctl --vault . --format text merge driver --base %O --ours %A --theirs %B --path %P'
```

Do not configure the driver globally. Repository-local configuration keeps
the executable choice outside tracked, potentially untrusted content and
limits activation to a checkout that the operator selected.

The command is not an installer: `vaultctl` does not edit `.gitattributes` or
Git config.

## Validation after Git merge

A per-file driver runs while Git is still assembling the worktree, so it
cannot reliably perform whole-vault prospective validation. After Git reports
a clean merge, run:

```bash
vaultctl --vault . validate
git diff --check
```

For early pilots, use `git merge --no-commit` so validation and human review
happen before creating a merge commit.

## Safety boundary

The manifest remains declarative and cannot select an executable or run
repository code. The driver rejects non-normalized logical paths and symlinked
input files, verifies the rendered candidate by parsing it again, rechecks the
manifest digest and `%A` before replacement, fsyncs staged content, and
attempts rollback if a post-replacement fault occurs.

No real-vault enablement, Git config rollout, forge adapter, automatic commit,
or promotion behavior is included in this milestone.
