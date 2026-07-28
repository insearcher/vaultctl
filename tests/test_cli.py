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
