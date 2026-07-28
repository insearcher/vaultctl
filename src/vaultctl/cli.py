from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from vaultctl import __version__
from vaultctl.engine import scan_vault
from vaultctl.errors import VaultctlError
from vaultctl.manifest import load_manifest, resolve_vault_root
from vaultctl.model import ScanResult


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vaultctl",
        description="Schema-driven CLI for Markdown vaults.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--vault",
        type=Path,
        help="Vault root. Defaults to discovery from the current directory.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
        help="Output format (default: json).",
    )

    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("scan", help="Normalize notes into nodes and edges.")
    commands.add_parser("validate", help="Validate manifest, notes, and graph.")
    commands.add_parser("doctor", help="Inspect vault and backend availability.")

    graph = commands.add_parser("graph", help="Graph operations.")
    graph_commands = graph.add_subparsers(dest="graph_command", required=True)
    graph_commands.add_parser("export", help="Export the normalized graph.")
    return parser


def _scan_payload(result: ScanResult) -> dict[str, Any]:
    return {
        "schemaVersion": "vaultctl.scan/v1",
        "vaultId": result.manifest.vault_id,
        "root": str(result.manifest.root),
        "valid": not result.errors,
        "nodes": [node.to_dict() for node in result.nodes],
        "edges": [edge.to_dict() for edge in result.edges],
        "issues": [issue.to_dict() for issue in result.issues],
    }


def _validation_payload(result: ScanResult) -> dict[str, Any]:
    return {
        "schemaVersion": "vaultctl.validate/v1",
        "vaultId": result.manifest.vault_id,
        "root": str(result.manifest.root),
        "valid": not result.errors,
        "summary": {
            "nodes": len(result.nodes),
            "edges": len(result.edges),
            "errors": len(result.errors),
            "warnings": len(result.warnings),
        },
        "issues": [issue.to_dict() for issue in result.issues],
    }


def _graph_payload(result: ScanResult) -> dict[str, Any]:
    return {
        "schemaVersion": "vaultctl.graph/v1",
        "vaultId": result.manifest.vault_id,
        "root": str(result.manifest.root),
        "valid": not result.errors,
        "nodes": [
            {
                "id": node.id,
                "path": node.path,
                "kind": node.kind,
                "title": node.title,
            }
            for node in result.nodes
        ],
        "edges": [edge.to_dict() for edge in result.edges],
        "issues": [issue.to_dict() for issue in result.issues],
    }


def _doctor_payload(root: Path) -> dict[str, Any]:
    manifest = load_manifest(root)
    return {
        "schemaVersion": "vaultctl.doctor/v1",
        "vaultId": manifest.vault_id,
        "root": str(root),
        "valid": True,
        "backends": {
            "filesystem": {"available": True},
            "obsidianLive": {
                "available": shutil.which("obsidian") is not None,
                "required": False,
            },
        },
    }


def _render_text(payload: dict[str, Any]) -> str:
    schema = payload["schemaVersion"]
    if schema == "vaultctl.validate/v1":
        summary = payload["summary"]
        if payload["valid"]:
            return (
                "validation ok: "
                f"{summary['nodes']} node(s), {summary['edges']} edge(s), "
                f"{summary['warnings']} warning(s)"
            )
        lines = [
            f"validation failed: {summary['errors']} error(s), "
            f"{summary['warnings']} warning(s)"
        ]
        lines.extend(
            f"- {issue.get('path', '<vault>')}: {issue['message']}"
            for issue in payload["issues"]
        )
        return "\n".join(lines)
    if schema == "vaultctl.doctor/v1":
        live = payload["backends"]["obsidianLive"]["available"]
        return (
            f"vault {payload['vaultId']}: ok\n"
            f"filesystem backend: available\n"
            f"obsidian live backend: {'available' if live else 'unavailable'}"
        )
    if schema == "vaultctl.graph/v1":
        return (
            f"graph: {len(payload['nodes'])} node(s), {len(payload['edges'])} edge(s)"
        )
    return (
        f"scan: {len(payload['nodes'])} node(s), "
        f"{len(payload['edges'])} edge(s), "
        f"{len(payload['issues'])} issue(s)"
    )


def _emit(payload: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(_render_text(payload))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root = resolve_vault_root(args.vault)
        if args.command == "doctor":
            payload = _doctor_payload(root)
            _emit(payload, args.format)
            return 0

        result = scan_vault(root)
        if args.command == "scan":
            payload = _scan_payload(result)
        elif args.command == "validate":
            payload = _validation_payload(result)
        else:
            payload = _graph_payload(result)
        _emit(payload, args.format)
        return 1 if result.errors else 0
    except VaultctlError as exc:
        if args.format == "json":
            print(
                json.dumps(
                    {
                        "schemaVersion": "vaultctl.error/v1",
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2
