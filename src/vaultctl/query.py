from __future__ import annotations

from typing import Any

from vaultctl.errors import QueryError
from vaultctl.model import Node, ScanResult


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def query_nodes(
    result: ScanResult,
    *,
    kinds: tuple[str, ...] = (),
    tags: tuple[str, ...] = (),
    has_fields: tuple[str, ...] = (),
    properties: tuple[tuple[str, Any], ...] = (),
    without_incoming: bool = False,
    limit: int | None = None,
) -> tuple[Node, ...]:
    """Return a stable, read-only projection of nodes matching exact filters."""

    selected_kinds = _unique(tuple(kind.strip() for kind in kinds))
    if any(not kind for kind in selected_kinds):
        raise QueryError("query node kinds must not be empty")
    unknown_kinds = sorted(set(selected_kinds) - set(result.manifest.node_kinds))
    if unknown_kinds:
        joined = ", ".join(unknown_kinds)
        raise QueryError(f"query references unknown node kind(s): {joined}")

    selected_tags = _unique(tuple(tag.strip().lstrip("#") for tag in tags))
    if any(not tag for tag in selected_tags):
        raise QueryError("query tags must not be empty")

    selected_fields = _unique(tuple(field.strip() for field in has_fields))
    if any(not field for field in selected_fields):
        raise QueryError("query field names must not be empty")

    selected_properties = tuple((name.strip(), value) for name, value in properties)
    property_names = [name for name, _ in selected_properties]
    if any(not name for name in property_names):
        raise QueryError("query property names must not be empty")
    duplicates = sorted(
        name for name in set(property_names) if property_names.count(name) > 1
    )
    if duplicates:
        joined = ", ".join(duplicates)
        raise QueryError(f"query repeats property filter(s): {joined}")

    if limit is not None and limit <= 0:
        raise QueryError("query limit must be greater than zero")

    incoming_targets = {edge.target for edge in result.edges}
    matches = []
    for node in result.nodes:
        if selected_kinds and node.kind not in selected_kinds:
            continue
        if selected_tags and not set(selected_tags).issubset(node.tags):
            continue
        if selected_fields and not all(
            field in node.properties for field in selected_fields
        ):
            continue
        if selected_properties and not all(
            name in node.properties and node.properties[name] == expected
            for name, expected in selected_properties
        ):
            continue
        if without_incoming and node.id in incoming_targets:
            continue
        matches.append(node)

    if limit is not None:
        matches = matches[:limit]
    return tuple(matches)
