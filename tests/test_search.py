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
