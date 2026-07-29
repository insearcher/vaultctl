from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from vaultctl.errors import MergeError
from vaultctl.markdown import ParsedMarkdown
from vaultctl.merge import plan_merge
from vaultctl.model import Receipt, VaultManifest

BASE_REVISION = "a" * 40
OURS_REVISION = "b" * 40
THEIRS_REVISION = "c" * 40


def _document(
    properties: dict[str, object] | None = None,
    body: str = "# Example\n",
) -> ParsedMarkdown:
    properties = properties or {}
    source = json.dumps(
        {"properties": properties, "body": body},
        ensure_ascii=False,
        sort_keys=True,
    )
    return ParsedMarkdown(
        properties=properties,
        body=body,
        title="Example",
        headings=("Example",),
        source_hash=hashlib.sha256(source.encode()).hexdigest(),
    )


def _manifest(
    *,
    fields: dict[str, str] | None = None,
    default_strategy: str = "manual",
) -> VaultManifest:
    merge = {
        "defaultFieldStrategy": default_strategy,
        "bodyStrategy": "manual",
        "fields": {
            field: {"strategy": strategy} for field, strategy in (fields or {}).items()
        },
    }
    raw = {
        "$schema": "synthetic",
        "apiVersion": "vaultctl/v1",
        "vaultId": "synthetic-vault",
        "nodeKinds": {"document": {"selectors": [{"path": "notes/**"}]}},
        "merge": merge,
    }
    return VaultManifest(
        api_version="vaultctl/v1",
        vault_id="synthetic-vault",
        root=Path("."),
        node_kinds=raw["nodeKinds"],
        relations={},
        raw=raw,
    )


def _plan(
    *,
    manifest: VaultManifest | None = None,
    base: ParsedMarkdown | None = None,
    ours: ParsedMarkdown | None = None,
    theirs: ParsedMarkdown | None = None,
):
    document = _document({"status": "draft"})
    return plan_merge(
        manifest or _manifest(fields={"status": "scalar"}),
        path="notes/example.md",
        base=base or document,
        ours=ours or document,
        theirs=theirs or document,
        base_revision=BASE_REVISION,
        ours_revision=OURS_REVISION,
        theirs_revision=THEIRS_REVISION,
    )


def _schema(name: str) -> dict[str, object]:
    path = Path(__file__).parents[1] / "src" / "vaultctl" / "schemas" / name
    return json.loads(path.read_text(encoding="utf-8"))


def test_merge_plan_accepts_one_sided_scalar_change() -> None:
    plan = _plan(
        ours=_document({"status": "ready"}),
    )

    assert plan.state == "clean"
    assert plan.conflicts == ()
    assert plan.candidate is not None
    assert plan.candidate.properties == {"status": "ready"}
    assert [
        (decision.location, decision.resolution) for decision in plan.decisions
    ] == [
        ("frontmatter.status", "ours"),
        ("body", "unchanged"),
    ]


def test_merge_plan_fails_closed_on_concurrent_scalar_change() -> None:
    plan = _plan(
        ours=_document({"status": "ready"}),
        theirs=_document({"status": "blocked"}),
    )

    assert plan.state == "conflict"
    assert plan.candidate is None
    assert [conflict.kind for conflict in plan.conflicts] == [
        "frontmatter.concurrent-change"
    ]
    assert plan.conflicts[0].location == "frontmatter.status"


def test_set_strategy_merges_independent_additions_symmetrically() -> None:
    manifest = _manifest(fields={"tags": "set"})
    base = _document({"tags": ["shared"]})
    ours = _document({"tags": ["shared", "alpha"]})
    theirs = _document({"tags": ["shared", "beta"]})

    plan = _plan(manifest=manifest, base=base, ours=ours, theirs=theirs)
    swapped = _plan(manifest=manifest, base=base, ours=theirs, theirs=ours)

    assert plan.state == "clean"
    assert plan.candidate is not None
    assert plan.candidate.properties == {"tags": ["shared", "alpha", "beta"]}
    assert swapped.candidate is not None
    assert swapped.candidate.properties == plan.candidate.properties
    assert plan.decisions[0].resolution == "set-merge"


def test_set_strategy_rejects_non_list_concurrent_values() -> None:
    manifest = _manifest(fields={"tags": "set"})
    plan = _plan(
        manifest=manifest,
        base=_document({"tags": ["shared"]}),
        ours=_document({"tags": "alpha"}),
        theirs=_document({"tags": ["shared", "beta"]}),
    )

    assert plan.state == "conflict"
    assert plan.conflicts[0].kind == "frontmatter.type-conflict"


def test_set_strategy_rejects_non_list_one_sided_change() -> None:
    manifest = _manifest(fields={"tags": "set"})
    plan = _plan(
        manifest=manifest,
        base=_document({"tags": ["shared"]}),
        ours=_document({"tags": "alpha"}),
        theirs=_document({"tags": ["shared"]}),
    )

    assert plan.state == "conflict"
    assert plan.conflicts[0].kind == "frontmatter.type-conflict"


def test_merge_plan_fails_closed_on_concurrent_body_change() -> None:
    plan = _plan(
        ours=_document({"status": "draft"}, "# Ours\n"),
        theirs=_document({"status": "draft"}, "# Theirs\n"),
    )

    assert plan.state == "conflict"
    assert plan.candidate is None
    assert [conflict.location for conflict in plan.conflicts] == ["body"]
    assert "value" not in plan.conflicts[0].ours
    assert plan.conflicts[0].ours["characters"] == len("# Ours\n")


def test_merge_plan_is_deterministic_and_matches_schema() -> None:
    first = _plan(ours=_document({"status": "ready"}))
    second = _plan(ours=_document({"status": "ready"}))
    payload = first.to_dict()

    assert second.to_dict() == payload
    schema = _schema("merge-plan-v1.schema.json")
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(payload)


def test_receipt_contract_matches_schema() -> None:
    digest = f"sha256:{'d' * 64}"
    receipt = Receipt(
        schema_version="vaultctl.receipt/v1",
        vault_id="synthetic-vault",
        operation_id="operation-1",
        paths=("notes/example.md",),
        before_hashes={"notes/example.md": "1" * 64},
        after_hashes={"notes/example.md": "2" * 64},
        state="applied",
        plan_id=digest,
        plan_digest=digest,
        input_revisions={
            "base": BASE_REVISION,
            "ours": OURS_REVISION,
            "theirs": THEIRS_REVISION,
        },
        manifest_digest=digest,
        engine_version="0.1.0a1",
    )

    schema = _schema("receipt-v1.schema.json")
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(receipt.to_dict())


@pytest.mark.parametrize(
    ("path", "revision", "message"),
    [
        ("../example.md", BASE_REVISION, "vault-relative"),
        ("notes/example.txt", BASE_REVISION, "Markdown path"),
        ("notes/example.md", "not-a-revision", "40- or 64-character"),
    ],
)
def test_merge_plan_rejects_unconfined_inputs(
    path: str,
    revision: str,
    message: str,
) -> None:
    document = _document()

    with pytest.raises(MergeError, match=message):
        plan_merge(
            _manifest(),
            path=path,
            base=document,
            ours=document,
            theirs=document,
            base_revision=revision,
            ours_revision=OURS_REVISION,
            theirs_revision=THEIRS_REVISION,
        )


def test_synthetic_merge_corpus_has_zero_false_clean() -> None:
    corpus_path = Path(__file__).parent / "fixtures" / "merge-corpus-v1.json"
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    cases = corpus["cases"]
    schema = Draft202012Validator(_schema("merge-plan-v1.schema.json"))

    assert corpus["schemaVersion"] == "vaultctl.merge-corpus/v1"
    assert len(cases) == 50
    assert len({case["id"] for case in cases}) == 50

    for case in cases:
        default_body = corpus["defaultBody"]
        manifest = _manifest(fields=case.get("fields", {}))
        base = _document(case["base"], case.get("baseBody", default_body))
        ours = _document(case["ours"], case.get("oursBody", default_body))
        theirs = _document(case["theirs"], case.get("theirsBody", default_body))
        plan = _plan(
            manifest=manifest,
            base=base,
            ours=ours,
            theirs=theirs,
        )

        assert plan.state == case["expectedState"], case["id"]
        schema.validate(plan.to_dict())
        assert (
            _plan(
                manifest=manifest,
                base=base,
                ours=ours,
                theirs=theirs,
            ).to_dict()
            == plan.to_dict()
        ), case["id"]

        if plan.state == "clean":
            assert plan.candidate is not None
            assert plan.candidate.properties == case["expectedProperties"], case["id"]
            assert plan.candidate.body == case.get(
                "expectedBody",
                default_body,
            ), case["id"]
        else:
            assert plan.candidate is None
            assert [conflict.location for conflict in plan.conflicts] == case[
                "conflictLocations"
            ], case["id"]

        if case.get("symmetric"):
            swapped = _plan(
                manifest=manifest,
                base=base,
                ours=theirs,
                theirs=ours,
            )
            assert swapped.state == plan.state, case["id"]
            assert swapped.candidate == plan.candidate, case["id"]
            assert [conflict.location for conflict in swapped.conflicts] == [
                conflict.location for conflict in plan.conflicts
            ], case["id"]
