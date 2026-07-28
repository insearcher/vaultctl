from __future__ import annotations

import json
from pathlib import Path

from vaultctl.cli import main


def test_validate_emits_versioned_json(make_vault, capsys) -> None:
    root = make_vault(
        notes={
            "notes/example.md": "---\ntags: [example]\nrelated: []\n---\n# Example\n"
        }
    )

    exit_code = main(["--vault", str(root), "validate"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schemaVersion"] == "vaultctl.validate/v1"
    assert payload["valid"] is True
    assert payload["summary"]["nodes"] == 1


def test_graph_text_output(make_vault, capsys) -> None:
    root = make_vault(
        notes={"notes/example.md": "---\ntags: []\nrelated: []\n---\n# Example\n"}
    )

    exit_code = main(["--vault", str(root), "--format", "text", "graph", "export"])

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "graph: 1 node(s), 0 edge(s)"


def test_discovery_walks_up_from_current_directory(
    make_vault,
    monkeypatch,
    capsys,
) -> None:
    root = make_vault(
        notes={"notes/example.md": "---\ntags: []\nrelated: []\n---\n# Example\n"}
    )
    nested = root / "notes" / "nested"
    nested.mkdir()
    monkeypatch.chdir(nested)

    exit_code = main(["doctor"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["vaultId"] == "test-vault"
    assert payload["backends"]["filesystem"]["available"] is True


def test_missing_manifest_is_a_user_error(tmp_path: Path, capsys) -> None:
    exit_code = main(["--vault", str(tmp_path), "scan"])

    assert exit_code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["schemaVersion"] == "vaultctl.error/v1"


def test_search_and_context_emit_versioned_json(make_vault, capsys) -> None:
    root = make_vault(
        notes={
            "notes/example.md": (
                "---\ntags: []\nrelated: []\n---\n"
                "# Example\n\nRelease planning details.\n"
            )
        }
    )

    search_exit = main(["--vault", str(root), "search", "release"])
    search_payload = json.loads(capsys.readouterr().out)
    context_exit = main(["--vault", str(root), "context", "release"])
    context_payload = json.loads(capsys.readouterr().out)

    assert search_exit == 0
    assert search_payload["schemaVersion"] == "vaultctl.search/v1"
    assert search_payload["hits"][0]["path"] == "notes/example.md"
    assert context_exit == 0
    assert context_payload["schemaVersion"] == "vaultctl.context/v1"
    assert context_payload["hits"][0]["snippets"] == ["Release planning details."]
    assert context_payload["groups"] == []
    assert context_payload["budget"]["truncated"] is False


def test_context_emits_group_contract(make_vault, capsys) -> None:
    root = make_vault(
        manifest_overrides={
            "context": {
                "grouping": {
                    "fields": ["topic"],
                }
            }
        },
        notes={
            "notes/example.md": (
                "---\n"
                "topic: example-group\n"
                "tags: []\n"
                "related: []\n"
                "---\n"
                "# Example\n\n"
                "Grouped query.\n"
            )
        },
    )

    exit_code = main(["--vault", str(root), "context", "grouped"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["groups"][0]["key"] == "example-group"
    assert payload["groups"][0]["representative"] == "notes/example.md"
    assert payload["groups"][0]["topMatch"] is None


def test_search_limit_error_uses_error_contract(make_vault, capsys) -> None:
    root = make_vault(
        manifest_overrides={
            "search": {
                "defaultLimit": 1,
                "maxLimit": 1,
            }
        },
        notes={
            "notes/example.md": (
                "---\ntags: []\nrelated: []\n---\n# Example\nSearchable.\n"
            )
        },
    )

    exit_code = main(["--vault", str(root), "search", "searchable", "--limit", "2"])

    assert exit_code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["schemaVersion"] == "vaultctl.error/v1"
    assert "exceeds manifest maxLimit" in payload["error"]


def test_stopword_only_search_and_context_are_no_hit(make_vault, capsys) -> None:
    root = make_vault(
        notes={
            "notes/example.md": (
                "---\ntags: []\nrelated: []\n---\n"
                "# Example\n\nAnd then there were none.\n"
            )
        }
    )

    search_exit = main(["--vault", str(root), "search", "and"])
    search_payload = json.loads(capsys.readouterr().out)
    context_exit = main(["--vault", str(root), "context", "and"])
    context_payload = json.loads(capsys.readouterr().out)

    assert search_exit == 1
    assert search_payload["hits"] == []
    assert context_exit == 1
    assert context_payload["hits"] == []
