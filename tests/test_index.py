from __future__ import annotations

import json
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path
from time import perf_counter

import vaultctl.index
from vaultctl.cli import main
from vaultctl.engine import scan_vault
from vaultctl.index import cache_path_for, open_index
from vaultctl.search import search

NOTE = "---\ntags: []\nrelated: []\n---\n"


def _build(root: Path) -> None:
    index = open_index(root)
    assert index is not None
    index.close()


def _payload(capsys, argv: list[str]) -> tuple[int, dict]:
    exit_code = main(argv)
    return exit_code, json.loads(capsys.readouterr().out)


def test_cached_scan_result_matches_full_scan(make_vault) -> None:
    root = make_vault(
        notes={
            "notes/alpha.md": (
                "---\ntags: [demo]\nrelated: [notes/beta]\n---\n"
                "# Alpha\n\nSee [[notes/beta]] and [[notes/missing]].\n"
            ),
            "notes/beta.md": (
                "---\ntags: []\nrelated: []\nupdated: 2026-08-01\n---\n# Beta\n"
            ),
            "notes/встречи/созвон.md": (NOTE + "# Созвон\n\nОбсудили планы.\n"),
            "notes/broken.md": "---\ntags: [unclosed\n",
        }
    )

    plain = scan_vault(root)
    index = open_index(root)
    assert index is not None
    cached = index.scan_result()
    index.close()

    assert [node.to_dict() for node in plain.nodes] == [
        node.to_dict() for node in cached.nodes
    ]
    assert [node.body for node in plain.nodes] == [node.body for node in cached.nodes]
    assert [node.headings for node in plain.nodes] == [
        node.headings for node in cached.nodes
    ]
    assert [issue.to_dict() for issue in plain.issues] == [
        issue.to_dict() for issue in cached.issues
    ]


def test_incremental_refresh_reparses_only_changed_file(
    make_vault,
    monkeypatch,
) -> None:
    root = make_vault(
        notes={
            "notes/stable.md": NOTE + "# Stable\n\nUnchanged body.\n",
            "notes/edited.md": NOTE + "# Edited\n\nOriginal body.\n",
        }
    )
    _build(root)

    index = open_index(root)
    assert index is not None
    stored_before = dict(index._connection.execute("SELECT path, mtime_ns FROM files"))
    index.close()

    parsed_paths: list[str] = []
    real_parse = vaultctl.index.parse_markdown

    def spy(path, **kwargs):
        parsed_paths.append(Path(path).name)
        return real_parse(path, **kwargs)

    monkeypatch.setattr(vaultctl.index, "parse_markdown", spy)

    target = root / "notes" / "edited.md"
    target.write_text(NOTE + "# Edited\n\nChanged body text.\n", encoding="utf-8")

    index = open_index(root)
    assert index is not None
    stored_after = dict(index._connection.execute("SELECT path, mtime_ns FROM files"))
    index.close()

    assert parsed_paths == ["edited.md"]
    assert stored_after["notes/stable.md"] == stored_before["notes/stable.md"]
    assert stored_after["notes/edited.md"] != stored_before["notes/edited.md"]

    parsed_paths.clear()
    _build(root)
    assert parsed_paths == []


def test_removed_note_is_evicted_from_cache(make_vault) -> None:
    root = make_vault(
        notes={
            "notes/keep.md": NOTE + "# Keep\n\nDurable content.\n",
            "notes/gone.md": NOTE + "# Gone\n\nEphemeral xyzzy content.\n",
        }
    )
    _build(root)
    (root / "notes" / "gone.md").unlink()

    index = open_index(root)
    assert index is not None
    assert [node.path for node in index.scan_result().nodes] == ["notes/keep.md"]
    assert index.search_hits("xyzzy") == ()
    for table in ("files", "nodes", "postings"):
        count = index._connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE path = ?",  # noqa: S608
            ("notes/gone.md",),
        ).fetchone()[0]
        assert count == 0
    index.close()


def test_manifest_change_triggers_full_rebuild(make_vault, monkeypatch) -> None:
    root = make_vault(
        notes={
            "notes/one.md": NOTE + "# One\n",
            "notes/two.md": NOTE + "# Two\n",
        }
    )
    _build(root)

    manifest_path = root / ".vaultctl" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["search"] = {"stopWords": ["until"]}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    parsed_paths: list[str] = []
    real_parse = vaultctl.index.parse_markdown

    def spy(path, **kwargs):
        parsed_paths.append(Path(path).name)
        return real_parse(path, **kwargs)

    monkeypatch.setattr(vaultctl.index, "parse_markdown", spy)
    _build(root)
    assert sorted(parsed_paths) == ["one.md", "two.md"]


def test_corrupted_database_still_serves_correct_results(
    make_vault,
    capsys,
) -> None:
    root = make_vault(
        notes={"notes/example.md": NOTE + "# Example\n\nRelease planning notes.\n"}
    )
    _build(root)
    cache_path_for(root).write_bytes(b"this is not a sqlite database")

    exit_code, payload = _payload(capsys, ["--vault", str(root), "search", "release"])

    assert exit_code == 0
    assert payload["schemaVersion"] == "vaultctl.search/v1"
    assert payload["hits"][0]["path"] == "notes/example.md"


def test_search_context_query_read_parity_with_cache(make_vault, capsys) -> None:
    overrides = {"search": {"stemming": {"enabled": False}, "freshness": {"weight": 0}}}
    notes = {
        "notes/route-plan.md": (
            NOTE + "# Route plan\n\nRoute plan details and follow-up.\n"
        ),
        "notes/guide.md": NOTE + "# Guide\n\nUse when comparing a route plan.\n",
        "notes/broken.md": "---\ntags: [unclosed\n",
        "notes/встречи/созвон.md": NOTE + "# Созвон\n\nПлан созвона по маршруту.\n",
    }
    root = make_vault(manifest_overrides=overrides, notes=notes)

    for argv in (
        ["search", "route plan"],
        ["context", "route plan"],
        ["query", "--kind", "document"],
        ["read", "notes/guide.md"],
    ):
        _, cached = _payload(capsys, ["--vault", str(root), *argv])
        _, plain = _payload(capsys, ["--vault", str(root), "--no-cache", *argv])
        assert cached == plain, argv


def test_morphology_matches_stemmed_tokens(make_vault, capsys) -> None:
    root = make_vault(
        notes={
            "notes/встречи/созвон-итоги.md": (
                NOTE + "# Итоги\n\nВчера был созвон команды.\n"
            ),
            "notes/planning.md": NOTE + "# Roadmap\n\nQuarterly planning notes.\n",
            "notes/unrelated.md": NOTE + "# Other\n\nNothing else here.\n",
        }
    )

    exit_code, payload = _payload(capsys, ["--vault", str(root), "search", "созвоны"])
    assert exit_code == 0
    assert [hit["path"] for hit in payload["hits"]] == ["notes/встречи/созвон-итоги.md"]

    exit_code, payload = _payload(capsys, ["--vault", str(root), "search", "plans"])
    assert exit_code == 0
    assert [hit["path"] for hit in payload["hits"]] == ["notes/planning.md"]


def test_stemming_can_be_disabled(make_vault, capsys) -> None:
    root = make_vault(
        manifest_overrides={"search": {"stemming": {"enabled": False}}},
        notes={
            "notes/встречи/созвон-итоги.md": (
                NOTE + "# Итоги\n\nВчера был созвон команды.\n"
            )
        },
    )

    exit_code, payload = _payload(capsys, ["--vault", str(root), "search", "созвоны"])
    assert exit_code == 0
    assert payload["hits"] == []


def test_freshness_prefers_recent_note_on_equal_scores(make_vault, capsys) -> None:
    recent = date.today().isoformat()
    old = (date.today() - timedelta(days=1500)).isoformat()
    notes = {
        "notes/a-old.md": (
            f"---\ntags: []\nrelated: []\nupdated: {old}\n---\n"
            "# First\n\nShared focus keyword here.\n"
        ),
        "notes/z-new.md": (
            f"---\ntags: []\nrelated: []\nupdated: {recent}\n---\n"
            "# Second\n\nShared focus keyword here.\n"
        ),
    }

    root = make_vault(notes=notes)
    _, payload = _payload(capsys, ["--vault", str(root), "search", "focus"])
    assert [hit["path"] for hit in payload["hits"]] == [
        "notes/z-new.md",
        "notes/a-old.md",
    ]

    manifest_path = root / ".vaultctl" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["search"] = {"freshness": {"weight": 0}}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    _, payload = _payload(capsys, ["--vault", str(root), "search", "focus"])
    assert [hit["path"] for hit in payload["hits"]] == [
        "notes/a-old.md",
        "notes/z-new.md",
    ]


def _neighbors_vault(make_vault) -> Path:
    return make_vault(
        notes={
            "notes/hub.md": (
                "---\ntags: []\nrelated: [notes/spoke-a, notes/spoke-b]\n---\n# Hub\n"
            ),
            "notes/spoke-a.md": (NOTE + "# Spoke A\n\nSee [[notes/leaf]].\n"),
            "notes/spoke-b.md": NOTE + "# Spoke B\n",
            "notes/leaf.md": NOTE + "# Leaf\n",
        }
    )


def test_neighbors_depth_direction_and_limit(make_vault, capsys) -> None:
    root = _neighbors_vault(make_vault)

    _, payload = _payload(capsys, ["--vault", str(root), "neighbors", "notes/hub.md"])
    assert payload["schemaVersion"] == "vaultctl.neighbors/v1"
    assert payload["target"] == {"id": "notes/hub", "path": "notes/hub.md"}
    assert [item["path"] for item in payload["neighbors"]] == [
        "notes/spoke-a.md",
        "notes/spoke-b.md",
    ]
    assert payload["neighbors"][0]["distance"] == 1
    assert payload["neighbors"][0]["edges"] == [
        {"field": "related", "direction": "out"}
    ]

    _, payload = _payload(
        capsys,
        ["--vault", str(root), "neighbors", "notes/hub", "--depth", "2"],
    )
    by_path = {item["path"]: item for item in payload["neighbors"]}
    assert by_path["notes/leaf.md"]["distance"] == 2
    assert by_path["notes/leaf.md"]["edges"] == [{"field": "link", "direction": "out"}]

    _, payload = _payload(
        capsys,
        ["--vault", str(root), "neighbors", "notes/spoke-a", "--direction", "in"],
    )
    assert [item["path"] for item in payload["neighbors"]] == ["notes/hub.md"]
    assert payload["neighbors"][0]["edges"] == [{"field": "related", "direction": "in"}]

    _, payload = _payload(
        capsys,
        ["--vault", str(root), "neighbors", "notes/spoke-a", "--direction", "out"],
    )
    assert [item["path"] for item in payload["neighbors"]] == ["notes/leaf.md"]

    _, payload = _payload(
        capsys,
        ["--vault", str(root), "neighbors", "notes/hub", "--limit", "1"],
    )
    assert len(payload["neighbors"]) == 1
    assert payload["truncated"] is True


def test_neighbors_cache_and_scan_paths_agree(make_vault, capsys) -> None:
    root = _neighbors_vault(make_vault)
    argv = ["neighbors", "notes/hub", "--depth", "2"]

    _, cached = _payload(capsys, ["--vault", str(root), *argv])
    _, plain = _payload(capsys, ["--vault", str(root), "--no-cache", *argv])
    assert cached == plain


def test_neighbors_rejects_bad_arguments_and_missing_target(
    make_vault,
    capsys,
) -> None:
    root = _neighbors_vault(make_vault)

    exit_code, payload = _payload(
        capsys,
        ["--vault", str(root), "neighbors", "notes/hub", "--depth", "4"],
    )
    assert exit_code == 2
    assert payload["schemaVersion"] == "vaultctl.error/v1"

    exit_code, payload = _payload(
        capsys,
        ["--vault", str(root), "neighbors", "notes/missing.md"],
    )
    assert exit_code == 2
    assert "notes/missing.md" in payload["error"]


def test_index_status_and_rebuild_cli(make_vault, capsys) -> None:
    root = make_vault(notes={"notes/example.md": NOTE + "# Example\n"})

    exit_code, payload = _payload(capsys, ["--vault", str(root), "index", "status"])
    assert exit_code == 0
    assert payload["schemaVersion"] == "vaultctl.index-status/v1"
    assert payload["cache"]["exists"] is False

    exit_code, payload = _payload(capsys, ["--vault", str(root), "index", "rebuild"])
    assert exit_code == 0
    cache = payload["cache"]
    assert cache["exists"] is True
    assert cache["readable"] is True
    assert cache["current"] is True
    assert cache["nodes"] == 1
    assert cache["terms"] > 0
    assert cache["pendingChanges"] == {"changed": 0, "removed": 0}
    assert Path(cache["path"]) == cache_path_for(root)
    assert root not in Path(cache["path"]).parents


def test_doctor_reports_cache_state(make_vault, capsys) -> None:
    root = make_vault(notes={"notes/example.md": NOTE + "# Example\n"})

    exit_code, payload = _payload(capsys, ["--vault", str(root), "doctor"])
    assert exit_code == 0
    assert payload["cache"]["present"] is False

    _build(root)
    exit_code, payload = _payload(capsys, ["--vault", str(root), "doctor"])
    assert exit_code == 0
    assert payload["cache"]["present"] is True
    assert payload["cache"]["current"] is True


def test_no_cache_flag_skips_cache_writes(make_vault, capsys) -> None:
    root = make_vault(notes={"notes/example.md": NOTE + "# Example\n\nSearchable.\n"})

    exit_code = main(["--vault", str(root), "--no-cache", "search", "searchable"])
    capsys.readouterr()

    assert exit_code == 0
    assert not cache_path_for(root).exists()


def test_cache_database_stays_outside_the_vault(make_vault) -> None:
    root = make_vault(notes={"notes/example.md": NOTE + "# Example\n"})
    _build(root)

    assert root not in cache_path_for(root).parents
    inside = [
        path
        for path in root.rglob("*")
        if path.suffix in {".db", ".sqlite"} or path.name.startswith("index.db")
    ]
    assert inside == []


def test_warm_cache_is_faster_than_cold_scan(make_vault) -> None:
    notes = {}
    for number in range(300):
        notes[f"notes/note-{number:03d}.md"] = (
            f"---\ntags: [topic{number % 7}]\nrelated: []\n---\n"
            f"# Note {number}\n\n"
            f"Release planning details for iteration {number}. "
            "Shared vocabulary body line about routes and plans.\n"
        )
    root = make_vault(notes=notes)

    started = perf_counter()
    result = scan_vault(root)
    search(result, "release plan")
    cold = perf_counter() - started

    _build(root)

    started = perf_counter()
    index = open_index(root)
    assert index is not None
    hits = index.search_hits("release plan")
    index.close()
    warm = perf_counter() - started

    assert hits
    assert warm < cold


def test_touch_without_content_change_is_cheap_and_stable(make_vault) -> None:
    root = make_vault(notes={"notes/example.md": NOTE + "# Example\n\nStable body.\n"})
    _build(root)

    target = root / "notes" / "example.md"
    stat = target.stat()
    time.sleep(0.01)
    target.touch()
    assert target.stat().st_mtime_ns != stat.st_mtime_ns

    index = open_index(root)
    assert index is not None
    assert [node.path for node in index.scan_result().nodes] == ["notes/example.md"]
    index.close()


def test_open_index_survives_concurrent_schema(make_vault) -> None:
    root = make_vault(notes={"notes/example.md": NOTE + "# Example\n"})
    _build(root)

    with sqlite3.connect(cache_path_for(root)) as connection:
        connection.execute("UPDATE meta SET value = '0' WHERE key = 'schemaVersion'")
        connection.commit()

    index = open_index(root)
    assert index is not None
    assert [node.path for node in index.scan_result().nodes] == ["notes/example.md"]
    index.close()
