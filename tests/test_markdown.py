from __future__ import annotations

import pytest

from vaultctl.errors import MarkdownError
from vaultctl.markdown import (
    extract_body_links,
    normalize_tags,
    parse_markdown,
    render_markdown_candidate,
)


def test_frontmatter_parses_block_lists_and_dates(tmp_path) -> None:
    path = tmp_path / "example.md"
    path.write_text(
        "---\ntags:\n  - example\npublished: 2026-01-02\n---\n\n# Example\n",
        encoding="utf-8",
    )

    parsed = parse_markdown(path, display_path="example.md")

    assert parsed.properties == {
        "tags": ["example"],
        "published": "2026-01-02",
    }
    assert parsed.title == "Example"
    assert parsed.headings == ("Example",)
    assert len(parsed.source_hash) == 64


def test_frontmatter_rejects_unquoted_colon_scalar_by_default(tmp_path) -> None:
    path = tmp_path / "example.md"
    path.write_text(
        "---\ndescription: Use when route: fallback\n---\n# Example\n",
        encoding="utf-8",
    )

    with pytest.raises(MarkdownError, match="invalid YAML frontmatter"):
        parse_markdown(path, display_path="example.md")


def test_frontmatter_can_parse_legacy_unquoted_colon_scalar(tmp_path) -> None:
    path = tmp_path / "example.md"
    path.write_text(
        "---\n"
        "description: Use when route: fallback\n"
        "tags: [routing, compatibility]\n"
        "---\n"
        "# Example\n",
        encoding="utf-8",
    )

    parsed = parse_markdown(
        path,
        display_path="example.md",
        allow_legacy_colon_scalars=True,
    )

    assert parsed.properties == {
        "description": "Use when route: fallback",
        "tags": ["routing", "compatibility"],
    }


def test_legacy_colon_mode_does_not_repair_nested_invalid_yaml(tmp_path) -> None:
    path = tmp_path / "example.md"
    path.write_text(
        "---\nmetadata:\n  description: route: fallback\n---\n# Example\n",
        encoding="utf-8",
    )

    with pytest.raises(MarkdownError, match="invalid YAML frontmatter"):
        parse_markdown(
            path,
            display_path="example.md",
            allow_legacy_colon_scalars=True,
        )


def test_link_extraction_ignores_images() -> None:
    links = extract_body_links(
        "See [[guide]] and [roadmap](roadmap.md), not ![image](image.png)."
    )

    assert links == (
        ("guide", "wikilink"),
        ("roadmap.md", "markdown-link"),
    )


def test_link_extraction_ignores_code_and_comments() -> None:
    links = extract_body_links(
        "Keep [[real]].\n"
        "`[[inline-example]]`\n"
        "```yaml\nparent: '[[fenced-example]]'\n```\n"
        "<!-- [[comment-example]] -->\n"
    )

    assert links == (("real", "wikilink"),)


def test_tags_are_normalized_without_duplicates() -> None:
    assert normalize_tags(
        ["#guide", "guide", " planning "],
        "Use #routing and keep #guide.",
    ) == (
        "guide",
        "planning",
        "routing",
    )


def test_body_tag_extraction_ignores_code_and_comments() -> None:
    tags = normalize_tags(
        [],
        "Keep #real.\n"
        "`#inline-example`\n"
        "```c\n#include <stdio.h>\n#define EXAMPLE 1\n```\n"
        "<!-- #comment-example -->\n",
    )

    assert tags == ("real",)


def test_candidate_renderer_preserves_semantic_noop_bytes() -> None:
    current = b'---\ntitle: "Quoted" # keep\ntags: [example]\n...\n# Example\n'

    rendered = render_markdown_candidate(
        current,
        properties={"title": "Quoted", "tags": ["example"]},
        body="# Example\n",
        display_path="notes/example.md",
    )

    assert rendered == current


def test_candidate_renderer_changes_only_body_when_properties_match() -> None:
    current = b'---\ntitle: "Quoted" # keep\ntags: [example]\n...\n# Example\n'

    rendered = render_markdown_candidate(
        current,
        properties={"title": "Quoted", "tags": ["example"]},
        body="# Updated\n",
        display_path="notes/example.md",
    )

    assert rendered == (
        b'---\ntitle: "Quoted" # keep\ntags: [example]\n...\n# Updated\n'
    )


def test_candidate_renderer_distinguishes_boolean_from_integer() -> None:
    current = b"---\nvalue: 1\n---\n# Example\n"

    rendered = render_markdown_candidate(
        current,
        properties={"value": True},
        body="# Example\n",
        display_path="notes/example.md",
    )

    assert rendered == b"---\nvalue: true\n---\n# Example\n"
