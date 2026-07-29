from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from vaultctl.errors import MarkdownError

H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
WIKILINK_RE = re.compile(r"!?\[\[([^\]]+)\]\]")
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
FENCED_CODE_RE = re.compile(
    r"^ {0,3}(?:```|~~~).*?^ {0,3}(?:```|~~~)[ \t]*$",
    re.MULTILINE | re.DOTALL,
)
INLINE_CODE_RE = re.compile(r"`+[^`\n]*`+")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
BODY_TAG_RE = re.compile(r"(?<!\w)#([A-Za-z0-9_/-]+)")
TOP_LEVEL_SCALAR_RE = re.compile(
    r"^(?P<key>[A-Za-z_][A-Za-z0-9_-]*):(?P<spacing>[ \t]*)(?P<value>.*)$"
)
YAML_STRUCTURED_VALUE_PREFIXES = frozenset("\"'[{>|!&*")


@dataclass(frozen=True)
class ParsedMarkdown:
    properties: dict[str, Any]
    body: str
    title: str
    headings: tuple[str, ...]
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


def _quote_legacy_colon_scalars(frontmatter: str) -> str:
    normalized = []
    for line in frontmatter.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        ending = line[len(content) :]
        match = TOP_LEVEL_SCALAR_RE.fullmatch(content)
        if match is None:
            normalized.append(line)
            continue

        value = match.group("value")
        stripped = value.lstrip()
        if (
            ": " not in value
            or not stripped
            or stripped[0] in YAML_STRUCTURED_VALUE_PREFIXES
        ):
            normalized.append(line)
            continue

        normalized.append(
            f"{match.group('key')}:{match.group('spacing')}"
            f"{json.dumps(value, ensure_ascii=False)}{ending}"
        )
    return "".join(normalized)


def parse_markdown(
    path: Path,
    *,
    display_path: str,
    allow_legacy_colon_scalars: bool = False,
) -> ParsedMarkdown:
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
        except Exception as strict_exc:
            if not allow_legacy_colon_scalars:
                raise MarkdownError(
                    f"{display_path} has invalid YAML frontmatter: {strict_exc}"
                ) from strict_exc

            normalized = _quote_legacy_colon_scalars(frontmatter)
            if normalized == frontmatter:
                raise MarkdownError(
                    f"{display_path} has invalid YAML frontmatter: {strict_exc}"
                ) from strict_exc
            try:
                loaded = yaml.load(normalized) or {}
            except Exception as fallback_exc:
                raise MarkdownError(
                    f"{display_path} has invalid YAML frontmatter: {fallback_exc}"
                ) from fallback_exc
        if not isinstance(loaded, Mapping):
            raise MarkdownError(f"{display_path} frontmatter must be a mapping")
        properties = _to_plain(loaded)
        body = "".join(lines[closing_index + 1 :])

    headings = tuple(match.group(1).strip() for match in HEADING_RE.finditer(body))
    h1 = H1_RE.search(body)
    title = h1.group(1).strip() if h1 else path.stem
    return ParsedMarkdown(
        properties=properties,
        body=body,
        title=title,
        headings=headings,
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


def normalize_tags(value: Any, body: str = "") -> tuple[str, ...]:
    if value is None:
        candidates = []
    elif isinstance(value, str):
        candidates = [value]
    elif isinstance(value, list):
        candidates = [item for item in value if isinstance(item, str)]
    else:
        candidates = []
    candidates.extend(BODY_TAG_RE.findall(body))

    normalized = []
    for tag in candidates:
        clean = tag.strip().lstrip("#")
        if clean and clean not in normalized:
            normalized.append(clean)
    return tuple(normalized)
