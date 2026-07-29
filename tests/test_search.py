from __future__ import annotations

import pytest

from vaultctl.engine import scan_vault
from vaultctl.errors import QueryError
from vaultctl.search import context, search


def _search_vault(make_vault):
    return make_vault(
        manifest_overrides={
            "search": {
                "defaultLimit": 2,
                "maxLimit": 3,
                "stopWords": ["when"],
                "zones": [
                    {
                        "source": "stem",
                        "weight": 20,
                        "phraseWeight": 20,
                    },
                    {
                        "source": "property",
                        "field": "description",
                        "weight": 8,
                        "phraseWeight": 16,
                    },
                    {
                        "source": "body",
                        "weight": 1,
                        "phraseWeight": 2,
                        "countCap": 2,
                    },
                ],
            },
            "context": {
                "defaultLimit": 2,
                "maxLimit": 2,
                "maxCharacters": 256,
                "snippetLines": 1,
                "snippetCharacters": 220,
            },
        },
        notes={
            "notes/route-plan.md": (
                "---\n"
                "description: General guide\n"
                "tags: []\n"
                "related: []\n"
                "---\n"
                "# Overview\n\n" + "Route plan details " * 20 + "\n"
            ),
            "notes/guide.md": (
                "---\n"
                "description: Route plan reference\n"
                "tags: []\n"
                "related: []\n"
                "---\n"
                "# Guide\n\n"
                "Use when comparing a route plan.\n"
            ),
            "notes/unrelated.md": ("---\ntags: []\nrelated: []\n---\n# Unrelated\n"),
        },
    )


def test_search_uses_manifest_zones_and_stable_ranking(make_vault) -> None:
    result = scan_vault(_search_vault(make_vault))

    hits = search(result, "route plan")

    assert [hit.path for hit in hits] == [
        "notes/route-plan.md",
        "notes/guide.md",
    ]
    assert hits[0].score > hits[1].score
    assert hits[0].matched_zones == ("stem", "body")
    assert hits[1].matched_zones == ("property:description", "body")


def test_search_limit_is_bounded_by_manifest(make_vault) -> None:
    result = scan_vault(_search_vault(make_vault))

    with pytest.raises(QueryError, match="exceeds manifest maxLimit"):
        search(result, "route", limit=4)


def test_context_honors_character_and_snippet_budgets(make_vault) -> None:
    result = scan_vault(_search_vault(make_vault))

    selected = context(result, "route plan")

    assert len(selected.hits) == 1
    assert len(selected.hits[0].snippets) == 1
    assert len(selected.hits[0].snippets[0]) == 220
    assert selected.used_characters <= selected.max_characters == 256
    assert selected.truncated is True


def test_context_limit_is_independent_from_search_output_limit(make_vault) -> None:
    root = make_vault(
        manifest_overrides={
            "search": {
                "defaultLimit": 1,
                "maxLimit": 1,
            },
            "context": {
                "defaultLimit": 2,
                "maxLimit": 2,
            },
        },
        notes={
            "notes/one.md": ("---\ntags: []\nrelated: []\n---\n# One\nShared query.\n"),
            "notes/two.md": ("---\ntags: []\nrelated: []\n---\n# Two\nShared query.\n"),
        },
    )
    result = scan_vault(root)

    assert len(search(result, "shared")) == 1
    assert len(context(result, "shared").hits) == 2


def test_empty_query_is_a_user_error(make_vault) -> None:
    result = scan_vault(_search_vault(make_vault))

    with pytest.raises(QueryError, match="query is empty"):
        search(result, "  ")


def test_stopword_only_query_has_no_search_or_context_hits(make_vault) -> None:
    result = scan_vault(_search_vault(make_vault))

    assert search(result, "when") == ()
    assert context(result, "when").hits == ()


def test_context_can_disable_title_fallback(make_vault) -> None:
    root = make_vault(
        manifest_overrides={
            "search": {
                "zones": [
                    {
                        "source": "property",
                        "field": "description",
                        "weight": 10,
                    }
                ]
            },
            "context": {
                "fallbackToTitle": False,
            },
        },
        notes={
            "notes/example.md": (
                "---\n"
                "description: routing\n"
                "tags: []\n"
                "related: []\n"
                "---\n"
                "# Example\n\n"
                "No matching body line.\n"
            )
        },
    )
    result = scan_vault(root)

    assert context(result, "routing").hits[0].snippets == ()


def test_context_groups_by_fields_ticket_paths_and_freshness(make_vault) -> None:
    root = make_vault(
        manifest_overrides={
            "search": {
                "zones": [
                    {
                        "source": "body",
                        "weight": 1,
                        "countCap": 6,
                    }
                ]
            },
            "context": {
                "fallbackToTitle": False,
                "outputFields": ["status", "updated"],
                "grouping": {
                    "fields": ["topic", "jira"],
                    "pathToken": "ticket",
                    "keyCase": "upper",
                    "statusField": "status",
                    "inactiveStatuses": ["archived", "superseded"],
                    "freshnessFields": ["updated", "created"],
                    "notesPerGroup": 1,
                },
            },
        },
        notes={
            "notes/abc-1-current.md": (
                "---\n"
                "topic: abc-1\n"
                "status: active\n"
                "updated: 2026-01-01\n"
                "tags: []\n"
                "related: []\n"
                "---\n"
                "# Current\n\n"
                "query\n"
            ),
            "notes/ABC-1-history.md": (
                "---\n"
                "topic: ABC-1\n"
                "status: archived\n"
                "updated: 2026-03-01\n"
                "tags: []\n"
                "related: []\n"
                "---\n"
                "# History\n\n"
                "query query query query query query\n"
            ),
            "notes/abc-22-follow-up.md": (
                "---\n"
                "status: active\n"
                "updated: 2026-02-01\n"
                "tags: []\n"
                "related: []\n"
                "---\n"
                "# Ticket path\n\n"
                "query\n"
            ),
            "notes/misc.md": (
                "---\n"
                "status: active\n"
                "updated: 2026-02-01\n"
                "tags: []\n"
                "related: []\n"
                "---\n"
                "# Misc\n\n"
                "query\n"
            ),
        },
    )
    result = scan_vault(root)

    selected = context(result, "query")

    assert [group.key for group in selected.groups] == [
        "ABC-1",
        "ABC-22",
        "notes/misc.md",
    ]
    first = selected.groups[0]
    assert first.count == 2
    assert first.representative == "notes/abc-1-current.md"
    assert first.top_match == "notes/ABC-1-history.md"
    assert [hit.path for hit in first.hits] == ["notes/abc-1-current.md"]
    assert first.hits[0].properties == {
        "status": "active",
        "updated": "2026-01-01",
    }
    assert selected.hits == tuple(
        hit for group in selected.groups for hit in group.hits
    )


def test_context_keeps_single_digit_ticket_paths_in_separate_groups(
    make_vault,
) -> None:
    root = make_vault(
        manifest_overrides={
            "search": {
                "zones": [
                    {
                        "source": "body",
                        "weight": 1,
                        "countCap": 1,
                    }
                ]
            },
            "context": {
                "fallbackToTitle": False,
                "grouping": {
                    "pathToken": "ticket",
                    "keyCase": "upper",
                },
            },
        },
        notes={
            "notes/abc-1-one.md": "# One\n\nquery\n",
            "notes/abc-1-two.md": "# Two\n\nquery\n",
        },
    )
    result = scan_vault(root)

    selected = context(result, "query")

    assert [group.key for group in selected.groups] == [
        "notes/abc-1-one.md",
        "notes/abc-1-two.md",
    ]
    assert [group.count for group in selected.groups] == [1, 1]
