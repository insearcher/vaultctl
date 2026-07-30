from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import vaultctl.mutation as mutation
from vaultctl.cli import main
from vaultctl.errors import MutationError
from vaultctl.manifest import load_manifest
from vaultctl.model import MutationValidation
from vaultctl.mutation import (
    apply_node_mutation_plan,
    node_mutation_request_from_dict,
    plan_node_mutation,
)


def _schema(name: str) -> dict[str, object]:
    path = Path(__file__).parents[1] / "src" / "vaultctl" / "schemas" / name
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_schema(name: str, payload: dict[str, object]) -> None:
    schema = _schema(name)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(payload)


def _hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _root(make_vault, *, apply: bool = True, notes=None) -> Path:
    capabilities = ["node-mutation-apply"] if apply else []
    return make_vault(
        manifest_overrides={"capabilities": capabilities},
        notes=notes,
    )


def _create_plan(root: Path, *, related: list[str] | None = None):
    request = node_mutation_request_from_dict(
        {
            "schemaVersion": "vaultctl.node-mutation-request/v1",
            "operation": "create",
            "path": "notes/new.md",
            "kind": "document",
            "document": {
                "properties": {
                    "tags": ["new"],
                    "related": related or [],
                },
                "body": "# New\n\nCandidate.\n",
            },
        }
    )
    return plan_node_mutation(load_manifest(root), request)


def _update_plan(root: Path):
    target = root / "notes" / "existing.md"
    request = node_mutation_request_from_dict(
        {
            "schemaVersion": "vaultctl.node-mutation-request/v1",
            "operation": "update",
            "path": "notes/existing.md",
            "kind": "document",
            "expectedSourceHash": _hash(target.read_bytes()),
            "changes": {
                "setProperties": {"status": "ready"},
                "body": "# Existing\n\nUpdated.\n",
            },
        }
    )
    return plan_node_mutation(load_manifest(root), request)


def _existing() -> str:
    return "---\nstatus: draft\ntags: []\nrelated: []\n---\n# Existing\n"


def test_apply_create_is_atomic_path_scoped_and_versioned(make_vault) -> None:
    root = _root(
        make_vault,
        notes={"notes/existing.md": _existing()},
    )
    target = root / "notes" / "new.md"
    unrelated = root / "notes" / "existing.md"
    unrelated_before = unrelated.read_bytes()
    plan = _create_plan(root, related=["[[notes/existing]]"])

    receipt = apply_node_mutation_plan(load_manifest(root), plan)

    assert receipt.state == "applied"
    assert receipt.before.to_dict() == {"exists": False}
    assert receipt.after.to_dict() == {
        "exists": True,
        "sourceHash": plan.candidate.source_hash,
    }
    assert target.read_text(encoding="utf-8") == plan.candidate.source
    expected_mode = stat.S_IMODE(target.parent.stat().st_mode) & 0o666 or 0o600
    assert stat.S_IMODE(target.stat().st_mode) == expected_mode
    assert unrelated.read_bytes() == unrelated_before
    assert list(target.parent.glob(".*.vaultctl-*.tmp")) == []
    _validate_schema(
        "node-mutation-receipt-v1.schema.json",
        receipt.to_dict(),
    )


def test_apply_update_preserves_mode_and_returns_exact_receipt(make_vault) -> None:
    root = _root(make_vault, notes={"notes/existing.md": _existing()})
    target = root / "notes" / "existing.md"
    target.chmod(0o640)
    before_hash = _hash(target.read_bytes())
    plan = _update_plan(root)

    receipt = apply_node_mutation_plan(load_manifest(root), plan)

    assert receipt.state == "applied"
    assert receipt.before.source_hash == before_hash
    assert receipt.after.source_hash == plan.candidate.source_hash
    assert _hash(target.read_bytes()) == plan.candidate.source_hash
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert list(target.parent.glob(".*.vaultctl-*.tmp")) == []
    _validate_schema(
        "node-mutation-receipt-v1.schema.json",
        receipt.to_dict(),
    )


def test_apply_requires_manifest_capability_without_writing(make_vault) -> None:
    root = _root(make_vault, apply=False)
    target = root / "notes" / "new.md"
    plan = _create_plan(root)

    with pytest.raises(MutationError, match="node-mutation-apply"):
        apply_node_mutation_plan(load_manifest(root), plan)

    assert not target.exists()


def test_apply_rejects_invalid_plan_without_writing(make_vault) -> None:
    root = _root(make_vault)
    plan = _create_plan(root, related=["[[notes/missing]]"])

    assert plan.state == "invalid"
    with pytest.raises(MutationError, match="ready, valid"):
        apply_node_mutation_plan(load_manifest(root), plan)

    assert not (root / "notes" / "new.md").exists()


def test_apply_rejects_stale_update_target(make_vault) -> None:
    root = _root(make_vault, notes={"notes/existing.md": _existing()})
    target = root / "notes" / "existing.md"
    plan = _update_plan(root)
    target.write_text("# Concurrent\n", encoding="utf-8")

    with pytest.raises(MutationError, match="hash is stale"):
        apply_node_mutation_plan(load_manifest(root), plan)

    assert target.read_text(encoding="utf-8") == "# Concurrent\n"


def test_apply_rejects_stale_manifest(make_vault) -> None:
    root = _root(make_vault)
    plan = _create_plan(root)
    manifest_path = root / ".vaultctl" / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["ignore"] = ["drafts/**"]
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MutationError, match="manifest"):
        apply_node_mutation_plan(load_manifest(root), plan)

    assert not (root / "notes" / "new.md").exists()


def test_apply_fails_closed_when_lock_is_busy(make_vault) -> None:
    root = _root(make_vault)
    plan = _create_plan(root)
    lock_path = root / ".vaultctl" / "manifest.json"

    with lock_path.open("rb") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(MutationError, match="lock is busy"):
            apply_node_mutation_plan(load_manifest(root), plan)

    assert not (root / "notes" / "new.md").exists()


def test_apply_rejects_symlink_target_without_touching_destination(
    make_vault,
    tmp_path: Path,
) -> None:
    root = _root(make_vault)
    plan = _create_plan(root)
    target = root / "notes" / "new.md"
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n", encoding="utf-8")
    target.symlink_to(outside)

    with pytest.raises(MutationError, match="symlinked"):
        apply_node_mutation_plan(load_manifest(root), plan)

    assert outside.read_text(encoding="utf-8") == "# Outside\n"


def test_apply_returns_failed_receipt_when_update_replace_does_not_start(
    make_vault,
    monkeypatch,
) -> None:
    root = _root(make_vault, notes={"notes/existing.md": _existing()})
    target = root / "notes" / "existing.md"
    before = target.read_bytes()
    plan = _update_plan(root)

    def fail_replace(staged: Path, destination: Path) -> None:
        raise OSError("synthetic pre-replace fault")

    monkeypatch.setattr(mutation, "_atomic_update", fail_replace)
    receipt = apply_node_mutation_plan(load_manifest(root), plan)

    assert receipt.state == "failed"
    assert receipt.error == "OSError: local atomic mutation failed"
    assert receipt.before == receipt.after
    assert target.read_bytes() == before
    assert list(target.parent.glob(".*.vaultctl-*.tmp")) == []
    _validate_schema(
        "node-mutation-receipt-v1.schema.json",
        receipt.to_dict(),
    )


def test_apply_rolls_back_update_after_replace_fault(make_vault, monkeypatch) -> None:
    root = _root(make_vault, notes={"notes/existing.md": _existing()})
    target = root / "notes" / "existing.md"
    before = target.read_bytes()
    plan = _update_plan(root)
    calls = 0

    def replace_then_fault(staged: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        os.replace(staged, destination)
        if calls == 1:
            raise OSError("synthetic post-replace fault")

    monkeypatch.setattr(mutation, "_atomic_update", replace_then_fault)
    receipt = apply_node_mutation_plan(load_manifest(root), plan)

    assert calls == 2
    assert receipt.state == "rolled-back"
    assert receipt.before == receipt.after
    assert target.read_bytes() == before
    assert list(target.parent.glob(".*.vaultctl-*.tmp")) == []
    _validate_schema(
        "node-mutation-receipt-v1.schema.json",
        receipt.to_dict(),
    )


def test_apply_rolls_back_create_after_link_fault(make_vault, monkeypatch) -> None:
    root = _root(make_vault)
    target = root / "notes" / "new.md"
    plan = _create_plan(root)

    def link_then_fault(staged: Path, destination: Path) -> None:
        os.link(staged, destination, follow_symlinks=False)
        raise OSError("synthetic post-link fault")

    monkeypatch.setattr(mutation, "_atomic_create", link_then_fault)
    receipt = apply_node_mutation_plan(load_manifest(root), plan)

    assert receipt.state == "rolled-back"
    assert receipt.before == receipt.after
    assert not target.exists()
    assert list(target.parent.glob(".*.vaultctl-*.tmp")) == []
    _validate_schema(
        "node-mutation-receipt-v1.schema.json",
        receipt.to_dict(),
    )


def test_apply_rolls_back_when_post_validation_differs(
    make_vault,
    monkeypatch,
) -> None:
    root = _root(make_vault, notes={"notes/existing.md": _existing()})
    target = root / "notes" / "existing.md"
    before = target.read_bytes()
    plan = _update_plan(root)
    original = mutation._validation_from_scan
    calls = 0

    def change_post_validation(result, *, path: str, kind: str):
        nonlocal calls
        calls += 1
        validation = original(result, path=path, kind=kind)
        if calls == 1:
            return validation
        return MutationValidation(
            valid=False,
            vault_digest=validation.vault_digest,
            summary={**validation.summary, "errors": 1},
            issues=validation.issues,
        )

    monkeypatch.setattr(mutation, "_validation_from_scan", change_post_validation)
    receipt = apply_node_mutation_plan(load_manifest(root), plan)

    assert calls == 2
    assert receipt.state == "rolled-back"
    assert "post-apply vault state differs" in receipt.error
    assert target.read_bytes() == before


def test_rollback_refuses_to_overwrite_unexpected_concurrent_state(
    make_vault,
    monkeypatch,
) -> None:
    root = _root(make_vault, notes={"notes/existing.md": _existing()})
    target = root / "notes" / "existing.md"
    plan = _update_plan(root)

    def replace_then_concurrent_change(staged: Path, destination: Path) -> None:
        os.replace(staged, destination)
        destination.write_text("# Concurrent after replace\n", encoding="utf-8")
        raise OSError("synthetic concurrent fault")

    monkeypatch.setattr(
        mutation,
        "_atomic_update",
        replace_then_concurrent_change,
    )
    with pytest.raises(MutationError, match="automatic rollback is unsafe"):
        apply_node_mutation_plan(load_manifest(root), plan)

    assert target.read_text(encoding="utf-8") == "# Concurrent after replace\n"


def test_create_collision_never_overwrites_concurrent_target(
    make_vault,
    monkeypatch,
) -> None:
    root = _root(make_vault)
    target = root / "notes" / "new.md"
    plan = _create_plan(root)

    def collide_before_link(staged: Path, destination: Path) -> None:
        destination.write_text("# Concurrent create\n", encoding="utf-8")
        os.link(staged, destination, follow_symlinks=False)

    monkeypatch.setattr(mutation, "_atomic_create", collide_before_link)
    with pytest.raises(MutationError, match="automatic rollback is unsafe"):
        apply_node_mutation_plan(load_manifest(root), plan)

    assert target.read_text(encoding="utf-8") == "# Concurrent create\n"


def test_node_apply_cli_writes_only_after_explicit_command(
    make_vault,
    tmp_path: Path,
    capsys,
) -> None:
    root = _root(make_vault)
    target = root / "notes" / "new.md"
    plan = _create_plan(root)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan.to_dict()), encoding="utf-8")

    exit_code = main(
        [
            "--vault",
            str(root),
            "node",
            "apply",
            "--plan",
            str(plan_path),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["schemaVersion"] == "vaultctl.node-mutation-receipt/v1"
    assert payload["state"] == "applied"
    assert target.read_text(encoding="utf-8") == plan.candidate.source


def test_node_apply_cli_returns_one_with_failed_receipt(
    make_vault,
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    root = _root(make_vault)
    target = root / "notes" / "new.md"
    plan = _create_plan(root)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan.to_dict()), encoding="utf-8")

    def fail_before_link(staged: Path, destination: Path) -> None:
        raise OSError("synthetic pre-link fault")

    monkeypatch.setattr(mutation, "_atomic_create", fail_before_link)
    exit_code = main(
        [
            "--vault",
            str(root),
            "node",
            "apply",
            "--plan",
            str(plan_path),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["state"] == "failed"
    assert payload["after"] == {"exists": False}
    assert not target.exists()
