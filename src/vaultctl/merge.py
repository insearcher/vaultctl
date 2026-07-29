from __future__ import annotations

import hashlib
import json
import re
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator

from vaultctl import __version__
from vaultctl.errors import MergeError
from vaultctl.markdown import ParsedMarkdown, parse_markdown
from vaultctl.model import (
    Conflict,
    MergeCandidate,
    MergeDecision,
    MergeInput,
    MergePlan,
    VaultManifest,
)

MERGE_PLAN_SCHEMA_VERSION = "vaultctl.merge-plan/v1"
REVISION_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_MISSING = object()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _digest(value: Any) -> str:
    encoded = _canonical_json(value).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _values_equal(left: Any, right: Any) -> bool:
    if left is _MISSING or right is _MISSING:
        return left is right
    return _canonical_json(left) == _canonical_json(right)


def _value_snapshot(value: Any) -> dict[str, Any]:
    if value is _MISSING:
        return {"present": False}
    return {"present": True, "value": value}


def _body_snapshot(value: str) -> dict[str, Any]:
    return {
        "present": True,
        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
        "characters": len(value),
    }


def _validate_logical_path(path: str) -> str:
    candidate = PurePosixPath(path)
    if (
        not path
        or candidate.is_absolute()
        or candidate.suffix.lower() != ".md"
        or ".." in candidate.parts
        or "\\" in path
        or candidate.as_posix() != path
    ):
        raise MergeError("merge path must be a normalized vault-relative Markdown path")
    return path


def _validate_revision(name: str, revision: str) -> str:
    if REVISION_RE.fullmatch(revision) is None:
        raise MergeError(f"{name} revision must be a 40- or 64-character hex ID")
    return revision


def _strategy(manifest: VaultManifest, field: str) -> str:
    config = manifest.raw.get("merge", {})
    field_policy = config.get("fields", {}).get(field)
    if field_policy is not None:
        return field_policy["strategy"]
    return config.get("defaultFieldStrategy", "manual")


def _unique_values(values: list[Any]) -> tuple[list[Any], set[str]]:
    unique = []
    keys: set[str] = set()
    for value in values:
        key = _canonical_json(value)
        if key in keys:
            continue
        keys.add(key)
        unique.append(value)
    return unique, keys


def _merge_set_values(base: Any, ours: Any, theirs: Any) -> Any:
    values = (base, ours, theirs)
    if any(value is not _MISSING and not isinstance(value, list) for value in values):
        return _MISSING

    base_values, base_keys = _unique_values([] if base is _MISSING else base)
    ours_values, ours_keys = _unique_values([] if ours is _MISSING else ours)
    theirs_values, theirs_keys = _unique_values([] if theirs is _MISSING else theirs)

    survivors = [
        value
        for value in base_values
        if _canonical_json(value) in ours_keys and _canonical_json(value) in theirs_keys
    ]
    additions_by_key = {
        _canonical_json(value): value
        for value in (*ours_values, *theirs_values)
        if _canonical_json(value) not in base_keys
    }
    additions = [
        additions_by_key[key]
        for key in sorted(additions_by_key)
        if key not in {_canonical_json(value) for value in survivors}
    ]
    return [*survivors, *additions]


def _normalize_set_value(value: Any) -> Any:
    if value is _MISSING:
        return _MISSING
    unique, _ = _unique_values(value)
    return unique


def _resolve_value(
    *,
    base: Any,
    ours: Any,
    theirs: Any,
    strategy: str,
) -> tuple[Any, str] | None:
    if strategy == "set" and any(
        value is not _MISSING and not isinstance(value, list)
        for value in (base, ours, theirs)
    ):
        return None
    if _values_equal(ours, theirs):
        resolution = "unchanged" if _values_equal(ours, base) else "same-change"
        candidate = _normalize_set_value(ours) if strategy == "set" else ours
        return candidate, resolution
    if _values_equal(ours, base):
        candidate = _normalize_set_value(theirs) if strategy == "set" else theirs
        return candidate, "theirs"
    if _values_equal(theirs, base):
        candidate = _normalize_set_value(ours) if strategy == "set" else ours
        return candidate, "ours"
    if strategy == "set":
        merged = _merge_set_values(base, ours, theirs)
        if merged is not _MISSING:
            return merged, "set-merge"
    return None


def _conflict(
    *,
    kind: str,
    path: str,
    location: str,
    strategy: str,
    message: str,
    base: dict[str, Any],
    ours: dict[str, Any],
    theirs: dict[str, Any],
) -> Conflict:
    conflict_id = _digest(
        {
            "kind": kind,
            "path": path,
            "location": location,
        }
    )
    return Conflict(
        id=conflict_id,
        kind=kind,
        path=path,
        location=location,
        strategy=strategy,
        message=message,
        base=base,
        ours=ours,
        theirs=theirs,
    )


def _candidate_hash(properties: dict[str, Any], body: str) -> str:
    return _digest({"properties": properties, "body": body})


def manifest_digest(manifest: VaultManifest) -> str:
    return _digest(manifest.raw)


def merge_plan_digest(plan: MergePlan) -> str:
    return _digest(plan.to_dict())


def merge_plan_from_dict(payload: dict[str, Any]) -> MergePlan:
    schema_path = files("vaultctl").joinpath("schemas/merge-plan-v1.schema.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.path))
    if errors:
        messages = []
        for error in errors:
            location = ".".join(str(item) for item in error.path) or "<root>"
            messages.append(f"{location}: {error.message}")
        raise MergeError("invalid merge plan:\n- " + "\n- ".join(messages))

    plan_core = dict(payload)
    plan_id = plan_core.pop("planId")
    if _digest(plan_core) != plan_id:
        raise MergeError("merge plan digest does not match planId")

    candidate_payload = payload["candidate"]
    candidate = None
    if candidate_payload is not None:
        expected_hash = _candidate_hash(
            candidate_payload["properties"],
            candidate_payload["body"],
        )
        if expected_hash != candidate_payload["contentHash"]:
            raise MergeError("merge candidate digest does not match contentHash")
        candidate = MergeCandidate(
            properties=candidate_payload["properties"],
            body=candidate_payload["body"],
            content_hash=candidate_payload["contentHash"],
        )

    return MergePlan(
        schema_version=payload["schemaVersion"],
        plan_id=plan_id,
        vault_id=payload["vaultId"],
        path=payload["path"],
        engine_version=payload["engineVersion"],
        manifest_digest=payload["manifestDigest"],
        inputs={
            name: MergeInput(
                revision=value["revision"],
                source_hash=value["sourceHash"],
            )
            for name, value in payload["inputs"].items()
        },
        state=payload["state"],
        decisions=tuple(
            MergeDecision(
                location=value["location"],
                strategy=value["strategy"],
                resolution=value["resolution"],
                candidate=value["candidate"],
            )
            for value in payload["decisions"]
        ),
        conflicts=tuple(
            Conflict(
                id=value["id"],
                kind=value["kind"],
                path=value["path"],
                location=value["location"],
                strategy=value["strategy"],
                message=value["message"],
                base=value["base"],
                ours=value["ours"],
                theirs=value["theirs"],
            )
            for value in payload["conflicts"]
        ),
        candidate=candidate,
    )


def load_merge_plan(path: Path) -> MergePlan:
    try:
        payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except OSError as exc:
        raise MergeError(f"cannot read merge plan: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise MergeError(
            f"invalid merge plan JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc
    if not isinstance(payload, dict):
        raise MergeError("merge plan must be a JSON object")
    return merge_plan_from_dict(payload)


def plan_merge(
    manifest: VaultManifest,
    *,
    path: str,
    base: ParsedMarkdown,
    ours: ParsedMarkdown,
    theirs: ParsedMarkdown,
    base_revision: str,
    ours_revision: str,
    theirs_revision: str,
) -> MergePlan:
    logical_path = _validate_logical_path(path)
    revisions = {
        "base": _validate_revision("base", base_revision),
        "ours": _validate_revision("ours", ours_revision),
        "theirs": _validate_revision("theirs", theirs_revision),
    }
    current_manifest_digest = manifest_digest(manifest)
    decisions: list[MergeDecision] = []
    conflicts: list[Conflict] = []
    candidate_properties: dict[str, Any] = {}

    fields = sorted(
        set(base.properties) | set(ours.properties) | set(theirs.properties)
    )
    for field in fields:
        base_value = base.properties.get(field, _MISSING)
        ours_value = ours.properties.get(field, _MISSING)
        theirs_value = theirs.properties.get(field, _MISSING)
        strategy = _strategy(manifest, field)
        location = f"frontmatter.{field}"
        resolved = _resolve_value(
            base=base_value,
            ours=ours_value,
            theirs=theirs_value,
            strategy=strategy,
        )
        if resolved is None:
            kind = (
                "frontmatter.type-conflict"
                if strategy == "set"
                and any(
                    value is not _MISSING and not isinstance(value, list)
                    for value in (base_value, ours_value, theirs_value)
                )
                else "frontmatter.concurrent-change"
            )
            message = (
                "set strategy requires list values or a missing field"
                if kind == "frontmatter.type-conflict"
                else "concurrent frontmatter changes require explicit resolution"
            )
            conflicts.append(
                _conflict(
                    kind=kind,
                    path=logical_path,
                    location=location,
                    strategy=strategy,
                    message=message,
                    base=_value_snapshot(base_value),
                    ours=_value_snapshot(ours_value),
                    theirs=_value_snapshot(theirs_value),
                )
            )
            continue

        candidate_value, resolution = resolved
        if candidate_value is not _MISSING:
            candidate_properties[field] = candidate_value
        decisions.append(
            MergeDecision(
                location=location,
                strategy=strategy,
                resolution=resolution,
                candidate=_value_snapshot(candidate_value),
            )
        )

    body_strategy = manifest.raw.get("merge", {}).get("bodyStrategy", "manual")
    body_resolved = _resolve_value(
        base=base.body,
        ours=ours.body,
        theirs=theirs.body,
        strategy=body_strategy,
    )
    candidate_body = ""
    if body_resolved is None:
        conflicts.append(
            _conflict(
                kind="body.concurrent-change",
                path=logical_path,
                location="body",
                strategy=body_strategy,
                message="concurrent body changes require explicit resolution",
                base=_body_snapshot(base.body),
                ours=_body_snapshot(ours.body),
                theirs=_body_snapshot(theirs.body),
            )
        )
    else:
        candidate_body, resolution = body_resolved
        decisions.append(
            MergeDecision(
                location="body",
                strategy=body_strategy,
                resolution=resolution,
                candidate=_body_snapshot(candidate_body),
            )
        )

    candidate = None
    if not conflicts:
        candidate = MergeCandidate(
            properties=candidate_properties,
            body=candidate_body,
            content_hash=_candidate_hash(candidate_properties, candidate_body),
        )

    inputs = {
        "base": MergeInput(revision=revisions["base"], source_hash=base.source_hash),
        "ours": MergeInput(revision=revisions["ours"], source_hash=ours.source_hash),
        "theirs": MergeInput(
            revision=revisions["theirs"],
            source_hash=theirs.source_hash,
        ),
    }
    state = "conflict" if conflicts else "clean"
    plan_core = {
        "schemaVersion": MERGE_PLAN_SCHEMA_VERSION,
        "vaultId": manifest.vault_id,
        "path": logical_path,
        "engineVersion": __version__,
        "manifestDigest": current_manifest_digest,
        "inputs": {
            name: merge_input.to_dict() for name, merge_input in sorted(inputs.items())
        },
        "state": state,
        "decisions": [decision.to_dict() for decision in decisions],
        "conflicts": [conflict.to_dict() for conflict in conflicts],
        "candidate": candidate.to_dict() if candidate else None,
    }
    return MergePlan(
        schema_version=MERGE_PLAN_SCHEMA_VERSION,
        plan_id=_digest(plan_core),
        vault_id=manifest.vault_id,
        path=logical_path,
        engine_version=__version__,
        manifest_digest=current_manifest_digest,
        inputs=inputs,
        state=state,
        decisions=tuple(decisions),
        conflicts=tuple(conflicts),
        candidate=candidate,
    )


def plan_merge_files(
    manifest: VaultManifest,
    *,
    path: str,
    base_path: Path,
    ours_path: Path,
    theirs_path: Path,
    base_revision: str,
    ours_revision: str,
    theirs_revision: str,
) -> MergePlan:
    parsed = {
        name: parse_markdown(
            source_path.expanduser().resolve(),
            display_path=f"{name}:{path}",
            allow_legacy_colon_scalars=manifest.allow_legacy_colon_scalars,
        )
        for name, source_path in {
            "base": base_path,
            "ours": ours_path,
            "theirs": theirs_path,
        }.items()
    }
    return plan_merge(
        manifest,
        path=path,
        base=parsed["base"],
        ours=parsed["ours"],
        theirs=parsed["theirs"],
        base_revision=base_revision,
        ours_revision=ours_revision,
        theirs_revision=theirs_revision,
    )
