from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Edge:
    source: str
    relation: str
    target: str
    provenance: str
    source_location: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "source": self.source,
            "relation": self.relation,
            "target": self.target,
            "provenance": self.provenance,
        }
        if self.source_location is not None:
            data["sourceLocation"] = self.source_location
        return data


@dataclass(frozen=True)
class Node:
    id: str
    path: str
    kind: str
    title: str
    properties: dict[str, Any]
    tags: tuple[str, ...]
    source_hash: str
    body: str = field(default="", repr=False)
    headings: tuple[str, ...] = field(default=(), repr=False)
    outgoing_edges: tuple[Edge, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "kind": self.kind,
            "title": self.title,
            "properties": self.properties,
            "tags": list(self.tags),
            "sourceHash": self.source_hash,
            "outgoingEdges": [edge.to_dict() for edge in self.outgoing_edges],
        }


@dataclass(frozen=True)
class VaultManifest:
    api_version: str
    vault_id: str
    root: Path
    node_kinds: dict[str, dict[str, Any]]
    relations: dict[str, dict[str, Any]]
    default_kind: str | None = None
    ignore: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    allow_legacy_colon_scalars: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class ValidationIssue:
    level: str
    code: str
    message: str
    path: str | None = None

    def to_dict(self) -> dict[str, str]:
        data = {
            "level": self.level,
            "code": self.code,
            "message": self.message,
        }
        if self.path is not None:
            data["path"] = self.path
        return data


@dataclass(frozen=True)
class ScanResult:
    manifest: VaultManifest
    nodes: tuple[Node, ...]
    issues: tuple[ValidationIssue, ...]

    @property
    def edges(self) -> tuple[Edge, ...]:
        return tuple(edge for node in self.nodes for edge in node.outgoing_edges)

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.level == "error")

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.level == "warning")


@dataclass(frozen=True)
class SearchHit:
    node_id: str
    path: str
    kind: str
    title: str
    score: int
    matched_zones: tuple[str, ...]
    snippets: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.node_id,
            "path": self.path,
            "kind": self.kind,
            "title": self.title,
            "score": self.score,
            "matchedZones": list(self.matched_zones),
            "snippets": list(self.snippets),
        }


@dataclass(frozen=True)
class ContextResult:
    hits: tuple[SearchHit, ...]
    max_characters: int
    used_characters: int
    truncated: bool


@dataclass(frozen=True)
class MutationPlan:
    """Versioned future write contract; no apply behavior exists yet."""

    schema_version: str
    vault_id: str
    operation: str
    expected_hashes: dict[str, str]
    paths: tuple[str, ...]


@dataclass(frozen=True)
class Receipt:
    """Versioned future mutation receipt; no write behavior exists yet."""

    schema_version: str
    vault_id: str
    operation_id: str
    paths: tuple[str, ...]
    before_hashes: dict[str, str]
    after_hashes: dict[str, str]
    state: str
