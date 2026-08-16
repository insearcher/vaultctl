from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "vaultctl"
SKILL = PLUGIN / "skills" / "vaultctl-agent" / "SKILL.md"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_cross_runtime_marketplaces_publish_one_vaultctl_plugin() -> None:
    codex = _json(ROOT / ".agents" / "plugins" / "marketplace.json")
    claude = _json(ROOT / ".claude-plugin" / "marketplace.json")

    assert codex["name"] == claude["name"] == "insearcher"
    assert [entry["name"] for entry in codex["plugins"]] == ["vaultctl"]
    assert [entry["name"] for entry in claude["plugins"]] == ["vaultctl"]
    assert codex["plugins"][0]["source"] == {
        "source": "local",
        "path": "./plugins/vaultctl",
    }
    assert claude["plugins"][0]["source"] == "./plugins/vaultctl"
    assert codex["plugins"][0]["policy"] == {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    }


def test_cross_runtime_plugin_manifests_match() -> None:
    codex = _json(PLUGIN / ".codex-plugin" / "plugin.json")
    claude = _json(PLUGIN / ".claude-plugin" / "plugin.json")

    for field in (
        "name",
        "version",
        "description",
        "author",
        "homepage",
        "repository",
        "license",
        "keywords",
        "skills",
    ):
        assert codex[field] == claude[field]
    assert codex["name"] == "vaultctl"
    assert codex["version"] == "0.1.2"
    assert codex["skills"] == "./skills/"
    for forbidden in ("apps", "hooks", "mcpServers"):
        assert forbidden not in codex
        assert forbidden not in claude


def test_agent_skill_keeps_generic_and_consumer_owned_boundaries() -> None:
    content = SKILL.read_text(encoding="utf-8")
    normalized = " ".join(content.split())

    for contract in (
        "consumer skill",
        "version and provenance check",
        "node plan --request",
        "node diff --plan",
        "node apply --plan",
        "node-mutation-apply",
        "vaultctl.node-mutation-receipt/v1",
        "merge plan",
        "merge validate",
        "never use `git add -A`",
        "commit and push as separate agent-owned decisions",
        "A manifest capability is a technical gate, not authorization",
    ):
        assert contract in normalized
