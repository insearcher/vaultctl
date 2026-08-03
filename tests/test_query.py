from __future__ import annotations

import json

import pytest

from vaultctl.cli import main
from vaultctl.engine import scan_vault
from vaultctl.errors import QueryError
from vaultctl.query import query_nodes


def _query_vault(make_vault):
    return make_vault(
        manifest_overrides={
            "nodeKinds": {
                "document": {"selectors": [{"path": "notes/**"}]},
                "task": {"selectors": [{"path": "tasks/**"}]},
            },
            "relations": {
                "related": {
                    "field": "related",
                    "cardinality": "0..*",
                    "targetKinds": ["document", "task"],
                }
            },
        },
        notes={
            "notes/source.md": (
                "---\n"
                "status: active\n"
                "owner: ada\n"
                "priority: 2\n"
                "tags: [operations, shared]\n"
                "related: ['[[notes/target]]']\n"
                "---\n"
                "# Source\n"
            ),
            "notes/target.md": (
                "---\n"
                "status: archived\n"
                "tags: [shared]\n"
                "related: ['[[notes/source]]']\n"
                "---\n"
                "# Target\n"
            ),
            "tasks/orphan.md": (
                "---\n"
                "status: active\n"
                "owner: grace\n"
                "tags: [operations]\n"
                "related: []\n"
                "---\n"
                "# Orphan\n"
            ),
        },
    )


def test_query_filters_exact_properties_tags_fields_and_kind(make_vault) -> None:
    result = scan_vault(_query_vault(make_vault))

    nodes = query_nodes(
        result,
        kinds=("document",),
        tags=("#operations",),
        has_fields=("owner",),
        properties=(("status", "active"), ("priority", 2)),
    )

    assert [node.path for node in nodes] == ["notes/source.md"]


def test_query_path_patterns_are_alternatives_and_combine_with_other_filters(
    make_vault,
) -> None:
    result = scan_vault(_query_vault(make_vault))

    nodes = query_nodes(
        result,
        paths=("notes/**", "tasks/missing-*.md"),
        tags=("shared",),
    )

    assert [node.path for node in nodes] == [
        "notes/source.md",
        "notes/target.md",
    ]


def test_query_path_double_star_crosses_directory_boundaries(make_vault) -> None:
    root = make_vault(
        notes={
            "notes/root.md": "# Root\n",
            "notes/nested/child.md": "# Child\n",
        }
    )
    result = scan_vault(root)

    nodes = query_nodes(result, paths=("notes/**",))

    assert [node.path for node in nodes] == [
        "notes/nested/child.md",
        "notes/root.md",
    ]


@pytest.mark.parametrize(
    "pattern",
    ("", "/notes/**", "../notes/**", "notes//**", r"notes\**"),
)
def test_query_rejects_unsafe_or_non_normalized_path_patterns(
    make_vault,
    pattern: str,
) -> None:
    result = scan_vault(_query_vault(make_vault))

    with pytest.raises(QueryError, match="normalized vault-relative"):
        query_nodes(result, paths=(pattern,))


def test_query_normalizes_filter_names(make_vault) -> None:
    result = scan_vault(_query_vault(make_vault))

    nodes = query_nodes(
        result,
        kinds=(" document ",),
        tags=(" #operations ",),
        has_fields=(" owner ",),
        properties=((" status ", "active"),),
    )

    assert [node.path for node in nodes] == ["notes/source.md"]


def test_query_null_property_does_not_match_a_missing_property(make_vault) -> None:
    result = scan_vault(_query_vault(make_vault))

    nodes = query_nodes(result, properties=(("owner", None),))

    assert nodes == ()


def test_query_without_incoming_returns_graph_orphans(make_vault) -> None:
    result = scan_vault(_query_vault(make_vault))

    nodes = query_nodes(result, without_incoming=True)

    assert [node.path for node in nodes] == ["tasks/orphan.md"]


def test_query_rejects_unknown_kind_duplicate_property_and_bad_limit(
    make_vault,
) -> None:
    result = scan_vault(_query_vault(make_vault))

    with pytest.raises(QueryError, match="unknown node kind"):
        query_nodes(result, kinds=("missing",))
    with pytest.raises(QueryError, match="repeats property"):
        query_nodes(result, properties=(("status", "active"), ("status", "done")))
    with pytest.raises(QueryError, match="greater than zero"):
        query_nodes(result, limit=0)


def test_query_limit_preserves_stable_path_order(make_vault) -> None:
    result = scan_vault(_query_vault(make_vault))

    nodes = query_nodes(result, limit=2)

    assert [node.path for node in nodes] == ["notes/source.md", "notes/target.md"]


def test_query_cli_emits_versioned_projection_and_incoming_edges(
    make_vault,
    capsys,
) -> None:
    root = _query_vault(make_vault)

    exit_code = main(
        [
            "--vault",
            str(root),
            "query",
            "--path",
            "notes/**",
            "--kind",
            "document",
            "--where",
            "status=archived",
            "--has-field",
            "status",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schemaVersion"] == "vaultctl.query/v1"
    assert payload["filters"]["properties"] == [
        {"field": "status", "value": "archived"}
    ]
    assert payload["filters"]["paths"] == ["notes/**"]
    assert payload["nodes"][0]["path"] == "notes/target.md"
    assert payload["nodes"][0]["incomingEdges"][0]["source"] == "notes/source"


def test_query_cli_parses_typed_json_values(make_vault, capsys) -> None:
    root = _query_vault(make_vault)

    exit_code = main(["--vault", str(root), "query", "--where", "priority=2"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["nodes"][0]["path"] == "notes/source.md"
    assert payload["filters"]["properties"][0]["value"] == 2


def test_query_cli_returns_one_for_no_matches(make_vault, capsys) -> None:
    root = _query_vault(make_vault)

    exit_code = main(["--vault", str(root), "query", "--where", "status=missing"])

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["nodes"] == []


def test_query_text_output_is_a_disposable_derived_view(
    make_vault,
    capsys,
) -> None:
    root = _query_vault(make_vault)

    exit_code = main(
        ["--vault", str(root), "--format", "text", "query", "--kind", "task"]
    )

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == ("task          tasks/orphan.md — Orphan")
