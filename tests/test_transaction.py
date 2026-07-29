from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import vaultctl.transaction as transaction
from vaultctl.cli import main
from vaultctl.errors import MergeError
from vaultctl.manifest import load_manifest
from vaultctl.merge import merge_plan_from_dict, plan_merge_files
from vaultctl.transaction import apply_merge_plan, validate_merge_plan

BASE_REVISION = "a" * 40
OURS_REVISION = "b" * 40
THEIRS_REVISION = "c" * 40


def _schema(name: str) -> dict[str, object]:
    path = Path(__file__).parents[1] / "src" / "vaultctl" / "schemas" / name
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_schema(name: str, payload: dict[str, object]) -> None:
    schema = _schema(name)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(payload)


def _plan(
    root: Path,
    tmp_path: Path,
    *,
    base: str,
    ours: str,
    theirs: str,
):
    triples = tmp_path / "triples"
    triples.mkdir()
    base_path = triples / "base.md"
    ours_path = triples / "ours.md"
    theirs_path = triples / "theirs.md"
    base_path.write_text(base, encoding="utf-8")
    ours_path.write_text(ours, encoding="utf-8")
    theirs_path.write_text(theirs, encoding="utf-8")
    return plan_merge_files(
        load_manifest(root),
        path="notes/example.md",
        base_path=base_path,
        ours_path=ours_path,
        theirs_path=theirs_path,
        base_revision=BASE_REVISION,
        ours_revision=OURS_REVISION,
        theirs_revision=THEIRS_REVISION,
    )


def _documents(status: str = "draft") -> str:
    return (
        "---\n"
        'title: "Quoted" # keep\n'
        f"status: {status}\n"
        "tags: []\n"
        "related: []\n"
        "---\n"
        "# Example\n"
    )


def _root(make_vault, *, apply: bool = False) -> Path:
    capabilities = ["semantic-merge-apply"] if apply else []
    return make_vault(
        manifest_overrides={
            "capabilities": capabilities,
            "merge": {
                "fields": {
                    "status": {"strategy": "scalar"},
                    "related": {"strategy": "set"},
                }
            },
        },
        notes={
            "notes/example.md": _documents(),
            "notes/unrelated.md": ("---\ntags: []\nrelated: []\n---\n# Unrelated\n"),
        },
    )


def test_prospective_validation_is_read_only_and_versioned(
    make_vault,
    tmp_path: Path,
) -> None:
    root = _root(make_vault)
    target = root / "notes" / "example.md"
    before = target.read_bytes()
    plan = _plan(
        root,
        tmp_path,
        base=_documents(),
        ours=_documents(),
        theirs=_documents("ready"),
    )

    validation = validate_merge_plan(load_manifest(root), plan)

    assert validation.valid is True
    assert validation.summary == {
        "nodes": 2,
        "edges": 0,
        "errors": 0,
        "warnings": 0,
    }
    assert target.read_bytes() == before
    _validate_schema("merge-validation-v1.schema.json", validation.to_dict())


def test_prospective_validation_rejects_graph_error_without_writing(
    make_vault,
    tmp_path: Path,
) -> None:
    root = _root(make_vault)
    target = root / "notes" / "example.md"
    before = target.read_bytes()
    theirs = _documents().replace(
        "related: []",
        "related: ['[[notes/missing]]']",
    )
    plan = _plan(
        root,
        tmp_path,
        base=_documents(),
        ours=_documents(),
        theirs=theirs,
    )

    validation = validate_merge_plan(load_manifest(root), plan)

    assert validation.valid is False
    assert validation.summary["errors"] == 1
    assert [issue.code for issue in validation.issues] == ["relation.unresolved"]
    assert target.read_bytes() == before
    _validate_schema("merge-validation-v1.schema.json", validation.to_dict())


def test_apply_is_atomic_path_scoped_and_preserves_unmodified_yaml(
    make_vault,
    tmp_path: Path,
) -> None:
    root = _root(make_vault, apply=True)
    target = root / "notes" / "example.md"
    unrelated = root / "notes" / "unrelated.md"
    unrelated_before = unrelated.read_bytes()
    plan = _plan(
        root,
        tmp_path,
        base=_documents(),
        ours=_documents(),
        theirs=_documents("ready"),
    )

    receipt = apply_merge_plan(load_manifest(root), plan)

    content = target.read_text(encoding="utf-8")
    assert receipt.state == "applied"
    assert receipt.error is None
    assert 'title: "Quoted" # keep' in content
    assert "status: ready" in content
    assert unrelated.read_bytes() == unrelated_before
    assert list(target.parent.glob(".*.vaultctl-*.tmp")) == []
    _validate_schema("receipt-v1.schema.json", receipt.to_dict())


def test_apply_rejects_stale_target_hash_without_writing_candidate(
    make_vault,
    tmp_path: Path,
) -> None:
    root = _root(make_vault, apply=True)
    target = root / "notes" / "example.md"
    plan = _plan(
        root,
        tmp_path,
        base=_documents(),
        ours=_documents(),
        theirs=_documents("ready"),
    )
    concurrent = _documents("blocked")
    target.write_text(concurrent, encoding="utf-8")

    with pytest.raises(MergeError, match="ours input"):
        apply_merge_plan(load_manifest(root), plan)

    assert target.read_text(encoding="utf-8") == concurrent


def test_apply_requires_explicit_manifest_capability(
    make_vault,
    tmp_path: Path,
) -> None:
    root = _root(make_vault)
    plan = _plan(
        root,
        tmp_path,
        base=_documents(),
        ours=_documents(),
        theirs=_documents("ready"),
    )

    with pytest.raises(MergeError, match="semantic-merge-apply"):
        apply_merge_plan(load_manifest(root), plan)


def test_apply_rejects_manifest_changed_after_selection(
    make_vault,
    tmp_path: Path,
) -> None:
    root = _root(make_vault, apply=True)
    target = root / "notes" / "example.md"
    before = target.read_bytes()
    plan = _plan(
        root,
        tmp_path,
        base=_documents(),
        ours=_documents(),
        theirs=_documents("ready"),
    )
    selected_manifest = load_manifest(root)
    manifest_path = root / ".vaultctl" / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["ignore"] = ["drafts/**"]
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MergeError, match="manifest changed"):
        apply_merge_plan(selected_manifest, plan)

    assert target.read_bytes() == before


def test_apply_returns_failed_receipt_when_replace_does_not_start(
    make_vault,
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _root(make_vault, apply=True)
    target = root / "notes" / "example.md"
    before = target.read_bytes()
    plan = _plan(
        root,
        tmp_path,
        base=_documents(),
        ours=_documents(),
        theirs=_documents("ready"),
    )

    def fail_replace(staged: Path, destination: Path) -> None:
        raise OSError("synthetic pre-replace fault")

    monkeypatch.setattr(transaction, "_atomic_replace", fail_replace)
    receipt = apply_merge_plan(load_manifest(root), plan)

    assert receipt.state == "failed"
    assert receipt.error == "OSError: local atomic replacement failed"
    assert target.read_bytes() == before
    assert list(target.parent.glob(".*.vaultctl-*.tmp")) == []
    _validate_schema("receipt-v1.schema.json", receipt.to_dict())


def test_apply_rolls_back_when_replace_faults_after_moving_candidate(
    make_vault,
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _root(make_vault, apply=True)
    target = root / "notes" / "example.md"
    before = target.read_bytes()
    plan = _plan(
        root,
        tmp_path,
        base=_documents(),
        ours=_documents(),
        theirs=_documents("ready"),
    )
    calls = 0

    def replace_then_fault(staged: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        os.replace(staged, destination)
        if calls == 1:
            raise OSError("synthetic post-replace fault")

    monkeypatch.setattr(transaction, "_atomic_replace", replace_then_fault)
    receipt = apply_merge_plan(load_manifest(root), plan)

    assert calls == 2
    assert receipt.state == "rolled-back"
    assert receipt.before_hashes == receipt.after_hashes
    assert target.read_bytes() == before
    assert list(target.parent.glob(".*.vaultctl-*.tmp")) == []
    _validate_schema("receipt-v1.schema.json", receipt.to_dict())


def test_apply_fails_closed_when_cooperative_lock_is_busy(
    make_vault,
    tmp_path: Path,
) -> None:
    root = _root(make_vault, apply=True)
    plan = _plan(
        root,
        tmp_path,
        base=_documents(),
        ours=_documents(),
        theirs=_documents("ready"),
    )
    lock_path = root / ".vaultctl" / "manifest.json"

    with lock_path.open("rb") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(MergeError, match="lock is busy"):
            apply_merge_plan(load_manifest(root), plan)


def test_apply_rejects_symlink_target_without_touching_destination(
    make_vault,
    tmp_path: Path,
) -> None:
    root = _root(make_vault, apply=True)
    target = root / "notes" / "example.md"
    plan = _plan(
        root,
        tmp_path,
        base=_documents(),
        ours=_documents(),
        theirs=_documents("ready"),
    )
    outside = tmp_path / "outside.md"
    outside.write_text(_documents(), encoding="utf-8")
    target.unlink()
    target.symlink_to(outside)

    with pytest.raises(MergeError, match="symlinked"):
        apply_merge_plan(load_manifest(root), plan)

    assert outside.read_text(encoding="utf-8") == _documents()


def test_merge_validate_cli_reads_versioned_plan(
    make_vault,
    tmp_path: Path,
    capsys,
) -> None:
    root = _root(make_vault)
    plan = _plan(
        root,
        tmp_path,
        base=_documents(),
        ours=_documents(),
        theirs=_documents("ready"),
    )
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan.to_dict()), encoding="utf-8")

    exit_code = main(
        [
            "--vault",
            str(root),
            "merge",
            "validate",
            "--plan",
            str(plan_path),
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schemaVersion"] == "vaultctl.merge-validation/v1"
    assert payload["valid"] is True


def test_plan_loader_rejects_tampered_payload(
    make_vault,
    tmp_path: Path,
) -> None:
    root = _root(make_vault)
    plan = _plan(
        root,
        tmp_path,
        base=_documents(),
        ours=_documents(),
        theirs=_documents("ready"),
    )
    payload = plan.to_dict()
    payload["candidate"]["body"] = "# Tampered\n"

    with pytest.raises(MergeError, match="plan digest"):
        merge_plan_from_dict(payload)
