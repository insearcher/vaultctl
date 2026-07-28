from __future__ import annotations

from vaultctl.markdown import extract_body_links, normalize_tags, parse_markdown


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
    assert len(parsed.source_hash) == 64


def test_link_extraction_ignores_images() -> None:
    links = extract_body_links(
        "See [[guide]] and [roadmap](roadmap.md), not ![image](image.png)."
    )

    assert links == (
        ("guide", "wikilink"),
        ("roadmap.md", "markdown-link"),
    )


def test_tags_are_normalized_without_duplicates() -> None:
    assert normalize_tags(["#guide", "guide", " planning "]) == (
        "guide",
        "planning",
    )
