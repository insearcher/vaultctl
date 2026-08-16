from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import PurePosixPath
from typing import Any

from vaultctl.errors import QueryError
from vaultctl.model import (
    ContextGroup,
    ContextResult,
    Node,
    ScanResult,
    SearchHit,
    VaultManifest,
)

TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
TICKET_RE = re.compile(
    r"\b(adhoc-\d{4}-\d{2}-\d{2}-[A-Za-z0-9._-]+|"
    r"[A-Za-z]{2,}-\d{2,})\b"
)
DEFAULT_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "for",
        "in",
        "of",
        "or",
        "the",
        "to",
        "в",
        "и",
        "на",
        "по",
        "для",
    }
)
DEFAULT_ZONES: tuple[dict[str, Any], ...] = (
    {"source": "stem", "weight": 12, "phraseWeight": 20, "countCap": 1},
    {"source": "title", "weight": 9, "phraseWeight": 25, "countCap": 1},
    {"source": "path", "weight": 8, "phraseWeight": 15, "countCap": 1},
    {"source": "tags", "weight": 7, "phraseWeight": 0, "countCap": 1},
    {"source": "properties", "weight": 4, "phraseWeight": 0, "countCap": 1},
    {"source": "headings", "weight": 4, "phraseWeight": 0, "countCap": 1},
    {"source": "body", "weight": 1, "phraseWeight": 8, "countCap": 6},
)
DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 100
DEFAULT_CONTEXT_LIMIT = 8
MAX_CONTEXT_LIMIT = 20
DEFAULT_CONTEXT_CHARACTERS = 12000
DEFAULT_SNIPPET_LINES = 2
DEFAULT_SNIPPET_CHARACTERS = 220


@dataclass(frozen=True)
class _RankedGroup:
    key: str
    score: int
    count: int
    representative: str
    top_match: str | None
    hits: tuple[SearchHit, ...]


def search_config(manifest: VaultManifest) -> dict[str, Any]:
    return manifest.raw.get("search", {})


def context_config(manifest: VaultManifest) -> dict[str, Any]:
    return manifest.raw.get("context", {})


def tokenize(query: str, *, stop_words: frozenset[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            token.lower()
            for token in TOKEN_RE.findall(query)
            if len(token) > 1 and token.lower() not in stop_words
        )
    )


def _plain_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def zone_name(zone: dict[str, Any]) -> str:
    if zone["source"] == "property":
        return f"property:{zone['field']}"
    return zone["source"]


def zone_text(node: Node, zone: dict[str, Any]) -> str:
    source = zone["source"]
    if source == "title":
        return node.title
    if source == "firstHeading":
        if node.headings:
            return node.headings[0]
        return PurePosixPath(node.path).stem.replace("-", " ").replace("_", " ")
    if source == "stem":
        return PurePosixPath(node.path).stem
    if source == "path":
        return node.path
    if source == "tags":
        return " ".join(node.tags)
    if source == "property":
        return _plain_text(node.properties.get(zone["field"]))
    if source == "properties":
        return " ".join(
            f"{key} {_plain_text(value)}"
            for key, value in sorted(node.properties.items())
        )
    if source == "headings":
        return " ".join(node.headings)
    return node.body


def _boost_matches(node: Node, boost: dict[str, Any]) -> bool:
    kind = boost.get("kind")
    if kind is not None:
        return node.kind == kind
    return PurePosixPath(node.path).match(boost["path"])


def resolved_zones(manifest: VaultManifest) -> tuple[dict[str, Any], ...]:
    configured = search_config(manifest).get("zones")
    return tuple(configured) if configured else DEFAULT_ZONES


def stop_words(manifest: VaultManifest) -> frozenset[str]:
    config = search_config(manifest)
    defaults = (
        DEFAULT_STOP_WORDS if config.get("useDefaultStopWords", True) else frozenset()
    )
    return defaults | frozenset(word.lower() for word in config.get("stopWords", ()))


def resolve_limit(
    *,
    requested: int | None,
    config: dict[str, Any],
    default: int,
    maximum: int,
    command: str,
) -> int:
    max_limit = config.get("maxLimit", maximum)
    limit = requested if requested is not None else config.get("defaultLimit", default)
    if limit <= 0:
        raise QueryError(f"{command} limit must be positive")
    if limit > max_limit:
        raise QueryError(
            f"{command} limit {limit} exceeds manifest maxLimit {max_limit}"
        )
    return limit


STEM_MATCH_WEIGHT = 0.8


def score_node(
    node: Node,
    *,
    phrase: str,
    tokens: tuple[str, ...],
    zones: tuple[dict[str, Any], ...],
    boosts: tuple[dict[str, Any], ...],
    stem_counts: Callable[[int, str], int] | None = None,
) -> SearchHit | None:
    """Score one node with the exact zone scorer.

    ``stem_counts`` optionally maps ``(zone_index, token)`` to the number of
    stem-equivalent occurrences; a token without an exact substring match in a
    zone then still contributes ``STEM_MATCH_WEIGHT`` of the zone weight.
    Without ``stem_counts`` the score stays an exact integer.
    """
    score: float = 0
    matched = []
    for zone_index, zone in enumerate(zones):
        text = zone_text(node, zone).lower()
        count_cap = zone.get("countCap", 1)
        zone_score: float = 0
        for token in tokens:
            count = min(text.count(token), count_cap)
            if count:
                zone_score += count * zone["weight"]
            elif stem_counts is not None:
                stem_count = min(stem_counts(zone_index, token), count_cap)
                zone_score += stem_count * zone["weight"] * STEM_MATCH_WEIGHT
        if tokens and phrase and phrase in text:
            zone_score += zone.get("phraseWeight", 0)
        if zone_score:
            score += zone_score
            matched.append(zone_name(zone))
    if score == 0:
        return None
    score += sum(boost["weight"] for boost in boosts if _boost_matches(node, boost))
    return SearchHit(
        node_id=node.id,
        path=node.path,
        kind=node.kind,
        title=node.title,
        score=score,
        matched_zones=tuple(matched),
    )


def _rank(result: ScanResult, query: str) -> tuple[SearchHit, ...]:
    phrase = query.strip().lower()
    tokens = tokenize(query, stop_words=stop_words(result.manifest))
    hits = [
        hit
        for node in result.nodes
        if (
            hit := score_node(
                node,
                phrase=phrase,
                tokens=tokens,
                zones=resolved_zones(result.manifest),
                boosts=tuple(search_config(result.manifest).get("boosts", ())),
            )
        )
        is not None
    ]
    hits.sort(key=lambda hit: (-hit.score, hit.path))
    return tuple(hits)


def search(
    result: ScanResult,
    query: str,
    *,
    limit: int | None = None,
) -> tuple[SearchHit, ...]:
    if not query.strip():
        raise QueryError("search query is empty")
    config = search_config(result.manifest)
    resolved_limit = resolve_limit(
        requested=limit,
        config=config,
        default=DEFAULT_SEARCH_LIMIT,
        maximum=MAX_SEARCH_LIMIT,
        command="search",
    )
    return _rank(result, query)[:resolved_limit]


def _snippets(
    node: Node,
    *,
    tokens: tuple[str, ...],
    max_lines: int,
    max_characters: int,
    fallback_to_title: bool,
) -> tuple[str, ...]:
    if max_lines == 0:
        return ()
    snippets = []
    for raw_line in node.body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.lower()
        if tokens and not any(token in lowered for token in tokens):
            continue
        snippets.append(line[:max_characters])
        if len(snippets) >= max_lines:
            break
    if not snippets and fallback_to_title and node.title:
        snippets.append(node.title[:max_characters])
    return tuple(snippets)


def _normalize_group_key(value: str, config: dict[str, Any]) -> str:
    key_case = config.get("keyCase", "preserve")
    if key_case == "upper":
        return value.upper()
    if key_case == "lower":
        return value.lower()
    return value


def _group_key(node: Node, config: dict[str, Any]) -> str:
    for field in config.get("fields", ()):
        value = node.properties.get(field)
        if isinstance(value, str) and value.strip():
            return _normalize_group_key(value.strip(), config)
    if config.get("pathToken") == "ticket":
        for segment in PurePosixPath(node.path).parts:
            match = TICKET_RE.search(segment)
            if match is not None:
                return _normalize_group_key(match.group(1), config)
    return node.path


def _freshness(node: Node, config: dict[str, Any]) -> str:
    for field in config.get("freshnessFields", ("updated", "created")):
        value = node.properties.get(field)
        if isinstance(value, str) and value:
            try:
                date.fromisoformat(value)
            except ValueError:
                continue
            return value
    return ""


def _group_hits(
    hits: tuple[SearchHit, ...],
    *,
    nodes: dict[str, Node],
    config: dict[str, Any],
) -> tuple[_RankedGroup, ...]:
    grouped: dict[str, list[SearchHit]] = defaultdict(list)
    for hit in hits:
        grouped[_group_key(nodes[hit.node_id], config)].append(hit)

    status_field = config.get("statusField", "status")
    inactive = set(config.get("inactiveStatuses", ("archived", "superseded")))
    notes_per_group = config.get("notesPerGroup", 2)
    ranked = []
    for key, members in grouped.items():
        best = min(members, key=lambda hit: (-hit.score, hit.path))
        ordered = sorted(members, key=lambda hit: hit.path)
        ordered.sort(key=lambda hit: hit.score, reverse=True)
        ordered.sort(
            key=lambda hit: _freshness(nodes[hit.node_id], config), reverse=True
        )
        ordered.sort(
            key=lambda hit: nodes[hit.node_id].properties.get(status_field) in inactive
        )
        representative = ordered[0].path
        ranked.append(
            _RankedGroup(
                key=key,
                score=best.score,
                count=len(ordered),
                representative=representative,
                top_match=best.path if best.path != representative else None,
                hits=tuple(ordered[:notes_per_group]),
            )
        )
    ranked.sort(key=lambda group: (-group.score, group.key))
    return tuple(ranked)


def _context_hit(
    hit: SearchHit,
    *,
    node: Node,
    tokens: tuple[str, ...],
    snippet_lines: int,
    snippet_characters: int,
    fallback_to_title: bool,
    output_fields: tuple[str, ...],
) -> SearchHit:
    return SearchHit(
        node_id=hit.node_id,
        path=hit.path,
        kind=hit.kind,
        title=hit.title,
        score=hit.score,
        matched_zones=hit.matched_zones,
        snippets=_snippets(
            node,
            tokens=tokens,
            max_lines=snippet_lines,
            max_characters=snippet_characters,
            fallback_to_title=fallback_to_title,
        ),
        properties={
            field: node.properties[field]
            for field in output_fields
            if field in node.properties
        },
    )


def _hit_cost(hit: SearchHit) -> int:
    return len(hit.path) + len(hit.title) + sum(len(item) for item in hit.snippets)


def build_context_result(
    manifest: VaultManifest,
    query: str,
    *,
    ranked_hits: tuple[SearchHit, ...],
    nodes: dict[str, Node],
    limit: int | None = None,
) -> ContextResult:
    """Build a context result from already-ranked hits and their nodes."""
    phrase = query.strip()
    if not phrase:
        raise QueryError("context query is empty")
    config = context_config(manifest)
    resolved_limit = resolve_limit(
        requested=limit,
        config=config,
        default=DEFAULT_CONTEXT_LIMIT,
        maximum=MAX_CONTEXT_LIMIT,
        command="context",
    )
    max_characters = config.get("maxCharacters", DEFAULT_CONTEXT_CHARACTERS)
    snippet_lines = config.get("snippetLines", DEFAULT_SNIPPET_LINES)
    snippet_characters = config.get("snippetCharacters", DEFAULT_SNIPPET_CHARACTERS)
    fallback_to_title = config.get("fallbackToTitle", True)
    output_fields = tuple(config.get("outputFields", ()))
    tokens = tokenize(query, stop_words=stop_words(manifest))
    used = 0
    truncated = False
    selected: list[SearchHit] = []
    selected_groups: list[ContextGroup] = []
    grouping = config.get("grouping")

    if grouping:
        ranked_groups = _group_hits(ranked_hits, nodes=nodes, config=grouping)[
            :resolved_limit
        ]
        for group in ranked_groups:
            group_hits = []
            for hit in group.hits:
                rendered = _context_hit(
                    hit,
                    node=nodes[hit.node_id],
                    tokens=tokens,
                    snippet_lines=snippet_lines,
                    snippet_characters=snippet_characters,
                    fallback_to_title=fallback_to_title,
                    output_fields=output_fields,
                )
                cost = _hit_cost(rendered)
                if used + cost > max_characters:
                    truncated = True
                    break
                group_hits.append(rendered)
                selected.append(rendered)
                used += cost
            if not group_hits:
                break
            selected_groups.append(
                ContextGroup(
                    key=group.key,
                    score=group.score,
                    count=group.count,
                    representative=group.representative,
                    top_match=group.top_match,
                    hits=tuple(group_hits),
                )
            )
            if truncated:
                break
    else:
        search_hits = ranked_hits[:resolved_limit]
        for hit in search_hits:
            rendered = _context_hit(
                hit,
                node=nodes[hit.node_id],
                tokens=tokens,
                snippet_lines=snippet_lines,
                snippet_characters=snippet_characters,
                fallback_to_title=fallback_to_title,
                output_fields=output_fields,
            )
            cost = _hit_cost(rendered)
            if used + cost > max_characters:
                truncated = True
                break
            selected.append(rendered)
            used += cost
        if len(selected) < len(search_hits):
            truncated = True

    return ContextResult(
        hits=tuple(selected),
        groups=tuple(selected_groups),
        max_characters=max_characters,
        used_characters=used,
        truncated=truncated,
    )


def context(
    result: ScanResult,
    query: str,
    *,
    limit: int | None = None,
) -> ContextResult:
    return build_context_result(
        result.manifest,
        query,
        ranked_hits=_rank(result, query),
        nodes={node.id: node for node in result.nodes},
        limit=limit,
    )
