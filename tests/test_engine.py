from __future__ import annotations

from pathlib import Path

from vaultctl.engine import scan_vault
from vaultctl.manifest import load_manifest


def test_example_vault_scans() -> None:
    root = Path(__file__).parents[1] / "examples" / "basic-vault"

    result = scan_vault(root)

    assert result.errors == ()
    assert [node.id for node in result.nodes] == [
        "notes/roadmap",
        "notes/welcome",
    ]
    assert {
        (edge.source, edge.relation, edge.target, edge.provenance)
        for edge in result.edges
    } == {
        (
            "notes/welcome",
            "link",
            "notes/roadmap",
            "markdown-link",
        ),
        (
            "notes/welcome",
            "related",
            "notes/roadmap",
            "frontmatter:related:wikilink",
        ),
    }


def test_manifest_can_enable_legacy_colon_scalar_compatibility(make_vault) -> None:
    root = make_vault(
        manifest_overrides={
            "frontmatter": {"allowLegacyColonScalars": True},
        },
        notes={
            "notes/example.md": (
                "---\n"
                "description: Use when route: fallback\n"
                "tags: []\n"
                "related: []\n"
                "---\n"
                "# Example\n"
            )
        },
    )

    result = scan_vault(root)

    assert result.errors == ()
    assert result.nodes[0].properties["description"] == "Use when route: fallback"


def test_ambiguous_kind_is_an_error(make_vault) -> None:
    root = make_vault(
        manifest_overrides={
            "nodeKinds": {
                "document": {"selectors": [{"path": "notes/**"}]},
                "guide": {"selectors": [{"tag": "guide"}]},
            }
        },
        notes={
            "notes/example.md": ("---\ntags: [guide]\nrelated: []\n---\n# Example\n")
        },
    )

    result = scan_vault(root)

    assert [issue.code for issue in result.errors] == ["node.ambiguous-kind"]


def test_single_star_does_not_cross_directory_boundaries(make_vault) -> None:
    root = make_vault(
        manifest_overrides={
            "defaultKind": "document",
            "nodeKinds": {
                "document": {"selectors": [{"path": "*.md"}]},
                "nested": {"selectors": [{"path": "notes/**"}]},
            },
            "relations": {},
        },
        notes={
            "root.md": "# Root\n",
            "notes/example.md": "# Nested\n",
        },
    )

    result = scan_vault(root)

    assert result.errors == ()
    assert [(node.id, node.kind) for node in result.nodes] == [
        ("notes/example", "nested"),
        ("root", "document"),
    ]


def test_many_relation_requires_a_list(make_vault) -> None:
    root = make_vault(
        notes={
            "notes/example.md": (
                "---\ntags: []\nrelated: '[[notes/target]]'\n---\n# Example\n"
            ),
            "notes/target.md": "---\ntags: []\nrelated: []\n---\n# Target\n",
        }
    )

    result = scan_vault(root)

    assert [issue.code for issue in result.errors] == ["relation.cardinality"]


def test_unresolved_declared_relation_is_an_error(make_vault) -> None:
    root = make_vault(
        notes={
            "notes/example.md": (
                "---\ntags: []\nrelated: ['[[notes/missing]]']\n---\n# Example\n"
            )
        }
    )

    result = scan_vault(root)

    assert [issue.code for issue in result.errors] == ["relation.unresolved"]


def test_unresolved_body_link_is_a_warning(make_vault) -> None:
    root = make_vault(
        notes={
            "notes/example.md": (
                "---\ntags: []\nrelated: []\n---\n# Example\n\nSee [[missing]].\n"
            )
        }
    )

    result = scan_vault(root)

    assert result.errors == ()
    assert [issue.code for issue in result.warnings] == ["link.unresolved"]


def test_unresolved_internal_markdown_link_is_a_warning(make_vault) -> None:
    root = make_vault(
        notes={
            "notes/example.md": (
                "---\ntags: []\nrelated: []\n---\n"
                "# Example\n\nSee [missing](missing.md).\n"
            )
        }
    )

    result = scan_vault(root)

    assert result.errors == ()
    assert [issue.code for issue in result.warnings] == ["link.unresolved"]


def test_internal_parent_relative_markdown_link_resolves(make_vault) -> None:
    root = make_vault(
        notes={
            "notes/deep/example.md": (
                "---\ntags: []\nrelated: []\n---\n"
                "# Example\n\nSee [target](../target.md).\n"
            ),
            "notes/target.md": "---\ntags: []\nrelated: []\n---\n# Target\n",
        }
    )

    result = scan_vault(root)

    assert result.issues == ()
    assert {
        (edge.source, edge.relation, edge.target, edge.provenance)
        for edge in result.edges
    } == {
        (
            "notes/deep/example",
            "link",
            "notes/target",
            "markdown-link",
        )
    }


def test_escaping_markdown_link_is_external(make_vault) -> None:
    root = make_vault(
        notes={
            "notes/example.md": (
                "---\ntags: []\nrelated: []\n---\n"
                "# Example\n\nSee [source](../../sibling/source.md).\n"
            )
        }
    )

    result = scan_vault(root)

    assert result.issues == ()
    assert result.edges == ()


def test_escaping_declared_relation_remains_an_error(make_vault) -> None:
    root = make_vault(
        notes={
            "notes/example.md": (
                "---\ntags: []\nrelated: ['../../sibling/source.md']\n---\n# Example\n"
            )
        }
    )

    result = scan_vault(root)

    assert [issue.code for issue in result.errors] == ["relation.unresolved"]


def test_body_wikilink_aliases_resolve(make_vault) -> None:
    root = make_vault(
        notes={
            "notes/example.md": (
                "---\ntags: []\nrelated: []\n---\n"
                "# Example\n\n"
                "See [[notes/target|Target]] and "
                "[[notes/target\\|Escaped target]].\n"
            ),
            "notes/target.md": "---\ntags: []\nrelated: []\n---\n# Target\n",
        }
    )

    result = scan_vault(root)

    assert result.issues == ()
    assert [
        (edge.relation, edge.target, edge.provenance)
        for edge in result.nodes[0].outgoing_edges
    ] == [
        ("link", "notes/target", "wikilink"),
        ("link", "notes/target", "wikilink"),
    ]


def test_markdown_artifact_links_are_not_note_edges(make_vault) -> None:
    root = make_vault(
        notes={
            "notes/example.md": (
                "---\ntags: []\nrelated: []\n---\n"
                "# Example\n\n"
                "Download [data](../assets/data.json).\n"
            )
        }
    )

    result = scan_vault(root)

    assert result.issues == ()
    assert result.edges == ()


def test_absolute_markdown_paths_are_not_note_edges(make_vault) -> None:
    root = make_vault(
        notes={
            "notes/example.md": (
                "---\ntags: []\nrelated: []\n---\n"
                "# Example\n\n"
                "Open [local source](/workspace/project/source.py).\n"
            )
        }
    )

    result = scan_vault(root)

    assert result.issues == ()
    assert result.edges == ()


def test_wikilink_artifacts_are_not_note_edges(make_vault) -> None:
    root = make_vault(
        notes={
            "notes/example.md": (
                "---\ntags: []\nrelated: []\n---\n"
                "# Example\n\n"
                "Open [[views/planning.canvas]].\n"
            )
        }
    )

    result = scan_vault(root)

    assert result.issues == ()
    assert result.edges == ()


def test_symlink_escape_is_rejected(make_vault, tmp_path: Path) -> None:
    root = make_vault(notes={})
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n", encoding="utf-8")
    notes = root / "notes"
    (notes / "escaped.md").symlink_to(outside)

    result = scan_vault(root)

    assert [issue.code for issue in result.errors] == ["path.escape"]


def test_unknown_manifest_fields_fail_closed(make_vault) -> None:
    root = make_vault(manifest_overrides={"shellHook": "do-something"})

    try:
        load_manifest(root)
    except Exception as exc:
        message = str(exc)
    else:
        raise AssertionError("manifest unexpectedly accepted an unknown field")

    assert "shellHook" in message


def test_property_search_zone_requires_a_field(make_vault) -> None:
    root = make_vault(
        manifest_overrides={
            "search": {
                "zones": [
                    {
                        "source": "property",
                        "weight": 10,
                    }
                ]
            }
        }
    )

    try:
        load_manifest(root)
    except Exception as exc:
        message = str(exc)
    else:
        raise AssertionError("property search zone unexpectedly accepted no field")

    assert "property source requires field" in message


def test_search_boost_rejects_unknown_kind(make_vault) -> None:
    root = make_vault(
        manifest_overrides={
            "search": {
                "boosts": [
                    {
                        "kind": "missing",
                        "weight": 5,
                    }
                ]
            }
        }
    )

    try:
        load_manifest(root)
    except Exception as exc:
        message = str(exc)
    else:
        raise AssertionError("search boost unexpectedly accepted an unknown kind")

    assert "search.boosts.0" in message
    assert "missing" in message


def test_manifest_limit_defaults_cannot_exceed_maximum(make_vault) -> None:
    root = make_vault(
        manifest_overrides={
            "context": {
                "maxLimit": 4,
            }
        }
    )

    try:
        load_manifest(root)
    except Exception as exc:
        message = str(exc)
    else:
        raise AssertionError("inconsistent context limits were accepted")

    assert "context.defaultLimit cannot exceed context.maxLimit" in message


def test_context_grouping_requires_a_key_source(make_vault) -> None:
    root = make_vault(
        manifest_overrides={
            "context": {
                "grouping": {
                    "notesPerGroup": 2,
                }
            }
        }
    )

    try:
        load_manifest(root)
    except Exception as exc:
        message = str(exc)
    else:
        raise AssertionError("context grouping unexpectedly accepted no key source")

    assert "context.grouping" in message


def test_manifest_accepts_declarative_merge_policy(make_vault) -> None:
    root = make_vault(
        manifest_overrides={
            "merge": {
                "defaultFieldStrategy": "manual",
                "fields": {
                    "tags": {
                        "strategy": "set",
                    },
                    "status": {
                        "strategy": "scalar",
                    },
                },
                "bodyStrategy": "manual",
            }
        }
    )

    manifest = load_manifest(root)

    assert manifest.raw["merge"]["fields"]["tags"]["strategy"] == "set"


def test_manifest_rejects_executable_merge_policy(make_vault) -> None:
    root = make_vault(
        manifest_overrides={
            "merge": {
                "fields": {
                    "tags": {
                        "strategy": "shell",
                    }
                }
            }
        }
    )

    try:
        load_manifest(root)
    except Exception as exc:
        message = str(exc)
    else:
        raise AssertionError("manifest unexpectedly accepted executable merge policy")

    assert "merge.fields.tags.strategy" in message
    assert "shell" in message


def test_acyclic_relation_detects_a_cycle(make_vault) -> None:
    root = make_vault(
        manifest_overrides={
            "relations": {
                "related": {
                    "field": "related",
                    "cardinality": "0..*",
                    "targetKinds": ["document"],
                    "acyclic": True,
                }
            }
        },
        notes={
            "notes/one.md": ("---\ntags: []\nrelated: ['[[notes/two]]']\n---\n# One\n"),
            "notes/two.md": ("---\ntags: []\nrelated: ['[[notes/one]]']\n---\n# Two\n"),
        },
    )

    result = scan_vault(root)

    assert [issue.code for issue in result.errors] == ["relation.cycle"]
