from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

from vaultctl.errors import MarkdownError
from vaultctl.manifest import load_manifest
from vaultctl.markdown import (
    extract_body_links,
    normalize_tags,
    parse_markdown,
    parse_markdown_bytes,
)
from vaultctl.model import Edge, Node, ScanResult, ValidationIssue, VaultManifest

BUILTIN_IGNORES = (".git/**", ".vaultctl/**")
WIKILINK_VALUE_RE = re.compile(r"^!?\[\[(.+)\]\]$")
MARKDOWN_LINK_VALUE_RE = re.compile(r"^\[[^\]]+\]\((.+)\)$")


@dataclass(frozen=True)
class _PendingNode:
    node: Node
    body: str


def _is_ignored(path: str, patterns: tuple[str, ...]) -> bool:
    return any(_path_matches(path, pattern) for pattern in patterns)


def _path_matches(path: str, pattern: str) -> bool:
    """Match vault-relative paths with *, ?, and directory-aware **."""
    expression = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            expression.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            expression.append(".*")
            index += 2
        elif pattern[index] == "*":
            expression.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            expression.append("[^/]")
            index += 1
        else:
            expression.append(re.escape(pattern[index]))
            index += 1
    return re.fullmatch("".join(expression), path) is not None


def _iter_markdown_files(manifest: VaultManifest) -> tuple[Path, ...]:
    patterns = tuple(dict.fromkeys((*BUILTIN_IGNORES, *manifest.ignore)))
    paths = []
    for candidate in manifest.root.rglob("*.md"):
        relative = candidate.relative_to(manifest.root).as_posix()
        if _is_ignored(relative, patterns):
            continue
        paths.append(candidate)
    return tuple(sorted(paths, key=lambda path: path.as_posix()))


def _selector_matches(
    selector: dict[str, Any],
    *,
    path: str,
    properties: dict[str, Any],
    tags: tuple[str, ...],
) -> bool:
    checks = []
    if "path" in selector:
        checks.append(_path_matches(path, selector["path"]))
    if "type" in selector:
        checks.append(properties.get("type") == selector["type"])
    if "tag" in selector:
        checks.append(selector["tag"].lstrip("#") in tags)
    if "hasField" in selector:
        checks.append(selector["hasField"] in properties)
    return bool(checks) and all(checks)


def _classify(
    manifest: VaultManifest,
    *,
    path: str,
    properties: dict[str, Any],
    tags: tuple[str, ...],
) -> tuple[str, tuple[ValidationIssue, ...]]:
    matches = [
        kind
        for kind, contract in manifest.node_kinds.items()
        if any(
            _selector_matches(
                selector,
                path=path,
                properties=properties,
                tags=tags,
            )
            for selector in contract["selectors"]
        )
    ]
    if len(matches) == 1:
        return matches[0], ()
    if len(matches) > 1:
        joined = ", ".join(sorted(matches))
        issue = ValidationIssue(
            level="error",
            code="node.ambiguous-kind",
            message=f"node matches multiple kinds: {joined}",
            path=path,
        )
        return "__invalid__", (issue,)
    if manifest.default_kind is not None:
        return manifest.default_kind, ()
    issue = ValidationIssue(
        level="error",
        code="node.unclassified",
        message="node does not match a kind and no defaultKind is configured",
        path=path,
    )
    return "__invalid__", (issue,)


def _field_type_matches(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "list":
        return isinstance(value, list)
    return False


def _validate_fields(node: Node, manifest: VaultManifest) -> list[ValidationIssue]:
    if node.kind not in manifest.node_kinds:
        return []
    issues = []
    fields = manifest.node_kinds[node.kind].get("fields", {})
    for name, contract in fields.items():
        if name not in node.properties:
            if contract.get("required", False):
                issues.append(
                    ValidationIssue(
                        level="error",
                        code="field.required",
                        message=f"required field {name!r} is missing",
                        path=node.path,
                    )
                )
            continue
        value = node.properties[name]
        if not _field_type_matches(value, contract["type"]):
            issues.append(
                ValidationIssue(
                    level="error",
                    code="field.type",
                    message=(f"field {name!r} must have type {contract['type']}"),
                    path=node.path,
                )
            )
            continue
        if "enum" in contract and value not in contract["enum"]:
            issues.append(
                ValidationIssue(
                    level="error",
                    code="field.enum",
                    message=f"field {name!r} has a value outside its enum",
                    path=node.path,
                )
            )
    return issues


def _clean_target(
    raw: str,
    *,
    syntax_hint: str | None = None,
) -> tuple[str, str] | None:
    value = raw.strip()
    wikilink = WIKILINK_VALUE_RE.match(value)
    if syntax_hint == "wikilink" or wikilink:
        if wikilink:
            value = wikilink.group(1)
        value = re.split(r"\\?\|", value, maxsplit=1)[0].rstrip("\\")
        provenance = "wikilink"
    else:
        markdown_link = MARKDOWN_LINK_VALUE_RE.match(value)
        if syntax_hint == "markdown-link" or markdown_link:
            if markdown_link:
                value = markdown_link.group(1)
            if value.startswith("<") and ">" in value:
                value = value[1 : value.index(">")]
            else:
                titled_target = re.match(r"""^(\S+)(?:\s+["'(].*)?$""", value)
                if titled_target:
                    value = titled_target.group(1)
            provenance = "markdown-link"
        else:
            provenance = "frontmatter"

    value = value.strip().strip("<>")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        return None
    if provenance == "markdown-link" and parsed.path.startswith("/"):
        return None

    target = unquote(parsed.path).strip()
    if not target or target.startswith("#"):
        return None
    if "#" in target:
        target = target.split("#", 1)[0]
    suffix = PurePosixPath(target).suffix.lower()
    if provenance == "markdown-link" and suffix not in {"", ".md"}:
        return None
    if target.endswith(".md"):
        target = target[:-3]
    target = target.lstrip("/")
    normalized = PurePosixPath(posixpath.normpath(target)).as_posix()
    if normalized in {"", "."}:
        return None
    return normalized, provenance


def _resolve_target(
    raw: str,
    *,
    source_id: str,
    node_ids: set[str],
    basename_index: dict[str, set[str]],
    syntax_hint: str | None = None,
) -> tuple[str, str] | None:
    cleaned = _clean_target(raw, syntax_hint=syntax_hint)
    if cleaned is None:
        return None
    target, provenance = cleaned

    candidates = [target]
    parent = PurePosixPath(source_id).parent
    if parent.as_posix() != ".":
        candidates.append((parent / target).as_posix())
    for candidate in candidates:
        normalized = PurePosixPath(posixpath.normpath(candidate)).as_posix()
        if normalized in node_ids:
            return normalized, provenance

    suffix = PurePosixPath(target).suffix.lower()
    if provenance == "wikilink" and suffix not in {"", ".md"}:
        return None

    basename_matches = basename_index.get(PurePosixPath(target).name, set())
    if len(basename_matches) == 1:
        return next(iter(basename_matches)), provenance
    return target, provenance


def _relation_values(
    node: Node,
    *,
    relation: str,
    contract: dict[str, Any],
) -> tuple[tuple[str, ...], list[ValidationIssue]]:
    field = contract["field"]
    if field not in node.properties:
        return (), []
    value = node.properties[field]
    cardinality = contract["cardinality"]
    issues: list[ValidationIssue] = []

    if cardinality == "0..1":
        if isinstance(value, list):
            issues.append(
                ValidationIssue(
                    level="error",
                    code="relation.cardinality",
                    message=f"relation {relation!r} expects a scalar",
                    path=node.path,
                )
            )
            return (), issues
        values = (value,)
    else:
        if not isinstance(value, list):
            issues.append(
                ValidationIssue(
                    level="error",
                    code="relation.cardinality",
                    message=f"relation {relation!r} expects a list",
                    path=node.path,
                )
            )
            return (), issues
        values = tuple(value)

    invalid = [item for item in values if not isinstance(item, str)]
    if invalid:
        issues.append(
            ValidationIssue(
                level="error",
                code="relation.target-type",
                message=f"relation {relation!r} targets must be strings",
                path=node.path,
            )
        )
        return (), issues
    return tuple(values), issues


def _find_cycle_start(adjacency: dict[str, list[str]]) -> str | None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> bool:
        if node_id in visiting:
            return True
        if node_id in visited:
            return False
        visiting.add(node_id)
        for target in adjacency.get(node_id, ()):
            if visit(target):
                return True
        visiting.remove(node_id)
        visited.add(node_id)
        return False

    for node_id in adjacency:
        if visit(node_id):
            return node_id
    return None


def _cycle_issues(
    nodes: tuple[Node, ...],
    manifest: VaultManifest,
) -> tuple[ValidationIssue, ...]:
    issues = []
    node_paths = {node.id: node.path for node in nodes}
    for relation, contract in manifest.relations.items():
        if not contract.get("acyclic", False):
            continue
        adjacency = {
            node.id: [
                edge.target
                for edge in node.outgoing_edges
                if edge.relation == relation and edge.target in node_paths
            ]
            for node in nodes
        }
        cycle_start = _find_cycle_start(adjacency)
        if cycle_start is not None:
            issues.append(
                ValidationIssue(
                    level="error",
                    code="relation.cycle",
                    message=f"relation {relation!r} must be acyclic",
                    path=node_paths[cycle_start],
                )
            )
    return tuple(issues)


def scan_vault(
    root: Path,
    *,
    overlays: Mapping[str, bytes] | None = None,
) -> ScanResult:
    manifest = load_manifest(root)
    prospective = dict(overlays or {})
    pending: list[_PendingNode] = []
    issues: list[ValidationIssue] = []

    for path in _iter_markdown_files(manifest):
        relative = path.relative_to(root).as_posix()
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError):
            issues.append(
                ValidationIssue(
                    level="error",
                    code="path.escape",
                    message="Markdown path resolves outside the vault root",
                    path=relative,
                )
            )
            continue
        try:
            if relative in prospective:
                parsed = parse_markdown_bytes(
                    prospective.pop(relative),
                    display_path=relative,
                    fallback_stem=path.stem,
                    allow_legacy_colon_scalars=manifest.allow_legacy_colon_scalars,
                )
            else:
                parsed = parse_markdown(
                    path,
                    display_path=relative,
                    allow_legacy_colon_scalars=manifest.allow_legacy_colon_scalars,
                )
        except MarkdownError as exc:
            issues.append(
                ValidationIssue(
                    level="error",
                    code="markdown.parse",
                    message=str(exc),
                    path=relative,
                )
            )
            continue

        tags = normalize_tags(parsed.properties.get("tags"), parsed.body)
        kind, classification_issues = _classify(
            manifest,
            path=relative,
            properties=parsed.properties,
            tags=tags,
        )
        issues.extend(classification_issues)
        node = Node(
            id=relative[:-3],
            path=relative,
            kind=kind,
            title=parsed.title,
            properties=parsed.properties,
            tags=tags,
            source_hash=parsed.source_hash,
            body=parsed.body,
            headings=parsed.headings,
        )
        issues.extend(_validate_fields(node, manifest))
        pending.append(_PendingNode(node=node, body=parsed.body))

    if prospective:
        joined = ", ".join(sorted(prospective))
        raise MarkdownError(
            "prospective overlays must target existing, included Markdown paths: "
            f"{joined}"
        )

    node_ids = {item.node.id for item in pending}
    basename_index: dict[str, set[str]] = {}
    for node_id in node_ids:
        basename_index.setdefault(PurePosixPath(node_id).name, set()).add(node_id)

    completed = []
    for item in pending:
        edges: list[Edge] = []
        for relation, contract in manifest.relations.items():
            values, relation_issues = _relation_values(
                item.node,
                relation=relation,
                contract=contract,
            )
            issues.extend(relation_issues)
            for raw_target in values:
                resolved = _resolve_target(
                    raw_target,
                    source_id=item.node.id,
                    node_ids=node_ids,
                    basename_index=basename_index,
                )
                if resolved is None:
                    continue
                target, source_syntax = resolved
                edges.append(
                    Edge(
                        source=item.node.id,
                        relation=relation,
                        target=target,
                        provenance=f"frontmatter:{contract['field']}:{source_syntax}",
                        source_location=f"frontmatter.{contract['field']}",
                    )
                )
                if target not in node_ids:
                    issues.append(
                        ValidationIssue(
                            level="error",
                            code="relation.unresolved",
                            message=(
                                f"relation {relation!r} targets missing node {target!r}"
                            ),
                            path=item.node.path,
                        )
                    )
                    continue
                target_kind = next(
                    pending_item.node.kind
                    for pending_item in pending
                    if pending_item.node.id == target
                )
                if target_kind not in contract["targetKinds"]:
                    issues.append(
                        ValidationIssue(
                            level="error",
                            code="relation.target-kind",
                            message=(
                                f"relation {relation!r} cannot target kind "
                                f"{target_kind!r}"
                            ),
                            path=item.node.path,
                        )
                    )

        for raw_target, syntax in extract_body_links(item.body):
            resolved = _resolve_target(
                raw_target,
                source_id=item.node.id,
                node_ids=node_ids,
                basename_index=basename_index,
                syntax_hint=syntax,
            )
            if resolved is None:
                continue
            target, _ = resolved
            edges.append(
                Edge(
                    source=item.node.id,
                    relation="link",
                    target=target,
                    provenance=syntax,
                )
            )
            if target not in node_ids:
                issues.append(
                    ValidationIssue(
                        level="warning",
                        code="link.unresolved",
                        message=f"link targets missing node {target!r}",
                        path=item.node.path,
                    )
                )

        completed.append(
            replace(
                item.node,
                outgoing_edges=tuple(
                    sorted(
                        edges,
                        key=lambda edge: (
                            edge.relation,
                            edge.target,
                            edge.provenance,
                        ),
                    )
                ),
            )
        )

    nodes = tuple(sorted(completed, key=lambda node: node.id))
    issues.extend(_cycle_issues(nodes, manifest))
    return ScanResult(
        manifest=manifest,
        nodes=nodes,
        issues=tuple(
            sorted(
                issues,
                key=lambda issue: (
                    issue.path or "",
                    issue.level,
                    issue.code,
                    issue.message,
                ),
            )
        ),
    )
