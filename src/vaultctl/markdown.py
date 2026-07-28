from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from vaultctl.errors import MarkdownError

HEADING_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
WIKILINK_RE = re.compile(r"!?\[\[([^\]]+)\]\]")
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
FENCED_CODE_RE = re.compile(
    r"^ {0,3}(?:```|~~~).*?^ {0,3}(?:```|~~~)[ \t]*$",
    re.MULTILINE | re.DOTALL,
)
INLINE_CODE_RE = re.compile(r"`+[^`\n]*`+")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


@dataclass(frozen=True)
class ParsedMarkdown:
    properties: dict[str, Any]
    body: str
    title: str
    source_hash: str


def _to_plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _to_plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_to_plain(item) for item in value]
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def parse_markdown(path: Path, *, display_path: str) -> ParsedMarkdown:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except OSError as exc:
        raise MarkdownError(f"cannot read {display_path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise MarkdownError(f"{display_path} is not valid UTF-8") from exc

    properties: dict[str, Any] = {}
    body = text
    lines = text.splitlines(keepends=True)

    if lines and lines[0].strip() == "---":
        closing_index = next(
            (
                index
                for index, line in enumerate(lines[1:], start=1)
                if line.strip() in {"---", "..."}
            ),
            None,
        )
        if closing_index is None:
            raise MarkdownError(f"{display_path} has unclosed frontmatter")

        yaml = YAML(typ="rt")
        yaml.allow_duplicate_keys = False
        yaml.preserve_quotes = True
        frontmatter = "".join(lines[1:closing_index])
        try:
            loaded = yaml.load(frontmatter) or {}
        except Exception as exc:
            raise MarkdownError(
                f"{display_path} has invalid YAML frontmatter: {exc}"
            ) from exc
        if not isinstance(loaded, Mapping):
            raise MarkdownError(f"{display_path} frontmatter must be a mapping")
        properties = _to_plain(loaded)
        body = "".join(lines[closing_index + 1 :])

    heading = HEADING_RE.search(body)
    title = heading.group(1).strip() if heading else path.stem
    return ParsedMarkdown(
        properties=properties,
        body=body,
        title=title,
        source_hash=hashlib.sha256(raw).hexdigest(),
    )


def extract_body_links(body: str) -> tuple[tuple[str, str], ...]:
    searchable = FENCED_CODE_RE.sub("", body)
    searchable = INLINE_CODE_RE.sub("", searchable)
    searchable = HTML_COMMENT_RE.sub("", searchable)
    links: list[tuple[str, str]] = []
    links.extend(
        (match.group(1), "wikilink") for match in WIKILINK_RE.finditer(searchable)
    )
    links.extend(
        (match.group(1), "markdown-link")
        for match in MARKDOWN_LINK_RE.finditer(searchable)
    )
    return tuple(links)


def normalize_tags(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, list):
        candidates = [item for item in value if isinstance(item, str)]
    else:
        return ()

    normalized = []
    for tag in candidates:
        clean = tag.strip().lstrip("#")
        if clean and clean not in normalized:
            normalized.append(clean)
    return tuple(normalized)
