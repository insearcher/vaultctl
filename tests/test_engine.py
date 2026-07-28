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


def test_symlink_escape_is_rejected(make_vault, tmp_path: Path) -> None:
    root = make_vault(notes={})
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n", encoding="utf-8")
    notes = root / "notes"
    notes.mkdir()
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
