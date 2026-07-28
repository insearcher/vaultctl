from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

SCHEMA_URL = (
    "https://raw.githubusercontent.com/insearcher/vaultctl/"
    "main/src/vaultctl/schemas/manifest-v1.schema.json"
)


@pytest.fixture
def make_vault(tmp_path: Path):
    def factory(
        *,
        manifest_overrides: dict[str, Any] | None = None,
        notes: dict[str, str] | None = None,
    ) -> Path:
        root = tmp_path / "vault"
        manifest_dir = root / ".vaultctl"
        manifest_dir.mkdir(parents=True)
        manifest: dict[str, Any] = {
            "$schema": SCHEMA_URL,
            "apiVersion": "vaultctl/v1",
            "vaultId": "test-vault",
            "defaultKind": "document",
            "nodeKinds": {
                "document": {
                    "selectors": [{"path": "notes/**"}],
                    "fields": {"tags": {"type": "list"}},
                }
            },
            "relations": {
                "related": {
                    "field": "related",
                    "cardinality": "0..*",
                    "targetKinds": ["document"],
                }
            },
        }
        if manifest_overrides:
            manifest.update(manifest_overrides)
        (manifest_dir / "manifest.json").write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )
        for relative, content in (notes or {}).items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return root

    return factory
