from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Any

from vaultctl.errors import QueryError
from vaultctl.model import ContextResult, Node, ScanResult, SearchHit

TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
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


def _search_config(result: ScanResult) -> dict[str, Any]:
    return result.manifest.raw.get("search", {})


def _context_config(result: ScanResult) -> dict[str, Any]:
    return result.manifest.raw.get("context", {})


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


def _zone_name(zone: dict[str, Any]) -> str:
    if zone["source"] == "property":
        return f"property:{zone['field']}"
    return zone["source"]


def _zone_text(node: Node, zone: dict[str, Any]) -> str:
    source = zone["source"]
    if source == "title":
        return node.title
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


def _resolved_zones(result: ScanResult) -> tuple[dict[str, Any], ...]:
    configured = _search_config(result).get("zones")
    return tuple(configured) if configured else DEFAULT_ZONES


def _stop_words(result: ScanResult) -> frozenset[str]:
    configured = _search_config(result).get("stopWords", ())
    return DEFAULT_STOP_WORDS | frozenset(word.lower() for word in configured)


def _resolve_limit(
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


def _score_node(
    node: Node,
    *,
    phrase: str,
    tokens: tuple[str, ...],
    zones: tuple[dict[str, Any], ...],
) -> SearchHit | None:
    score = 0
    matched = []
    for zone in zones:
        text = _zone_text(node, zone).lower()
        zone_score = 0
        for token in tokens:
            count = min(text.count(token), zone.get("countCap", 1))
            zone_score += count * zone["weight"]
        if tokens and phrase and phrase in text:
            zone_score += zone.get("phraseWeight", 0)
        if zone_score:
            score += zone_score
            matched.append(_zone_name(zone))
    if score == 0:
        return None
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
    tokens = tokenize(query, stop_words=_stop_words(result))
    hits = [
        hit
        for node in result.nodes
        if (
            hit := _score_node(
                node,
                phrase=phrase,
                tokens=tokens,
                zones=_resolved_zones(result),
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
    config = _search_config(result)
    resolved_limit = _resolve_limit(
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


def context(
    result: ScanResult,
    query: str,
    *,
    limit: int | None = None,
) -> ContextResult:
    phrase = query.strip()
    if not phrase:
        raise QueryError("context query is empty")
    config = _context_config(result)
    resolved_limit = _resolve_limit(
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
    tokens = tokenize(query, stop_words=_stop_words(result))
    search_hits = _rank(result, query)[:resolved_limit]
    nodes = {node.id: node for node in result.nodes}
    selected = []
    used = 0
    truncated = False
    for hit in search_hits:
        node = nodes[hit.node_id]
        snippets = _snippets(
            node,
            tokens=tokens,
            max_lines=snippet_lines,
            max_characters=snippet_characters,
            fallback_to_title=fallback_to_title,
        )
        cost = len(hit.path) + len(hit.title) + sum(len(item) for item in snippets)
        if used + cost > max_characters:
            truncated = True
            break
        selected.append(
            SearchHit(
                node_id=hit.node_id,
                path=hit.path,
                kind=hit.kind,
                title=hit.title,
                score=hit.score,
                matched_zones=hit.matched_zones,
                snippets=snippets,
            )
        )
        used += cost
    if len(selected) < len(search_hits):
        truncated = True
    return ContextResult(
        hits=tuple(selected),
        max_characters=max_characters,
        used_characters=used,
        truncated=truncated,
    )
