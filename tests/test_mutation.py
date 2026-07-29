from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from vaultctl.cli import main
from vaultctl.errors import MarkdownError, MutationError
from vaultctl.manifest import load_manifest
from vaultctl.mutation import (
    diff_node_mutation_plan,
    load_node_mutation_plan,
    node_mutation_plan_from_dict,
    node_mutation_request_from_dict,
    plan_node_mutation,
    render_node_mutation_plan,
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


def _create_request(
    *,
    path: str = "notes/new.md",
    kind: str = "document",
    properties: dict[str, object] | None = None,
    body: str = "# New\n",
):
    return node_mutation_request_from_dict(
        {
            "schemaVersion": "vaultctl.node-mutation-request/v1",
            "operation": "create",
            "path": path,
            "kind": kind,
            "document": {
                "properties": properties or {"tags": [], "related": []},
                "body": body,
            },
        }
    )


def _update_request(
    target: Path,
    *,
    changes: dict[str, object],
    kind: str = "document",
):
    return node_mutation_request_from_dict(
        {
            "schemaVersion": "vaultctl.node-mutation-request/v1",
            "operation": "update",
            "path": target.relative_to(target.parents[1]).as_posix(),
            "kind": kind,
            "expectedSourceHash": _hash(target.read_bytes()),
            "changes": changes,
        }
    )


def test_create_plan_is_versioned_read_only_and_renderable(make_vault) -> None:
    root = make_vault(
        notes={"notes/existing.md": ("---\ntags: []\nrelated: []\n---\n# Existing\n")}
    )
    target = root / "notes" / "new.md"
    request = _create_request(
        properties={"tags": ["new"], "related": ["[[notes/existing]]"]},
        body="# New\n\nCandidate.\n",
    )

    plan = plan_node_mutation(load_manifest(root), request)

    assert plan.schema_version == "vaultctl.node-mutation-plan/v1"
    assert plan.operation == "create"
    assert plan.precondition.to_dict() == {"exists": False}
    assert plan.state == "ready"
    assert plan.validation.summary == {
        "nodes": 2,
        "edges": 1,
        "errors": 0,
        "warnings": 0,
    }
    assert plan.diff.startswith("--- /dev/null\n+++ b/notes/new.md\n")
    assert render_node_mutation_plan(load_manifest(root), plan) == (
        plan.candidate.source.encode()
    )
    assert diff_node_mutation_plan(load_manifest(root), plan) == plan.diff
    assert plan_node_mutation(load_manifest(root), request) == plan
    assert not target.exists()
    _validate_schema("node-mutation-plan-v1.schema.json", plan.to_dict())


def test_update_plan_applies_typed_patch_without_writing(make_vault) -> None:
    original = (
        "---\n"
        'title: "Quoted" # keep\n'
        "status: draft\n"
        "obsolete: true\n"
        "tags: [old]\n"
        "related: []\n"
        "---\n"
        "# Existing\n\nOld body.\n"
    )
    root = make_vault(notes={"notes/existing.md": original})
    target = root / "notes" / "existing.md"
    request = _update_request(
        target,
        changes={
            "setProperties": {"status": "ready", "tags": ["old", "new"]},
            "removeProperties": ["obsolete"],
            "body": "# Existing\n\nNew body.\n",
        },
    )

    plan = plan_node_mutation(load_manifest(root), request)
    candidate = render_node_mutation_plan(load_manifest(root), plan).decode()

    assert plan.operation == "update"
    assert plan.precondition.source_hash == _hash(original.encode())
    assert plan.state == "ready"
    assert 'title: "Quoted" # keep' in candidate
    assert "status: ready" in candidate
    assert "obsolete:" not in candidate
    assert "New body." in candidate
    assert target.read_text(encoding="utf-8") == original
    _validate_schema("node-mutation-plan-v1.schema.json", plan.to_dict())


def test_invalid_graph_is_visible_without_hiding_candidate(make_vault) -> None:
    root = make_vault()
    request = _create_request(
        properties={"tags": [], "related": ["[[notes/missing]]"]},
    )

    plan = plan_node_mutation(load_manifest(root), request)

    assert plan.state == "invalid"
    assert plan.validation.valid is False
    assert [issue.code for issue in plan.validation.issues] == ["relation.unresolved"]
    assert (
        render_node_mutation_plan(load_manifest(root), plan)
        .decode()
        .endswith("# New\n")
    )
    assert not (root / "notes" / "new.md").exists()


def test_update_requires_exact_source_hash(make_vault) -> None:
    root = make_vault(notes={"notes/existing.md": "# Existing\n"})
    target = root / "notes" / "existing.md"
    payload = {
        "schemaVersion": "vaultctl.node-mutation-request/v1",
        "operation": "update",
        "path": "notes/existing.md",
        "kind": "document",
        "expectedSourceHash": "0" * 64,
        "changes": {"body": "# Changed\n"},
    }

    with pytest.raises(MutationError, match="expectedSourceHash"):
        plan_node_mutation(
            load_manifest(root),
            node_mutation_request_from_dict(payload),
        )

    assert target.read_text(encoding="utf-8") == "# Existing\n"


def test_diff_marks_missing_final_newlines(make_vault) -> None:
    root = make_vault(notes={"notes/existing.md": "# Existing"})
    target = root / "notes" / "existing.md"
    request = _update_request(target, changes={"body": "# Changed"})

    plan = plan_node_mutation(load_manifest(root), request)

    assert plan.diff.count("\\ No newline at end of file") == 2
    assert "-# Existing\n\\ No newline" in plan.diff
    assert "+# Changed\n\\ No newline" in plan.diff


def test_render_rejects_stale_update_target(make_vault) -> None:
    root = make_vault(notes={"notes/existing.md": "# Existing\n"})
    target = root / "notes" / "existing.md"
    request = _update_request(target, changes={"body": "# Planned\n"})
    plan = plan_node_mutation(load_manifest(root), request)
    target.write_text("# Concurrent\n", encoding="utf-8")

    with pytest.raises(MutationError, match="hash is stale"):
        render_node_mutation_plan(load_manifest(root), plan)

    assert target.read_text(encoding="utf-8") == "# Concurrent\n"


def test_render_rejects_stale_prospective_vault(make_vault) -> None:
    root = make_vault()
    plan = plan_node_mutation(load_manifest(root), _create_request())
    unrelated = root / "notes" / "unrelated.md"
    unrelated.parent.mkdir()
    unrelated.write_text("# Concurrent\n", encoding="utf-8")

    with pytest.raises(MutationError, match="prospective vault state"):
        render_node_mutation_plan(load_manifest(root), plan)

    assert not (root / "notes" / "new.md").exists()


def test_create_rejects_ignored_path(make_vault) -> None:
    root = make_vault(manifest_overrides={"ignore": ["drafts/**"]})
    with pytest.raises(MarkdownError, match="ignore rules"):
        plan_node_mutation(
            load_manifest(root),
            _create_request(path="drafts/new.md"),
        )


def test_create_rejects_symlinked_parent(make_vault, tmp_path: Path) -> None:
    root = make_vault()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "notes").symlink_to(outside)
    with pytest.raises(MutationError, match="symlinked"):
        plan_node_mutation(load_manifest(root), _create_request())
    assert list(outside.iterdir()) == []


def test_candidate_kind_mismatch_is_a_typed_validation_error(make_vault) -> None:
    root = make_vault(
        manifest_overrides={
            "nodeKinds": {
                "document": {"selectors": [{"path": "notes/**"}]},
                "task": {"selectors": [{"path": "tasks/**"}]},
            },
        }
    )

    plan = plan_node_mutation(
        load_manifest(root),
        _create_request(path="tasks/new.md", kind="document", properties={}),
    )

    assert plan.state == "invalid"
    assert [issue.code for issue in plan.validation.issues] == ["node.kind-mismatch"]


def test_request_and_plan_loaders_fail_closed_on_ambiguous_or_tampered_data(
    make_vault,
) -> None:
    with pytest.raises(MutationError, match="invalid node mutation request"):
        node_mutation_request_from_dict(
            {
                "schemaVersion": "vaultctl.node-mutation-request/v1",
                "operation": "create",
                "path": "notes/new.md",
                "kind": "document",
                "document": {"properties": {}, "body": "# New\n"},
                "changes": {"body": "# Ambiguous\n"},
            }
        )

    root = make_vault()
    plan = plan_node_mutation(load_manifest(root), _create_request())
    payload = plan.to_dict()
    payload["candidate"]["source"] = "# Tampered\n"
    with pytest.raises(MutationError, match="plan digest"):
        node_mutation_plan_from_dict(payload)


def test_node_cli_plans_renders_and_diffs_without_writing(
    make_vault,
    tmp_path: Path,
    capsys,
) -> None:
    root = make_vault()
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "schemaVersion": "vaultctl.node-mutation-request/v1",
                "operation": "create",
                "path": "notes/new.md",
                "kind": "document",
                "document": {
                    "properties": {"tags": [], "related": []},
                    "body": "# New\n",
                },
            }
        ),
        encoding="utf-8",
    )

    plan_exit = main(
        [
            "--vault",
            str(root),
            "node",
            "plan",
            "--request",
            str(request_path),
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(payload), encoding="utf-8")
    render_exit = main(
        [
            "--vault",
            str(root),
            "node",
            "render",
            "--plan",
            str(plan_path),
        ]
    )
    rendered = capsys.readouterr().out
    diff_exit = main(
        [
            "--vault",
            str(root),
            "node",
            "diff",
            "--plan",
            str(plan_path),
        ]
    )
    diff = capsys.readouterr().out

    assert plan_exit == render_exit == diff_exit == 0
    assert payload["schemaVersion"] == "vaultctl.node-mutation-plan/v1"
    assert rendered == payload["candidate"]["source"]
    assert diff == payload["diff"]
    assert not (root / "notes" / "new.md").exists()
    assert load_node_mutation_plan(plan_path).plan_id == payload["planId"]
