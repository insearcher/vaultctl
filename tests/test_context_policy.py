from __future__ import annotations

import json
import os
import shutil
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

import vaultctl.cli
import vaultctl.index
import vaultctl.search
from vaultctl.cli import main
from vaultctl.engine import scan_vault
from vaultctl.errors import ManifestError
from vaultctl.manifest import load_manifest
from vaultctl.model import SearchHit
from vaultctl.search import apply_freshness, context, context_properties_text

NOTE = "---\ntags: []\nrelated: []\n---\n"


def _payload(root: Path, capsys, *args: str) -> dict:
    assert main(["--vault", str(root), *args]) == 0
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize(
    ("properties", "expected"),
    [
        ({}, 100),
        ({"updated": "invalid", "created": 17}, 100),
        ({"updated": "2026-01-01", "created": "2025-01-01"}, 115),
        ({"updated": "invalid", "created": "2026-01-01"}, 115),
        ({"updated": "2027-01-01"}, 115),
        ({"updated": "2025-10-03"}, 107.5),
        ({"updated": "2026-01-01T12:30:00+00:00"}, 115),
    ],
)
def test_freshness_uses_only_content_dates(make_vault, properties, expected) -> None:
    root = make_vault(notes={"notes/example.md": NOTE + "# Example\n"})
    node = replace(scan_vault(root).nodes[0], properties=properties)
    hit = SearchHit(node.id, node.path, node.kind, node.title, 100, ())

    assert apply_freshness(hit, node, {}, as_of=date(2026, 1, 1)).score == expected
    assert (
        apply_freshness(
            hit, node, {"freshness": {"weight": 0}}, as_of=date(2026, 1, 1)
        ).score
        == 100
    )


def test_replicas_and_touches_do_not_change_undated_ranking(
    make_vault, tmp_path, capsys
) -> None:
    root = make_vault(
        notes={
            "notes/a.md": NOTE + "# Identical\n\nShared probe.\n",
            "notes/b.md": NOTE + "# Identical\n\nShared probe.\n",
        }
    )
    replica = tmp_path / "replica"
    shutil.copytree(root, replica)
    times = (946684800, 1893456000)
    for copy, timestamps in ((root, times), (replica, times[::-1])):
        for name, timestamp in zip(("a", "b"), timestamps, strict=True):
            os.utime(copy / "notes" / f"{name}.md", (timestamp, timestamp))
    for command in ("search", "context"):
        expected = _payload(root, capsys, "--no-cache", command, "probe")
        assert [hit["path"] for hit in expected["hits"]] == [
            "notes/a.md",
            "notes/b.md",
        ]
        assert expected["hits"][0]["score"] == expected["hits"][1]["score"]
        for copy in (root, replica):
            expected["root"] = str(copy.resolve())
            assert _payload(copy, capsys, command, "probe") == expected
            assert _payload(copy, capsys, command, "probe") == expected
            target = copy / "notes" / "a.md"
            os.utime(target, (times[1], times[1]))
            assert _payload(copy, capsys, command, "probe") == expected
            assert _payload(copy, capsys, "--no-cache", command, "probe") == expected


def test_freshness_and_context_policy_match_cache_and_fallback(
    make_vault, monkeypatch, capsys
) -> None:
    def fixed_day() -> date:
        return date(2026, 1, 1)

    monkeypatch.setattr(vaultctl.search, "ranking_date", fixed_day)
    monkeypatch.setattr(vaultctl.index, "ranking_date", fixed_day)
    root = make_vault(
        manifest_overrides={
            "search": {"stemming": {"enabled": False}},
            "context": {
                "preferredSections": ["Current state"],
                "outputFields": ["status", "updated"],
            },
        },
        notes={
            "notes/a-old.md": (
                "---\nupdated: 2020-01-01\nstatus: archived\n---\n"
                "# Guide\nQuery old assumption.\n\n## Current state\nRetired.\n"
            ),
            "notes/z-new.md": (
                "---\nupdated: 2026-01-01\nstatus: active\n---\n"
                "# Guide\nQuery old assumption.\n\n## Current state\nReplacement.\n"
            ),
        },
    )
    for command in ("search", "context"):
        cached = _payload(root, capsys, command, "query")
        assert cached["hits"][0]["path"] == "notes/z-new.md"
        assert _payload(root, capsys, "--no-cache", command, "query") == cached
        if command == "context":
            assert cached["hits"][0]["snippets"] == ["Replacement."]
        with monkeypatch.context() as unavailable:
            unavailable.setattr(vaultctl.cli, "open_index", lambda _: None)
            assert _payload(root, capsys, command, "query") == cached


def test_preferred_section_boundaries_and_priority(make_vault) -> None:
    root = make_vault(
        manifest_overrides={
            "context": {
                "preferredSections": ["Current state", "Decision"],
                "snippetLines": 3,
            }
        },
        notes={
            "notes/example.md": (
                NOTE + "# Guide\nQuery old assumption.\n"
                "```markdown\n## Current state\nFake content.\n```\n"
                "<!--\n## Current state\nAlso fake.\n-->\n"
                "## Decision\nSecondary result.\n"
                "## CURRENT STATE ##\nReplaced.\n"
                "### Reason\nEvidence accepted.\n"
                "~~~~text\n## History\nCode is not context.\n~~~~\n"
                "## History\nQuery old assumption again.\n"
            )
        },
    )

    assert context(scan_vault(root), "query").hits[0].snippets == (
        "Replaced.",
        "Evidence accepted.",
    )


def test_missing_section_uses_matching_fallback(make_vault) -> None:
    root = make_vault(
        manifest_overrides={"context": {"preferredSections": ["Current state"]}},
        notes={"notes/example.md": NOTE + "# Guide\nQuery legacy line.\n"},
    )
    assert context(scan_vault(root), "query").hits[0].snippets == (
        "Query legacy line.",
    )


def test_empty_current_section_does_not_fall_back_to_history(make_vault):
    root = make_vault(
        manifest_overrides={"context": {"preferredSections": ["Current state"]}},
        notes={
            "notes/example.md": NOTE + "# Guide\nQuery legacy line.\n"
            "## Current state\n\n## History\nQuery old claim.\n"
        },
    )
    assert context(scan_vault(root), "query").hits[0].snippets == ()


@pytest.mark.parametrize("ticks", ["`", "``"])
def test_literal_comment_in_inline_code_does_not_hide_section(make_vault, ticks):
    root = make_vault(
        manifest_overrides={"context": {"preferredSections": ["Current state"]}},
        notes={
            "notes/example.md": NOTE + "# Guide\nQuery legacy line.\n"
            f"The literal opener is {ticks}<!--{ticks}.\n"
            "## Current state\nCorrect current.\n"
        },
    )
    assert context(scan_vault(root), "query").hits[0].snippets == ("Correct current.",)


def test_ambiguous_preferred_section_does_not_emit_legacy_context(make_vault):
    root = make_vault(
        manifest_overrides={"context": {"preferredSections": ["Current state"]}},
        notes={
            "notes/example.md": NOTE + "# Guide\nQuery obsolete claim.\n"
            "## Current state\nFirst.\n## Current state\nSecond.\n"
        },
    )
    assert context(scan_vault(root), "query").hits[0].snippets == ()


def test_comment_opening_in_fence_does_not_hide_current_section(make_vault):
    root = make_vault(
        manifest_overrides={"context": {"preferredSections": ["Current state"]}},
        notes={
            "notes/example.md": NOTE + "# Guide\nQuery obsolete claim.\n"
            "```html\n<!--\n```\n"
            "## Current state\nCorrect current.\n"
            "<!-- trailing unrelated -->\n## History\nQuery old result.\n"
        },
    )
    assert context(scan_vault(root), "query").hits[0].snippets == ("Correct current.",)


@pytest.mark.parametrize("snippet_lines", [0, 1])
def test_preferred_sections_honor_snippet_limits(make_vault, snippet_lines) -> None:
    root = make_vault(
        manifest_overrides={
            "context": {
                "preferredSections": ["Current state"],
                "snippetLines": snippet_lines,
                "snippetCharacters": 32,
            }
        },
        notes={"notes/example.md": NOTE + "# Query\n## Current state\n" + "x" * 100},
    )
    expected = ("x" * 32,) if snippet_lines else ()
    assert context(scan_vault(root), "query").hits[0].snippets == expected


@pytest.mark.parametrize("value", ["Current state", [""], [" "], ["x", "x"], [7]])
def test_preferred_sections_manifest_validation(make_vault, value) -> None:
    root = make_vault(manifest_overrides={"context": {"preferredSections": value}})
    with pytest.raises(ManifestError, match="preferredSections"):
        load_manifest(root)


@pytest.mark.parametrize("grouped", [False, True])
def test_context_text_projects_only_allowed_properties(make_vault, capsys, grouped):
    config = {"outputFields": ["status", "details", "missing"]}
    if grouped:
        config["grouping"] = {"fields": ["status"]}
    root = make_vault(
        manifest_overrides={"context": config},
        notes={
            "notes/example.md": (
                "---\nstatus: active\nprivate: hidden-value\n"
                "details:\n  labels: [one, two]\n  reason: |\n    First.\n    Second.\n"
                "---\n# Query\n"
            )
        },
    )
    payload = _payload(root, capsys, "context", "query")
    properties = payload["hits"][0]["properties"]
    assert set(properties) == {"status", "details"}
    assert main(["--vault", str(root), "--format", "text", "context", "query"]) == 0
    rendered = capsys.readouterr().out
    assert context_properties_text(properties) in rendered
    assert "hidden-value" not in rendered
    assert "missing" not in rendered
    assert "First.\\nSecond.\\n" in rendered
    hit = payload["hits"][0]
    assert payload["budget"]["usedCharacters"] == (
        len(hit["path"])
        + len(hit["title"])
        + sum(map(len, hit["snippets"]))
        + len(context_properties_text(properties))
    )


@pytest.mark.parametrize("grouped", [False, True])
def test_projected_properties_cannot_exceed_context_budget(make_vault, grouped):
    config = {"maxCharacters": 256, "outputFields": ["details"]}
    if grouped:
        config["grouping"] = {"fields": ["details"]}
    root = make_vault(
        manifest_overrides={"context": config},
        notes={"notes/example.md": "---\ndetails: " + "x" * 300 + "\n---\n# Query\n"},
    )
    selected = context(scan_vault(root), "query")
    assert selected.hits == ()
    assert selected.used_characters == 0
    assert selected.truncated is True
