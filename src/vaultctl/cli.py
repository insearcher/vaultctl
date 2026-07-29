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
from vaultctl.merge import load_merge_plan, plan_merge_files
from vaultctl.model import ContextResult, ScanResult, SearchHit
from vaultctl.mutation import (
    diff_node_mutation_plan,
    load_node_mutation_plan,
    load_node_mutation_request,
    plan_node_mutation,
    render_node_mutation_plan,
)
from vaultctl.search import context as build_context
from vaultctl.search import search as search_nodes
from vaultctl.transaction import validate_merge_plan


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

    search = commands.add_parser("search", help="Rank notes for a text query.")
    search.add_argument("query")
    search.add_argument("--limit", type=int)

    context = commands.add_parser(
        "context",
        help="Return ranked notes and snippets within the manifest budget.",
    )
    context.add_argument("query")
    context.add_argument("--limit", type=int)

    graph = commands.add_parser("graph", help="Graph operations.")
    graph_commands = graph.add_subparsers(dest="graph_command", required=True)
    graph_commands.add_parser("export", help="Export the normalized graph.")

    merge = commands.add_parser("merge", help="Semantic merge operations.")
    merge_commands = merge.add_subparsers(dest="merge_command", required=True)
    merge_plan = merge_commands.add_parser(
        "plan",
        help="Plan a read-only semantic merge for one Markdown triple.",
    )
    merge_plan.add_argument("--path", required=True)
    merge_plan.add_argument("--base", type=Path, required=True)
    merge_plan.add_argument("--ours", type=Path, required=True)
    merge_plan.add_argument("--theirs", type=Path, required=True)
    merge_plan.add_argument("--base-revision", required=True)
    merge_plan.add_argument("--ours-revision", required=True)
    merge_plan.add_argument("--theirs-revision", required=True)
    merge_validate = merge_commands.add_parser(
        "validate",
        help="Validate a clean merge plan against the prospective whole vault.",
    )
    merge_validate.add_argument("--plan", type=Path, required=True)

    node = commands.add_parser("node", help="Typed node mutation planning.")
    node_commands = node.add_subparsers(dest="node_command", required=True)
    node_plan = node_commands.add_parser(
        "plan",
        help="Plan and prospectively validate one read-only create or update.",
    )
    node_plan.add_argument("--request", type=Path, required=True)
    node_render = node_commands.add_parser(
        "render",
        help="Render exact candidate Markdown from a current plan without writing.",
    )
    node_render.add_argument("--plan", type=Path, required=True)
    node_diff = node_commands.add_parser(
        "diff",
        help="Render a current plan's unified diff without writing.",
    )
    node_diff.add_argument("--plan", type=Path, required=True)
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


def _search_payload(
    result: ScanResult,
    *,
    query: str,
    hits: tuple[SearchHit, ...],
) -> dict[str, Any]:
    return {
        "schemaVersion": "vaultctl.search/v1",
        "vaultId": result.manifest.vault_id,
        "root": str(result.manifest.root),
        "valid": not result.errors,
        "query": query,
        "hits": [hit.to_dict() for hit in hits],
        "issues": [issue.to_dict() for issue in result.issues],
    }


def _context_payload(
    result: ScanResult,
    *,
    query: str,
    context_result: ContextResult,
) -> dict[str, Any]:
    return {
        "schemaVersion": "vaultctl.context/v1",
        "vaultId": result.manifest.vault_id,
        "root": str(result.manifest.root),
        "valid": not result.errors,
        "query": query,
        "budget": {
            "maxCharacters": context_result.max_characters,
            "usedCharacters": context_result.used_characters,
            "truncated": context_result.truncated,
        },
        "hits": [hit.to_dict() for hit in context_result.hits],
        "groups": [group.to_dict() for group in context_result.groups],
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
    if schema == "vaultctl.search/v1":
        if not payload["hits"]:
            return "No hits."
        return "\n".join(
            f"{hit['score']:>4}  {hit['path']} — {hit['title']}"
            for hit in payload["hits"]
        )
    if schema == "vaultctl.context/v1":
        if not payload["hits"]:
            return "No context hits."
        lines = []
        if payload["groups"]:
            for group in payload["groups"]:
                lines.append(
                    f"▸ {group['key']} (score {group['score']}, notes {group['count']})"
                )
                for hit in group["hits"]:
                    lines.append(f"  - {hit['path']} ({hit['score']}) — {hit['title']}")
                    lines.extend(f"    {snippet}" for snippet in hit["snippets"])
        else:
            for hit in payload["hits"]:
                lines.append(f"- {hit['path']} ({hit['score']}) — {hit['title']}")
                lines.extend(f"  {snippet}" for snippet in hit["snippets"])
        budget = payload["budget"]
        lines.append(
            f"context characters: {budget['usedCharacters']}/{budget['maxCharacters']}"
        )
        return "\n".join(lines)
    if schema == "vaultctl.merge-plan/v1":
        if payload["state"] == "clean":
            return (
                f"merge plan clean: {payload['path']} "
                f"({len(payload['decisions'])} decision(s))"
            )
        lines = [
            f"merge plan conflict: {payload['path']} "
            f"({len(payload['conflicts'])} conflict(s))"
        ]
        lines.extend(
            f"- {conflict['location']}: {conflict['message']}"
            for conflict in payload["conflicts"]
        )
        return "\n".join(lines)
    if schema == "vaultctl.merge-validation/v1":
        summary = payload["summary"]
        status = "ok" if payload["valid"] else "failed"
        return (
            f"prospective merge validation {status}: {payload['path']} "
            f"({summary['errors']} error(s), {summary['warnings']} warning(s))"
        )
    if schema == "vaultctl.node-mutation-plan/v1":
        summary = payload["validation"]["summary"]
        return (
            f"node {payload['operation']} plan {payload['state']}: "
            f"{payload['path']} ({summary['errors']} error(s), "
            f"{summary['warnings']} warning(s))"
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
        if args.command == "merge":
            manifest = load_manifest(root)
            if args.merge_command == "plan":
                plan = plan_merge_files(
                    manifest,
                    path=args.path,
                    base_path=args.base,
                    ours_path=args.ours,
                    theirs_path=args.theirs,
                    base_revision=args.base_revision,
                    ours_revision=args.ours_revision,
                    theirs_revision=args.theirs_revision,
                )
                _emit(plan.to_dict(), args.format)
                return 0 if plan.state == "clean" else 1
            plan = load_merge_plan(args.plan)
            validation = validate_merge_plan(manifest, plan)
            _emit(validation.to_dict(), args.format)
            return 0 if validation.valid else 1
        if args.command == "node":
            manifest = load_manifest(root)
            if args.node_command == "plan":
                request = load_node_mutation_request(args.request)
                plan = plan_node_mutation(manifest, request)
                _emit(plan.to_dict(), args.format)
                return 0 if plan.state == "ready" else 1
            plan = load_node_mutation_plan(args.plan)
            if args.node_command == "render":
                sys.stdout.buffer.write(render_node_mutation_plan(manifest, plan))
            else:
                sys.stdout.write(diff_node_mutation_plan(manifest, plan))
            return 0

        result = scan_vault(root)
        if args.command == "scan":
            payload = _scan_payload(result)
        elif args.command == "validate":
            payload = _validation_payload(result)
        elif args.command == "search":
            hits = search_nodes(result, args.query, limit=args.limit)
            payload = _search_payload(result, query=args.query, hits=hits)
        elif args.command == "context":
            context_result = build_context(result, args.query, limit=args.limit)
            payload = _context_payload(
                result,
                query=args.query,
                context_result=context_result,
            )
        else:
            payload = _graph_payload(result)
        _emit(payload, args.format)
        if result.errors:
            return 1
        if args.command in {"search", "context"} and not payload["hits"]:
            return 1
        return 0
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
