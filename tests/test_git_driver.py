from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import vaultctl.git_driver as git_driver
from vaultctl.cli import main
from vaultctl.errors import MergeError
from vaultctl.git_driver import run_git_merge_driver
from vaultctl.manifest import load_manifest
from vaultctl.markdown import parse_markdown


def _document(
    *,
    status: str = "draft",
    tags: tuple[str, ...] = ("shared",),
    body: str = "# Example\n",
) -> str:
    tag_lines = "".join(f"  - {tag}\n" for tag in tags)
    return f"---\nstatus: {status}\ntags:\n{tag_lines}related: []\n---\n{body}"


def _root(make_vault) -> Path:
    return make_vault(
        manifest_overrides={
            "merge": {
                "fields": {
                    "status": {"strategy": "scalar"},
                    "tags": {"strategy": "set"},
                }
            }
        },
        notes={
            "notes/example.md": _document(),
            "notes/unrelated.md": _document(body="# Unrelated\n"),
        },
    )


def _triple(
    tmp_path: Path,
    *,
    base: str,
    ours: str,
    theirs: str,
) -> tuple[Path, Path, Path]:
    root = tmp_path / "triple"
    root.mkdir()
    paths = tuple(root / f"{name}.md" for name in ("base", "ours", "theirs"))
    for path, content in zip(paths, (base, ours, theirs), strict=True):
        path.write_text(content, encoding="utf-8")
    return paths


def _schema() -> dict[str, object]:
    path = (
        Path(__file__).parents[1]
        / "src"
        / "vaultctl"
        / "schemas"
        / "merge-driver-v1.schema.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _operation_id(payload: dict[str, object]) -> str:
    core = dict(payload)
    core.pop("operationId")
    encoded = json.dumps(
        core,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def test_driver_applies_only_ours_and_emits_versioned_receipt(
    make_vault,
    tmp_path: Path,
) -> None:
    root = _root(make_vault)
    base, ours, theirs = _triple(
        tmp_path,
        base=_document(),
        ours=_document(tags=("shared", "alpha")),
        theirs=_document(tags=("shared", "beta")),
    )
    base_before = base.read_bytes()
    theirs_before = theirs.read_bytes()

    receipt = run_git_merge_driver(
        load_manifest(root),
        path="notes/example.md",
        base_path=base,
        ours_path=ours,
        theirs_path=theirs,
    )

    payload = receipt.to_dict()
    assert receipt.state == "applied"
    assert receipt.before_hash != receipt.after_hash
    assert receipt.input_revisions == {
        "base": receipt.input_revisions["base"],
        "ours": receipt.before_hash,
        "theirs": receipt.input_revisions["theirs"],
    }
    assert all(len(value) == 64 for value in receipt.input_revisions.values())
    assert parse_markdown(
        ours,
        display_path="notes/example.md",
    ).properties["tags"] == ["shared", "alpha", "beta"]
    assert base.read_bytes() == base_before
    assert theirs.read_bytes() == theirs_before
    assert list(ours.parent.glob(".*.vaultctl-driver-*.tmp")) == []
    assert payload["operationId"] == _operation_id(payload)
    Draft202012Validator.check_schema(_schema())
    Draft202012Validator(_schema()).validate(payload)


def test_driver_conflict_leaves_ours_byte_identical(
    make_vault,
    tmp_path: Path,
) -> None:
    root = _root(make_vault)
    base, ours, theirs = _triple(
        tmp_path,
        base=_document(),
        ours=_document(status="ready"),
        theirs=_document(status="blocked"),
    )
    before = ours.read_bytes()

    receipt = run_git_merge_driver(
        load_manifest(root),
        path="notes/example.md",
        base_path=base,
        ours_path=ours,
        theirs_path=theirs,
    )

    assert receipt.state == "conflict"
    assert receipt.before_hash == receipt.after_hash
    assert receipt.conflicts == (
        {
            "id": receipt.conflicts[0]["id"],
            "kind": "frontmatter.concurrent-change",
            "location": "frontmatter.status",
        },
    )
    assert ours.read_bytes() == before
    assert "value" not in json.dumps(receipt.to_dict())
    Draft202012Validator(_schema()).validate(receipt.to_dict())


def test_driver_rolls_back_after_post_replace_fault(
    make_vault,
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _root(make_vault)
    base, ours, theirs = _triple(
        tmp_path,
        base=_document(),
        ours=_document(),
        theirs=_document(status="ready"),
    )
    before = ours.read_bytes()
    calls = 0

    def replace_then_fault(staged: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        os.replace(staged, target)
        if calls == 1:
            raise OSError("synthetic post-replace fault")

    monkeypatch.setattr(git_driver, "_atomic_replace", replace_then_fault)

    receipt = run_git_merge_driver(
        load_manifest(root),
        path="notes/example.md",
        base_path=base,
        ours_path=ours,
        theirs_path=theirs,
    )

    assert calls == 2
    assert receipt.state == "rolled-back"
    assert receipt.error == "OSError: Git merge driver could not replace ours"
    assert receipt.before_hash == receipt.after_hash
    assert ours.read_bytes() == before
    assert list(ours.parent.glob(".*.vaultctl-driver-*.tmp")) == []
    Draft202012Validator(_schema()).validate(receipt.to_dict())


def test_driver_fails_before_replace_when_manifest_changes(
    make_vault,
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _root(make_vault)
    base, ours, theirs = _triple(
        tmp_path,
        base=_document(),
        ours=_document(),
        theirs=_document(status="ready"),
    )
    before = ours.read_bytes()
    original_stage = git_driver._stage_bytes

    def stage_then_change_manifest(
        target: Path,
        content: bytes,
        mode: int,
    ) -> Path:
        staged = original_stage(target, content, mode)
        manifest_path = root / ".vaultctl" / "manifest.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["ignore"] = ["drafts/**"]
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        return staged

    monkeypatch.setattr(git_driver, "_stage_bytes", stage_then_change_manifest)

    receipt = run_git_merge_driver(
        load_manifest(root),
        path="notes/example.md",
        base_path=base,
        ours_path=ours,
        theirs_path=theirs,
    )

    assert receipt.state == "failed"
    assert receipt.error == "vault manifest changed while the driver was running"
    assert ours.read_bytes() == before
    assert list(ours.parent.glob(".*.vaultctl-driver-*.tmp")) == []
    Draft202012Validator(_schema()).validate(receipt.to_dict())


def test_driver_rejects_symlinked_ours_without_touching_destination(
    make_vault,
    tmp_path: Path,
) -> None:
    root = _root(make_vault)
    base, ours, theirs = _triple(
        tmp_path,
        base=_document(),
        ours=_document(),
        theirs=_document(status="ready"),
    )
    outside = tmp_path / "outside.md"
    outside.write_text(_document(), encoding="utf-8")
    ours.unlink()
    ours.symlink_to(outside)

    with pytest.raises(MergeError, match="regular non-symlink"):
        run_git_merge_driver(
            load_manifest(root),
            path="notes/example.md",
            base_path=base,
            ours_path=ours,
            theirs_path=theirs,
        )

    assert outside.read_text(encoding="utf-8") == _document()


def test_driver_cli_exit_codes_do_not_expose_note_values(
    make_vault,
    tmp_path: Path,
    capsys,
) -> None:
    root = _root(make_vault)
    base, ours, theirs = _triple(
        tmp_path,
        base=_document(),
        ours=_document(status="private-ours"),
        theirs=_document(status="private-theirs"),
    )

    exit_code = main(
        [
            "--vault",
            str(root),
            "merge",
            "driver",
            "--path",
            "notes/example.md",
            "--base",
            str(base),
            "--ours",
            str(ours),
            "--theirs",
            str(theirs),
        ]
    )

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert exit_code == 1
    assert payload["state"] == "conflict"
    assert "private-ours" not in output
    assert "private-theirs" not in output


def test_driver_cli_returns_two_for_recovered_write_failure(
    make_vault,
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    root = _root(make_vault)
    base, ours, theirs = _triple(
        tmp_path,
        base=_document(),
        ours=_document(),
        theirs=_document(status="ready"),
    )
    before = ours.read_bytes()

    def fail_replace(staged: Path, target: Path) -> None:
        raise OSError("synthetic pre-replace fault")

    monkeypatch.setattr(git_driver, "_atomic_replace", fail_replace)

    exit_code = main(
        [
            "--vault",
            str(root),
            "merge",
            "driver",
            "--path",
            "notes/example.md",
            "--base",
            str(base),
            "--ours",
            str(ours),
            "--theirs",
            str(theirs),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["state"] == "failed"
    assert payload["error"] == "OSError: Git merge driver could not replace ours"
    assert ours.read_bytes() == before
    Draft202012Validator(_schema()).validate(payload)


def _git(
    root: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=check,
        text=True,
        capture_output=True,
    )


def _write_note(
    root: Path,
    *,
    status: str = "draft",
    tags: tuple[str, ...] = ("shared",),
) -> None:
    (root / "notes" / "example.md").write_text(
        _document(status=status, tags=tags),
        encoding="utf-8",
    )


def _git_repo(make_vault) -> Path:
    root = _root(make_vault)
    (root / ".gitattributes").write_text("*.md merge=vaultctl\n", encoding="utf-8")
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Synthetic Tester")
    _git(root, "config", "user.email", "synthetic.invalid")
    command = (
        f"{shlex.quote(sys.executable)} -m vaultctl "
        "--vault . --format text merge driver "
        "--base %O --ours %A --theirs %B --path %P"
    )
    _git(root, "config", "merge.vaultctl.name", "vaultctl semantic Markdown merge")
    _git(root, "config", "merge.vaultctl.driver", command)
    _git(root, "add", ".")
    _git(root, "commit", "-m", "synthetic base")
    return root


def test_custom_driver_merges_throwaway_repository_without_updating_ref(
    make_vault,
) -> None:
    root = _git_repo(make_vault)
    manifest_before = (root / ".vaultctl" / "manifest.json").read_bytes()
    unrelated_before = (root / "notes" / "unrelated.md").read_bytes()

    _git(root, "switch", "-c", "ours")
    _write_note(root, tags=("shared", "alpha"))
    _git(root, "commit", "-am", "ours")
    _git(root, "switch", "main")
    _git(root, "switch", "-c", "theirs")
    _write_note(root, tags=("shared", "beta"))
    _git(root, "commit", "-am", "theirs")
    _git(root, "switch", "ours")
    head_before = _git(root, "rev-parse", "HEAD").stdout.strip()

    result = _git(root, "merge", "--no-commit", "theirs")

    assert result.returncode == 0
    assert _git(root, "rev-parse", "HEAD").stdout.strip() == head_before
    assert parse_markdown(
        root / "notes" / "example.md",
        display_path="notes/example.md",
    ).properties["tags"] == ["shared", "alpha", "beta"]
    assert _git(root, "status", "--porcelain").stdout.splitlines() == [
        "M  notes/example.md"
    ]
    assert (root / ".vaultctl" / "manifest.json").read_bytes() == manifest_before
    assert (root / "notes" / "unrelated.md").read_bytes() == unrelated_before
    validation = subprocess.run(
        [
            sys.executable,
            "-m",
            "vaultctl",
            "--vault",
            str(root),
            "validate",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    assert validation.returncode == 0, validation.stdout + validation.stderr


def test_custom_driver_reports_git_conflict_without_updating_ref(
    make_vault,
) -> None:
    root = _git_repo(make_vault)

    _git(root, "switch", "-c", "ours")
    _write_note(root, status="ready")
    _git(root, "commit", "-am", "ours")
    ours_content = (root / "notes" / "example.md").read_bytes()
    _git(root, "switch", "main")
    _git(root, "switch", "-c", "theirs")
    _write_note(root, status="blocked")
    _git(root, "commit", "-am", "theirs")
    _git(root, "switch", "ours")
    head_before = _git(root, "rev-parse", "HEAD").stdout.strip()

    result = _git(root, "merge", "--no-commit", "theirs", check=False)

    assert result.returncode == 1
    assert _git(root, "rev-parse", "HEAD").stdout.strip() == head_before
    assert (root / "notes" / "example.md").read_bytes() == ours_content
    assert _git(root, "status", "--porcelain").stdout.splitlines() == [
        "UU notes/example.md"
    ]
