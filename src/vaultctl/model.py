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
    properties: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.node_id,
            "path": self.path,
            "kind": self.kind,
            "title": self.title,
            "score": self.score,
            "matchedZones": list(self.matched_zones),
            "snippets": list(self.snippets),
            "properties": self.properties,
        }


@dataclass(frozen=True)
class ContextGroup:
    key: str
    score: int
    count: int
    representative: str
    top_match: str | None
    hits: tuple[SearchHit, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "score": self.score,
            "count": self.count,
            "representative": self.representative,
            "topMatch": self.top_match,
            "hits": [hit.to_dict() for hit in self.hits],
        }


@dataclass(frozen=True)
class ContextResult:
    hits: tuple[SearchHit, ...]
    groups: tuple[ContextGroup, ...]
    max_characters: int
    used_characters: int
    truncated: bool


@dataclass(frozen=True)
class MergeInput:
    revision: str
    source_hash: str

    def to_dict(self) -> dict[str, str]:
        return {
            "revision": self.revision,
            "sourceHash": self.source_hash,
        }


@dataclass(frozen=True)
class Conflict:
    id: str
    kind: str
    path: str
    location: str
    strategy: str
    message: str
    base: dict[str, Any]
    ours: dict[str, Any]
    theirs: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "path": self.path,
            "location": self.location,
            "strategy": self.strategy,
            "message": self.message,
            "base": self.base,
            "ours": self.ours,
            "theirs": self.theirs,
        }


@dataclass(frozen=True)
class MergeDecision:
    location: str
    strategy: str
    resolution: str
    candidate: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "location": self.location,
            "strategy": self.strategy,
            "resolution": self.resolution,
            "candidate": self.candidate,
        }


@dataclass(frozen=True)
class MergeCandidate:
    properties: dict[str, Any]
    body: str
    content_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "properties": self.properties,
            "body": self.body,
            "contentHash": self.content_hash,
        }


@dataclass(frozen=True)
class MergePlan:
    schema_version: str
    plan_id: str
    vault_id: str
    path: str
    engine_version: str
    manifest_digest: str
    inputs: dict[str, MergeInput]
    state: str
    decisions: tuple[MergeDecision, ...]
    conflicts: tuple[Conflict, ...]
    candidate: MergeCandidate | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "planId": self.plan_id,
            "vaultId": self.vault_id,
            "path": self.path,
            "engineVersion": self.engine_version,
            "manifestDigest": self.manifest_digest,
            "inputs": {
                name: merge_input.to_dict()
                for name, merge_input in sorted(self.inputs.items())
            },
            "state": self.state,
            "decisions": [decision.to_dict() for decision in self.decisions],
            "conflicts": [conflict.to_dict() for conflict in self.conflicts],
            "candidate": self.candidate.to_dict() if self.candidate else None,
        }


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
    plan_id: str
    plan_digest: str
    input_revisions: dict[str, str]
    manifest_digest: str
    engine_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "vaultId": self.vault_id,
            "operationId": self.operation_id,
            "paths": list(self.paths),
            "beforeHashes": self.before_hashes,
            "afterHashes": self.after_hashes,
            "state": self.state,
            "planId": self.plan_id,
            "planDigest": self.plan_digest,
            "inputRevisions": self.input_revisions,
            "manifestDigest": self.manifest_digest,
            "engineVersion": self.engine_version,
        }
