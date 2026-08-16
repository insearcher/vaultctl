"""Disposable SQLite read index for fast repeated vault reads.

The cache lives outside the vault under the user state directory, keyed by the
hashed real path of the vault root. It is never a source of truth: Markdown
files and the manifest remain authoritative, and any SQLite, schema, or
filesystem failure must end in a silent fallback to a full ``scan_vault``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import sqlite3
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timezone
from functools import cache, lru_cache
from pathlib import Path
from typing import Any, NamedTuple

import snowballstemmer

from vaultctl import __version__
from vaultctl.engine import (
    BUILTIN_IGNORES,
    PendingNode,
    assemble,
    build_pending,
    path_matches,
)
from vaultctl.errors import CacheError, MarkdownError, QueryError
from vaultctl.manifest import load_manifest
from vaultctl.markdown import parse_markdown
from vaultctl.merge import manifest_digest
from vaultctl.model import (
    ContextResult,
    Edge,
    Node,
    ScanResult,
    SearchHit,
    ValidationIssue,
    VaultManifest,
)
from vaultctl.search import (
    DEFAULT_SEARCH_LIMIT,
    MAX_SEARCH_LIMIT,
    STEM_MATCH_WEIGHT,
    TOKEN_RE,
    build_context_result,
    resolve_limit,
    resolved_zones,
    score_node,
    search_config,
    stop_words,
    tokenize,
    zone_text,
)

CACHE_SCHEMA_VERSION = "1"
CANDIDATE_LIMIT = 200
STEM_PREFIX = "s:"
DEFAULT_FRESHNESS_HALF_LIFE_DAYS = 90.0
DEFAULT_FRESHNESS_WEIGHT = 0.15
MAX_NEIGHBOR_DEPTH = 3
DEFAULT_NEIGHBOR_LIMIT = 20
CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")
LATIN_RE = re.compile(r"[a-z]")

_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
  path TEXT PRIMARY KEY,
  mtime_ns INTEGER NOT NULL,
  size INTEGER NOT NULL,
  escaped INTEGER NOT NULL DEFAULT 0,
  issues TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS nodes (
  path TEXT PRIMARY KEY,
  id TEXT NOT NULL,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  properties TEXT NOT NULL,
  tags TEXT NOT NULL,
  updated TEXT,
  created TEXT,
  source_hash TEXT NOT NULL,
  body TEXT NOT NULL,
  headings TEXT NOT NULL,
  links TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nodes_id ON nodes (id);
CREATE TABLE IF NOT EXISTS edges (
  src TEXT NOT NULL,
  dst TEXT NOT NULL,
  field TEXT NOT NULL,
  provenance TEXT NOT NULL,
  source_location TEXT,
  resolved INTEGER NOT NULL,
  position INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_edges_src ON edges (src);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges (dst);
CREATE TABLE IF NOT EXISTS postings (
  term TEXT NOT NULL,
  file INTEGER NOT NULL,
  zone INTEGER NOT NULL,
  count INTEGER NOT NULL,
  PRIMARY KEY (term, file, zone)
) WITHOUT ROWID;
"""
_TABLES = ("meta", "files", "nodes", "edges", "postings")


class FileStat(NamedTuple):
    mtime_ns: int
    size: int
    escape: bool


@cache
def _stemmer(language: str) -> Any:
    return snowballstemmer.stemmer(language)


@lru_cache(maxsize=65536)
def stem_term(token: str) -> str:
    """Stem one lowercase token by script: Cyrillic → russian, Latin → english."""
    if CYRILLIC_RE.search(token):
        return _stemmer("russian").stemWord(token)
    if LATIN_RE.search(token):
        return _stemmer("english").stemWord(token)
    return token


def state_directory() -> Path:
    override = os.environ.get("VAULTCTL_STATE_DIR")
    if override:
        return Path(override).expanduser()
    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    if xdg_state_home:
        return Path(xdg_state_home).expanduser() / "vaultctl"
    return Path.home() / ".local" / "state" / "vaultctl"


def cache_path_for(root: Path) -> Path:
    key = hashlib.sha256(os.fsencode(os.path.realpath(root))).hexdigest()[:16]
    return state_directory() / key / "index.db"


def _ignored(path: str, patterns: tuple[str, ...]) -> bool:
    return any(path_matches(path, pattern) for pattern in patterns)


def _resolves_inside(root: Path, relative: str) -> bool:
    try:
        resolved = (root / relative).resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return False
    return True


def walk_vault_files(manifest: VaultManifest) -> dict[str, FileStat]:
    """Stat every included Markdown path with an ``os.scandir`` walk.

    Yields the same candidate set as the engine's full scan: entries whose
    name ends in ``.md`` and that are not excluded by ignore patterns.
    Directories are pruned only when an exact ``<dir>/**`` ignore pattern
    guarantees that everything below them is excluded.
    """
    patterns = tuple(dict.fromkeys((*BUILTIN_IGNORES, *manifest.ignore)))
    pattern_set = set(patterns)
    root = manifest.root
    root_is_real = os.path.realpath(root) == str(root)
    stats: dict[str, FileStat] = {}
    stack: list[tuple[str, str]] = [(str(root), "")]
    while stack:
        directory, prefix = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            relative = f"{prefix}{entry.name}"
            is_real_dir = entry.is_dir(follow_symlinks=False)
            if is_real_dir and f"{relative}/**" not in pattern_set:
                stack.append((entry.path, f"{relative}/"))
            if not entry.name.endswith(".md") or _ignored(relative, patterns):
                continue
            if is_real_dir:
                stat_result = entry.stat(follow_symlinks=False)
                stats[relative] = FileStat(
                    stat_result.st_mtime_ns, stat_result.st_size, escape=False
                )
                continue
            try:
                stat_result = entry.stat()
            except OSError:
                stat_result = entry.stat(follow_symlinks=False)
                stats[relative] = FileStat(
                    stat_result.st_mtime_ns, stat_result.st_size, escape=True
                )
                continue
            escape = (entry.is_symlink() or not root_is_real) and not _resolves_inside(
                root, relative
            )
            stats[relative] = FileStat(
                stat_result.st_mtime_ns, stat_result.st_size, escape=escape
            )
    return stats


def _issue_dicts(issues: tuple[ValidationIssue, ...] | list[ValidationIssue]) -> str:
    return json.dumps(
        [issue.to_dict() for issue in issues], ensure_ascii=False, sort_keys=True
    )


def _issues_from_json(raw: str) -> list[ValidationIssue]:
    return [
        ValidationIssue(
            level=item["level"],
            code=item["code"],
            message=item["message"],
            path=item.get("path"),
        )
        for item in json.loads(raw)
    ]


def _date_property(properties: dict[str, Any], key: str) -> str | None:
    value = properties.get(key)
    if isinstance(value, str) and value:
        return value
    return None


def _age_days(
    updated: str | None,
    created: str | None,
    mtime_ns: int | None,
    today: date,
) -> float:
    for value in (updated, created):
        if not value:
            continue
        try:
            known = date.fromisoformat(value[:10])
        except ValueError:
            continue
        return float(max((today - known).days, 0))
    if mtime_ns is not None:
        try:
            known = date.fromtimestamp(mtime_ns / 1_000_000_000)
        except (OSError, OverflowError, ValueError):
            return 0.0
        return float(max((today - known).days, 0))
    return 0.0


def _normalized_score(value: float) -> float:
    rounded = round(value, 3)
    if isinstance(rounded, float) and rounded.is_integer():
        return int(rounded)
    return rounded


class VaultIndex:
    """One open read-index cache bound to a vault and its manifest."""

    def __init__(
        self,
        manifest: VaultManifest,
        connection: sqlite3.Connection,
        db_path: Path,
    ) -> None:
        self.manifest = manifest
        self.db_path = db_path
        self._connection = connection

    @classmethod
    def open(cls, root: Path, *, manifest: VaultManifest | None = None) -> VaultIndex:
        if manifest is None:
            manifest = load_manifest(root)
        db_path = cache_path_for(manifest.root)
        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CacheError(f"cannot create read index directory: {exc}") from exc
        try:
            connection = cls._connect(db_path)
        except sqlite3.DatabaseError:
            cls._remove_database(db_path)
            try:
                connection = cls._connect(db_path)
            except sqlite3.Error as exc:
                raise CacheError(f"cannot open read index: {exc}") from exc
        except sqlite3.Error as exc:
            raise CacheError(f"cannot open read index: {exc}") from exc
        return cls(manifest, connection, db_path)

    @staticmethod
    def _connect(db_path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(db_path, timeout=5.0)
        try:
            connection.executescript(_SCHEMA_DDL)
        except sqlite3.Error:
            connection.close()
            raise
        return connection

    @staticmethod
    def _remove_database(db_path: Path) -> None:
        for suffix in ("", "-journal", "-wal", "-shm"):
            candidate = Path(f"{db_path}{suffix}")
            with contextlib.suppress(OSError):
                candidate.unlink()

    def close(self) -> None:
        self._connection.close()

    # -- refresh -----------------------------------------------------------

    def _expected_meta(self) -> dict[str, str]:
        return {
            "schemaVersion": CACHE_SCHEMA_VERSION,
            "engineVersion": __version__,
            "manifestDigest": manifest_digest(self.manifest),
            "vaultId": self.manifest.vault_id,
            "root": str(self.manifest.root),
        }

    def refresh(self, *, force: bool = False) -> None:
        try:
            self._refresh(force=force)
        except sqlite3.Error as exc:
            raise CacheError(f"read index refresh failed: {exc}") from exc

    def _refresh(self, *, force: bool) -> None:
        cursor = self._connection.cursor()
        meta = dict(cursor.execute("SELECT key, value FROM meta"))
        expected = self._expected_meta()
        stale = any(
            meta.get(key) != value for key, value in expected.items() if key != "root"
        )
        rebuild = force or stale
        if rebuild:
            for table in ("files", "nodes", "edges", "postings"):
                cursor.execute(f"DELETE FROM {table}")  # noqa: S608
            stored: dict[str, FileStat] = {}
        else:
            stored = {
                path: FileStat(mtime_ns, size, bool(escape))
                for path, mtime_ns, size, escape in cursor.execute(
                    "SELECT path, mtime_ns, size, escaped FROM files"
                )
            }
        walked = walk_vault_files(self.manifest)
        changed = [path for path, item in walked.items() if stored.get(path) != item]
        removed = [path for path in stored if path not in walked]
        for path in sorted(changed):
            self._process_file(cursor, path, walked[path])
        for path in removed:
            self._evict_postings(cursor, path)
            cursor.execute("DELETE FROM files WHERE path = ?", (path,))
            cursor.execute("DELETE FROM nodes WHERE path = ?", (path,))
        if rebuild or changed or removed:
            self._reassemble(cursor)
            for key, value in expected.items():
                self._set_meta(cursor, key, value)
            self._set_meta(
                cursor,
                "updatedAt",
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
        self._connection.commit()

    @staticmethod
    def _set_meta(cursor: sqlite3.Cursor, key: str, value: str) -> None:
        cursor.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def _process_file(
        self,
        cursor: sqlite3.Cursor,
        relative: str,
        item: FileStat,
    ) -> None:
        issues: list[ValidationIssue] = []
        pending: PendingNode | None = None
        if item.escape:
            issues.append(
                ValidationIssue(
                    level="error",
                    code="path.escape",
                    message="Markdown path resolves outside the vault root",
                    path=relative,
                )
            )
        else:
            try:
                parsed = parse_markdown(
                    self.manifest.root / relative,
                    display_path=relative,
                    allow_legacy_colon_scalars=(
                        self.manifest.allow_legacy_colon_scalars
                    ),
                )
            except MarkdownError as exc:
                issues.append(
                    ValidationIssue(
                        level="error",
                        code="markdown.parse",
                        message=str(exc),
                        path=relative,
                    )
                )
            else:
                pending, item_issues = build_pending(
                    parsed, relative=relative, manifest=self.manifest
                )
                issues.extend(item_issues)

        self._evict_postings(cursor, relative)
        cursor.execute(
            "INSERT INTO files (path, mtime_ns, size, escaped, issues) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (path) DO UPDATE SET mtime_ns = excluded.mtime_ns, "
            "size = excluded.size, escaped = excluded.escaped, "
            "issues = excluded.issues",
            (
                relative,
                item.mtime_ns,
                item.size,
                int(item.escape),
                _issue_dicts(issues),
            ),
        )
        cursor.execute("DELETE FROM nodes WHERE path = ?", (relative,))
        if pending is None:
            return
        node = pending.node
        cursor.execute(
            "INSERT INTO nodes (path, id, kind, title, properties, tags, updated, "
            "created, source_hash, body, headings, links) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                node.path,
                node.id,
                node.kind,
                node.title,
                json.dumps(node.properties, ensure_ascii=False, sort_keys=True),
                json.dumps(list(node.tags), ensure_ascii=False),
                _date_property(node.properties, "updated"),
                _date_property(node.properties, "created"),
                node.source_hash,
                node.body,
                json.dumps(list(node.headings), ensure_ascii=False),
                json.dumps(
                    [[raw, syntax] for raw, syntax in pending.links],
                    ensure_ascii=False,
                ),
            ),
        )
        file_id = cursor.execute(
            "SELECT rowid FROM files WHERE path = ?", (relative,)
        ).fetchone()[0]
        cursor.executemany(
            "INSERT OR REPLACE INTO postings (term, file, zone, count) "
            "VALUES (?, ?, ?, ?)",
            (
                (term, file_id, zone_index, count)
                for term, zone_index, count in self._posting_entries(node)
            ),
        )

    def _evict_postings(self, cursor: sqlite3.Cursor, relative: str) -> None:
        """Delete a path's postings by recomputing their exact keys.

        The postings table has no per-file index; the old node row still holds
        the text that produced the previous rows, so their keys are recomputed
        deterministically instead of scanning the whole table.
        """
        file_row = cursor.execute(
            "SELECT rowid FROM files WHERE path = ?", (relative,)
        ).fetchone()
        if file_row is None:
            return
        old = cursor.execute(
            "SELECT id, kind, title, properties, tags, source_hash, body, "
            "headings FROM nodes WHERE path = ?",
            (relative,),
        ).fetchone()
        if old is None:
            return
        node = Node(
            id=old[0],
            path=relative,
            kind=old[1],
            title=old[2],
            properties=json.loads(old[3]),
            tags=tuple(json.loads(old[4])),
            source_hash=old[5],
            body=old[6],
            headings=tuple(json.loads(old[7])),
        )
        cursor.executemany(
            "DELETE FROM postings WHERE term = ? AND file = ? AND zone = ?",
            (
                (term, file_row[0], zone_index)
                for term, zone_index, _ in self._posting_entries(node)
            ),
        )

    def _posting_entries(self, node: Node) -> list[tuple[str, int, int]]:
        """Index every token twice: verbatim, and stemmed when that differs."""
        zones = resolved_zones(self.manifest)
        stop = stop_words(self.manifest)
        entries: list[tuple[str, int, int]] = []
        for zone_index, zone in enumerate(zones):
            exact: Counter[str] = Counter()
            for token in TOKEN_RE.findall(zone_text(node, zone).lower()):
                if len(token) > 1 and token not in stop:
                    exact[token] += 1
            stems: Counter[str] = Counter()
            for token, count in exact.items():
                stem = stem_term(token)
                if stem != token:
                    stems[stem] += count
            entries.extend((term, zone_index, count) for term, count in exact.items())
            entries.extend(
                (f"{STEM_PREFIX}{term}", zone_index, count)
                for term, count in stems.items()
            )
        return entries

    def _reassemble(self, cursor: sqlite3.Cursor) -> None:
        issues: list[ValidationIssue] = []
        for (raw,) in cursor.execute("SELECT issues FROM files"):
            issues.extend(_issues_from_json(raw))
        pending: list[PendingNode] = []
        for row in cursor.execute(
            "SELECT path, id, kind, title, properties, tags, source_hash, links "
            "FROM nodes"
        ):
            path, node_id, kind, title, properties, tags, source_hash, links = row
            node = Node(
                id=node_id,
                path=path,
                kind=kind,
                title=title,
                properties=json.loads(properties),
                tags=tuple(json.loads(tags)),
                source_hash=source_hash,
            )
            pending.append(
                PendingNode(
                    node=node,
                    links=tuple(
                        (raw_target, syntax) for raw_target, syntax in json.loads(links)
                    ),
                )
            )
        result = assemble(self.manifest, pending, issues)
        node_ids = {node.id for node in result.nodes}
        cursor.execute("DELETE FROM edges")
        cursor.executemany(
            "INSERT INTO edges (src, dst, field, provenance, source_location, "
            "resolved, position) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    edge.source,
                    edge.target,
                    edge.relation,
                    edge.provenance,
                    edge.source_location,
                    int(edge.target in node_ids),
                    position,
                )
                for node in result.nodes
                for position, edge in enumerate(node.outgoing_edges)
            ),
        )
        self._set_meta(cursor, "issues", _issue_dicts(result.issues))

    # -- reads -------------------------------------------------------------

    def issues(self) -> tuple[ValidationIssue, ...]:
        try:
            row = self._connection.execute(
                "SELECT value FROM meta WHERE key = 'issues'"
            ).fetchone()
        except sqlite3.Error as exc:
            raise CacheError(f"read index is unreadable: {exc}") from exc
        if row is None:
            return ()
        return tuple(_issues_from_json(row[0]))

    def lite_result(self) -> ScanResult:
        """Result carrying manifest and issues only, for payload envelopes."""
        return ScanResult(manifest=self.manifest, nodes=(), issues=self.issues())

    def scan_result(self) -> ScanResult:
        try:
            return self._scan_result()
        except sqlite3.Error as exc:
            raise CacheError(f"read index is unreadable: {exc}") from exc

    def _scan_result(self) -> ScanResult:
        edges_by_source: dict[str, list[Edge]] = {}
        for src, dst, field, provenance, source_location in self._connection.execute(
            "SELECT src, dst, field, provenance, source_location FROM edges "
            "ORDER BY src, position"
        ):
            edges_by_source.setdefault(src, []).append(
                Edge(
                    source=src,
                    relation=field,
                    target=dst,
                    provenance=provenance,
                    source_location=source_location,
                )
            )
        nodes = tuple(
            Node(
                id=row[1],
                path=row[0],
                kind=row[2],
                title=row[3],
                properties=json.loads(row[4]),
                tags=tuple(json.loads(row[5])),
                source_hash=row[6],
                body=row[7],
                headings=tuple(json.loads(row[8])),
                outgoing_edges=tuple(edges_by_source.get(row[1], ())),
            )
            for row in self._connection.execute(
                "SELECT path, id, kind, title, properties, tags, source_hash, "
                "body, headings FROM nodes ORDER BY id"
            )
        )
        return ScanResult(manifest=self.manifest, nodes=nodes, issues=self.issues())

    # -- search ------------------------------------------------------------

    def search_hits(
        self,
        query: str,
        *,
        limit: int | None = None,
    ) -> tuple[SearchHit, ...]:
        if not query.strip():
            raise QueryError("search query is empty")
        config = search_config(self.manifest)
        resolved_limit = resolve_limit(
            requested=limit,
            config=config,
            default=DEFAULT_SEARCH_LIMIT,
            maximum=MAX_SEARCH_LIMIT,
            command="search",
        )
        hits, _ = self._ranked_hits(query)
        return hits[:resolved_limit]

    def context_result(
        self,
        query: str,
        *,
        limit: int | None = None,
    ) -> ContextResult:
        if not query.strip():
            raise QueryError("context query is empty")
        hits, nodes = self._ranked_hits(query)
        return build_context_result(
            self.manifest,
            query,
            ranked_hits=hits,
            nodes=nodes,
            limit=limit,
        )

    def _ranked_hits(
        self,
        query: str,
    ) -> tuple[tuple[SearchHit, ...], dict[str, Node]]:
        try:
            return self._ranked(query)
        except sqlite3.Error as exc:
            raise CacheError(f"read index search failed: {exc}") from exc

    def _ranked(self, query: str) -> tuple[tuple[SearchHit, ...], dict[str, Node]]:
        config = search_config(self.manifest)
        zones = resolved_zones(self.manifest)
        boosts = tuple(config.get("boosts", ()))
        stemming = config.get("stemming", {}).get("enabled", True)
        phrase = query.strip().lower()
        tokens = tokenize(query, stop_words=stop_words(self.manifest))
        candidates = self._candidate_paths(tokens, zones, stemming_enabled=stemming)
        if not candidates:
            return (), {}
        paths = [path for path, _ in candidates]
        ids_by_path = dict(candidates)
        nodes, mtimes = self._load_nodes(paths)
        stem_counts = (
            self._stem_counts([file_id for _, file_id in candidates], tokens)
            if stemming and tokens
            else {}
        )
        half_life, weight = self._freshness_config(config)
        today = date.today()
        hits = []
        for path in paths:
            node = nodes.get(path)
            if node is None:
                continue
            file_id = ids_by_path[path]

            def _lookup(
                zone_index: int,
                token: str,
                file_id: int = file_id,
            ) -> int:
                return stem_counts.get((file_id, zone_index, stem_term(token)), 0)

            hit = score_node(
                node,
                phrase=phrase,
                tokens=tokens,
                zones=zones,
                boosts=boosts,
                stem_counts=_lookup if stemming else None,
            )
            if hit is None:
                continue
            score = float(hit.score)
            if weight > 0:
                age = _age_days(
                    _date_property(node.properties, "updated"),
                    _date_property(node.properties, "created"),
                    mtimes.get(path),
                    today,
                )
                score *= 1.0 + weight * 0.5 ** (age / half_life)
            hits.append(replace(hit, score=_normalized_score(score)))
        hits.sort(key=lambda hit: (-hit.score, hit.path))
        return tuple(hits), {node.id: node for node in nodes.values()}

    @staticmethod
    def _freshness_config(config: dict[str, Any]) -> tuple[float, float]:
        section = config.get("freshness", {})
        half_life = float(section.get("halfLifeDays", DEFAULT_FRESHNESS_HALF_LIFE_DAYS))
        weight = float(section.get("weight", DEFAULT_FRESHNESS_WEIGHT))
        return half_life, weight

    def _candidate_paths(
        self,
        tokens: tuple[str, ...],
        zones: tuple[dict[str, Any], ...],
        *,
        stemming_enabled: bool,
    ) -> list[tuple[str, int]]:
        """Rank candidate ``(path, file_id)`` pairs from the postings table."""
        if not tokens:
            return []
        row = self._connection.execute("SELECT COUNT(*) FROM nodes").fetchone()
        node_count = row[0] if row else 0
        if not node_count:
            return []
        weights = [zone.get("weight", 0) for zone in zones]
        caps = [zone.get("countCap", 1) for zone in zones]
        scores: dict[int, float] = {}

        def _accumulate(
            term_rows: list[tuple[str, int, int, int]],
            factor: float,
        ) -> None:
            by_term: dict[str, list[tuple[int, int, int]]] = {}
            for term, file_id, zone_index, count in term_rows:
                if zone_index < len(weights):
                    by_term.setdefault(term, []).append((file_id, zone_index, count))
            for rows in by_term.values():
                document_frequency = len({file_id for file_id, _, _ in rows})
                idf = math.log(
                    1
                    + (node_count - document_frequency + 0.5)
                    / (document_frequency + 0.5)
                )
                for file_id, zone_index, count in rows:
                    scores[file_id] = scores.get(file_id, 0.0) + (
                        weights[zone_index]
                        * min(count, caps[zone_index])
                        * idf
                        * factor
                    )

        for token in tokens:
            exact_rows = self._connection.execute(
                "SELECT term, file, zone, count FROM postings "
                "WHERE term GLOB ? || '*' AND term NOT GLOB 's:*'",
                (token,),
            ).fetchall()
            _accumulate(exact_rows, 1.0)
            if stemming_enabled:
                for term in self._stem_lookup_terms(token):
                    stem_rows = self._connection.execute(
                        "SELECT term, file, zone, count FROM postings WHERE term = ?",
                        (term,),
                    ).fetchall()
                    _accumulate(stem_rows, STEM_MATCH_WEIGHT)
        paths_by_id = self._paths_for_ids(list(scores))
        ranked = sorted(
            (
                (paths_by_id[file_id], file_id)
                for file_id in scores
                if file_id in paths_by_id
            ),
            key=lambda item: (-scores[item[1]], item[0]),
        )
        return ranked[:CANDIDATE_LIMIT]

    @staticmethod
    def _stem_lookup_terms(token: str) -> list[str]:
        """Postings terms that count as stem-equivalent matches for a token.

        Surface forms identical to their own stem are stored once, verbatim,
        so the stem lookup consults both the raw stem (unless the exact
        prefix query already covered it) and the ``s:``-prefixed entry.
        """
        stem = stem_term(token)
        terms = []
        if not stem.startswith(token):
            terms.append(stem)
        terms.append(f"{STEM_PREFIX}{stem}")
        return terms

    def _paths_for_ids(self, file_ids: list[int]) -> dict[int, str]:
        paths: dict[int, str] = {}
        for chunk_start in range(0, len(file_ids), 500):
            chunk = file_ids[chunk_start : chunk_start + 500]
            placeholders = ", ".join("?" for _ in chunk)
            for file_id, path in self._connection.execute(
                f"SELECT rowid, path FROM files WHERE rowid IN ({placeholders})",
                chunk,
            ):
                paths[file_id] = path
        return paths

    def _load_nodes(
        self,
        paths: list[str],
    ) -> tuple[dict[str, Node], dict[str, int]]:
        nodes: dict[str, Node] = {}
        mtimes: dict[str, int] = {}
        for chunk_start in range(0, len(paths), 500):
            chunk = paths[chunk_start : chunk_start + 500]
            placeholders = ", ".join("?" for _ in chunk)
            for row in self._connection.execute(
                "SELECT path, id, kind, title, properties, tags, source_hash, "
                f"body, headings FROM nodes WHERE path IN ({placeholders})",
                chunk,
            ):
                nodes[row[0]] = Node(
                    id=row[1],
                    path=row[0],
                    kind=row[2],
                    title=row[3],
                    properties=json.loads(row[4]),
                    tags=tuple(json.loads(row[5])),
                    source_hash=row[6],
                    body=row[7],
                    headings=tuple(json.loads(row[8])),
                )
            for path, mtime_ns in self._connection.execute(
                f"SELECT path, mtime_ns FROM files WHERE path IN ({placeholders})",
                chunk,
            ):
                mtimes[path] = mtime_ns
        return nodes, mtimes

    def _stem_counts(
        self,
        file_ids: list[int],
        tokens: tuple[str, ...],
    ) -> dict[tuple[int, int, str], int]:
        """Sum stem-equivalent counts per ``(file_id, zone, stem)``."""
        terms: set[str] = set()
        for token in tokens:
            stem = stem_term(token)
            terms.update((stem, f"{STEM_PREFIX}{stem}"))
        counts: dict[tuple[int, int, str], int] = {}
        ordered_terms = sorted(terms)
        term_marks = ", ".join("?" for _ in ordered_terms)
        for chunk_start in range(0, len(file_ids), 400):
            chunk = file_ids[chunk_start : chunk_start + 400]
            file_marks = ", ".join("?" for _ in chunk)
            for term, file_id, zone_index, count in self._connection.execute(
                "SELECT term, file, zone, count FROM postings "
                f"WHERE term IN ({term_marks}) AND file IN ({file_marks})",
                (*ordered_terms, *chunk),
            ):
                stem = (
                    term[len(STEM_PREFIX) :] if term.startswith(STEM_PREFIX) else term
                )
                key = (file_id, zone_index, stem)
                counts[key] = counts.get(key, 0) + count
        return counts

    # -- graph -------------------------------------------------------------

    def neighbors_data(
        self,
        target: str,
        *,
        depth: int,
        direction: str,
        limit: int,
    ) -> dict[str, Any]:
        try:
            node_meta = {
                row[0]: {
                    "id": row[0],
                    "path": row[1],
                    "kind": row[2],
                    "title": row[3],
                    "updated": row[4],
                }
                for row in self._connection.execute(
                    "SELECT id, path, kind, title, updated FROM nodes"
                )
            }
            edges = self._connection.execute(
                "SELECT src, dst, field FROM edges WHERE resolved = 1"
            ).fetchall()
        except sqlite3.Error as exc:
            raise CacheError(f"read index is unreadable: {exc}") from exc
        return compute_neighbors(
            node_meta=node_meta,
            edges=edges,
            target=target,
            depth=depth,
            direction=direction,
            limit=limit,
        )

    # -- status ------------------------------------------------------------

    def counts(self) -> dict[str, int]:
        counts = {}
        for table in ("files", "nodes", "edges", "postings"):
            row = self._connection.execute(
                f"SELECT COUNT(*) FROM {table}"  # noqa: S608
            ).fetchone()
            counts[table] = row[0] if row else 0
        row = self._connection.execute(
            "SELECT COUNT(DISTINCT term) FROM postings"
        ).fetchone()
        counts["terms"] = row[0] if row else 0
        return counts

    def meta(self) -> dict[str, str]:
        return dict(self._connection.execute("SELECT key, value FROM meta"))


def validate_neighbor_arguments(depth: int, direction: str, limit: int) -> None:
    if depth < 1 or depth > MAX_NEIGHBOR_DEPTH:
        raise QueryError(f"neighbors depth must be between 1 and {MAX_NEIGHBOR_DEPTH}")
    if direction not in {"in", "out", "both"}:
        raise QueryError("neighbors direction must be one of: in, out, both")
    if limit < 1:
        raise QueryError("neighbors limit must be positive")


def compute_neighbors(
    *,
    node_meta: dict[str, dict[str, Any]],
    edges: list[tuple[str, str, str]],
    target: str,
    depth: int,
    direction: str,
    limit: int,
) -> dict[str, Any]:
    validate_neighbor_arguments(depth, direction, limit)
    cleaned = target.strip()
    target_id = cleaned[:-3] if cleaned.endswith(".md") else cleaned
    if target_id not in node_meta:
        raise QueryError(
            f"neighbors target {target!r} does not match a note in the vault"
        )

    outgoing: dict[str, list[tuple[str, str]]] = {}
    incoming: dict[str, list[tuple[str, str]]] = {}
    for src, dst, field in edges:
        if src not in node_meta or dst not in node_meta:
            continue
        outgoing.setdefault(src, []).append((dst, field))
        incoming.setdefault(dst, []).append((src, field))

    distance: dict[str, int] = {target_id: 0}
    found: dict[str, set[tuple[str, str]]] = {}
    frontier = [target_id]
    for level in range(1, depth + 1):
        next_frontier: set[str] = set()
        for node_id in frontier:
            if direction in {"out", "both"}:
                for dst, field in outgoing.get(node_id, ()):
                    if dst not in distance:
                        distance[dst] = level
                        next_frontier.add(dst)
                    if distance[dst] == level:
                        found.setdefault(dst, set()).add((field, "out"))
            if direction in {"in", "both"}:
                for src, field in incoming.get(node_id, ()):
                    if src not in distance:
                        distance[src] = level
                        next_frontier.add(src)
                    if distance[src] == level:
                        found.setdefault(src, set()).add((field, "in"))
        frontier = sorted(next_frontier)

    ordered = sorted(
        found,
        key=lambda node_id: (distance[node_id], node_meta[node_id]["path"]),
    )
    truncated = len(ordered) > limit
    neighbors = [
        {
            "id": node_id,
            "path": node_meta[node_id]["path"],
            "kind": node_meta[node_id]["kind"],
            "title": node_meta[node_id]["title"],
            "updated": node_meta[node_id]["updated"],
            "distance": distance[node_id],
            "edges": [
                {"field": field, "direction": edge_direction}
                for field, edge_direction in sorted(found[node_id])
            ],
        }
        for node_id in ordered[:limit]
    ]
    return {
        "target": {
            "id": target_id,
            "path": node_meta[target_id]["path"],
        },
        "neighbors": neighbors,
        "truncated": truncated,
    }


def neighbors_from_scan(
    result: ScanResult,
    target: str,
    *,
    depth: int,
    direction: str,
    limit: int,
) -> dict[str, Any]:
    node_meta = {
        node.id: {
            "id": node.id,
            "path": node.path,
            "kind": node.kind,
            "title": node.title,
            "updated": _date_property(node.properties, "updated"),
        }
        for node in result.nodes
    }
    edges = [
        (edge.source, edge.target, edge.relation)
        for edge in result.edges
        if edge.target in node_meta
    ]
    return compute_neighbors(
        node_meta=node_meta,
        edges=edges,
        target=target,
        depth=depth,
        direction=direction,
        limit=limit,
    )


def open_index(root: Path) -> VaultIndex | None:
    """Open and refresh the cache; return ``None`` on any cache failure."""
    try:
        index = VaultIndex.open(root)
    except CacheError:
        return None
    try:
        index.refresh()
    except CacheError:
        index.close()
        return None
    return index


def rebuild_index(root: Path) -> VaultIndex:
    """Discard any existing cache and rebuild it from a full parse."""
    manifest = load_manifest(root)
    db_path = cache_path_for(manifest.root)
    VaultIndex._remove_database(db_path)
    index = VaultIndex.open(root, manifest=manifest)
    index.refresh(force=True)
    return index


def index_status(root: Path, *, pending: bool = True) -> dict[str, Any]:
    """Describe the cache for ``index status`` and ``doctor`` payloads."""
    manifest = load_manifest(root)
    db_path = cache_path_for(manifest.root)
    status: dict[str, Any] = {
        "exists": db_path.is_file(),
        "path": str(db_path),
    }
    if not status["exists"]:
        return status
    try:
        status["sizeBytes"] = db_path.stat().st_size
    except OSError:
        status["sizeBytes"] = None
    try:
        index = VaultIndex.open(root, manifest=manifest)
    except CacheError as exc:
        status["readable"] = False
        status["error"] = str(exc)
        return status
    try:
        meta = index.meta()
        counts = index.counts()
        expected = index._expected_meta()
        status["readable"] = True
        status["current"] = all(
            meta.get(key) == value for key, value in expected.items() if key != "root"
        )
        status["engineVersion"] = meta.get("engineVersion")
        status["manifestDigest"] = meta.get("manifestDigest")
        status["updatedAt"] = meta.get("updatedAt")
        status["files"] = counts["files"]
        status["nodes"] = counts["nodes"]
        status["edges"] = counts["edges"]
        status["terms"] = counts["terms"]
        status["postings"] = counts["postings"]
        if pending:
            walked = walk_vault_files(manifest)
            stored = {
                path: FileStat(mtime_ns, size, bool(escape))
                for path, mtime_ns, size, escape in index._connection.execute(
                    "SELECT path, mtime_ns, size, escaped FROM files"
                )
            }
            status["pendingChanges"] = {
                "changed": sum(
                    1 for path, item in walked.items() if stored.get(path) != item
                ),
                "removed": sum(1 for path in stored if path not in walked),
            }
    except sqlite3.Error as exc:
        status["readable"] = False
        status["error"] = str(exc)
    finally:
        index.close()
    return status
