from __future__ import annotations

import difflib
import fcntl
import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from jsonschema import Draft202012Validator

from vaultctl import __version__
from vaultctl.engine import scan_vault
from vaultctl.errors import MutationError
from vaultctl.manifest import load_manifest
from vaultctl.markdown import parse_markdown_bytes, render_markdown_candidate
from vaultctl.merge import _digest, manifest_digest
from vaultctl.model import (
    MutationCandidate,
    MutationFileState,
    MutationPlan,
    MutationPrecondition,
    MutationValidation,
    NodeMutationReceipt,
    NodeMutationRequest,
    ScanResult,
    ValidationIssue,
    VaultManifest,
)

NODE_MUTATION_REQUEST_SCHEMA_VERSION = "vaultctl.node-mutation-request/v1"
NODE_MUTATION_PLAN_SCHEMA_VERSION = "vaultctl.node-mutation-plan/v1"
NODE_MUTATION_RECEIPT_SCHEMA_VERSION = "vaultctl.node-mutation-receipt/v1"
NODE_APPLY_CAPABILITY = "node-mutation-apply"


@dataclass(frozen=True)
class _PreparedNodeMutation:
    manifest: VaultManifest
    target: Path
    before: bytes
    candidate: bytes
    validation: MutationValidation


def _source_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


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
        raise MutationError(
            "node mutation path must be a normalized vault-relative Markdown path"
        )
    return path


def _schema(name: str) -> dict[str, Any]:
    path = files("vaultctl").joinpath(f"schemas/{name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_payload(
    payload: dict[str, Any],
    *,
    schema_name: str,
    label: str,
) -> None:
    validator = Draft202012Validator(_schema(schema_name))
    errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.path))
    if not errors:
        return
    messages = []
    for error in errors:
        location = ".".join(str(item) for item in error.path) or "<root>"
        messages.append(f"{location}: {error.message}")
    raise MutationError(f"invalid {label}:\n- " + "\n- ".join(messages))


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except OSError as exc:
        raise MutationError(f"cannot read {label}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise MutationError(
            f"invalid {label} JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc
    if not isinstance(payload, dict):
        raise MutationError(f"{label} must be a JSON object")
    return payload


def node_mutation_request_from_dict(
    payload: dict[str, Any],
) -> NodeMutationRequest:
    _validate_payload(
        payload,
        schema_name="node-mutation-request-v1.schema.json",
        label="node mutation request",
    )
    operation = payload["operation"]
    if operation == "create":
        document = payload["document"]
        properties = document["properties"]
        remove_properties: tuple[str, ...] = ()
        body = document["body"]
        expected_source_hash = None
    else:
        changes = payload["changes"]
        properties = changes.get("setProperties", {})
        remove_properties = tuple(changes.get("removeProperties", ()))
        body = changes.get("body")
        expected_source_hash = payload["expectedSourceHash"]

    overlap = sorted(set(properties) & set(remove_properties))
    if overlap:
        raise MutationError(
            "update cannot set and remove the same properties: " + ", ".join(overlap)
        )
    return NodeMutationRequest(
        schema_version=payload["schemaVersion"],
        operation=operation,
        path=_validate_logical_path(payload["path"]),
        kind=payload["kind"],
        properties=properties,
        remove_properties=remove_properties,
        body=body,
        expected_source_hash=expected_source_hash,
    )


def load_node_mutation_request(path: Path) -> NodeMutationRequest:
    return node_mutation_request_from_dict(
        _load_json(path, label="node mutation request")
    )


def _validation_from_dict(payload: dict[str, Any]) -> MutationValidation:
    return MutationValidation(
        valid=payload["valid"],
        vault_digest=payload["vaultDigest"],
        summary=payload["summary"],
        issues=tuple(
            ValidationIssue(
                level=issue["level"],
                code=issue["code"],
                message=issue["message"],
                path=issue.get("path"),
            )
            for issue in payload["issues"]
        ),
    )


def node_mutation_plan_from_dict(payload: dict[str, Any]) -> MutationPlan:
    _validate_payload(
        payload,
        schema_name="node-mutation-plan-v1.schema.json",
        label="node mutation plan",
    )
    plan_core = dict(payload)
    plan_id = plan_core.pop("planId")
    if _digest(plan_core) != plan_id:
        raise MutationError("node mutation plan digest does not match planId")

    candidate_payload = payload["candidate"]
    source = candidate_payload["source"]
    if _source_hash(source.encode("utf-8")) != candidate_payload["sourceHash"]:
        raise MutationError("node mutation candidate digest does not match sourceHash")
    precondition_payload = payload["precondition"]
    return MutationPlan(
        schema_version=payload["schemaVersion"],
        plan_id=plan_id,
        vault_id=payload["vaultId"],
        operation=payload["operation"],
        path=_validate_logical_path(payload["path"]),
        kind=payload["kind"],
        engine_version=payload["engineVersion"],
        manifest_digest=payload["manifestDigest"],
        precondition=MutationPrecondition(
            exists=precondition_payload["exists"],
            source_hash=precondition_payload.get("sourceHash"),
        ),
        candidate=MutationCandidate(
            source=source,
            source_hash=candidate_payload["sourceHash"],
        ),
        diff=payload["diff"],
        state=payload["state"],
        validation=_validation_from_dict(payload["validation"]),
    )


def load_node_mutation_plan(path: Path) -> MutationPlan:
    return node_mutation_plan_from_dict(_load_json(path, label="node mutation plan"))


def _target_path(
    manifest: VaultManifest,
    logical_path: str,
    *,
    must_exist: bool,
) -> Path:
    root = manifest.root.resolve()
    relative = PurePosixPath(_validate_logical_path(logical_path))
    target = root.joinpath(*relative.parts)
    if target.is_symlink():
        raise MutationError("node mutation rejects symlinked targets and parent paths")
    try:
        resolved = target.resolve(strict=must_exist)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise MutationError(
            "node mutation target must remain inside the vault without "
            "symlinked parents"
        ) from exc
    if resolved != target.absolute():
        raise MutationError("node mutation rejects symlinked targets and parent paths")
    if must_exist and not target.is_file():
        raise MutationError("update target must be an existing regular file")
    if not must_exist and not target.parent.is_dir():
        raise MutationError("create target parent must be an existing directory")
    if not must_exist and (target.exists() or target.is_symlink()):
        raise MutationError("create target already exists")
    return target


def _read_target(target: Path) -> bytes:
    try:
        return target.read_bytes()
    except OSError as exc:
        raise MutationError("cannot read node mutation target") from exc


def _scan_digest(
    result: ScanResult,
    issues: tuple[ValidationIssue, ...],
) -> str:
    return _digest(
        {
            "vaultId": result.manifest.vault_id,
            "manifestDigest": manifest_digest(result.manifest),
            "nodes": [
                {"path": node.path, "sourceHash": node.source_hash}
                for node in result.nodes
            ],
            "edges": [edge.to_dict() for edge in result.edges],
            "issues": [issue.to_dict() for issue in issues],
        }
    )


def _validation_from_scan(
    result: ScanResult,
    *,
    path: str,
    kind: str,
) -> MutationValidation:
    issues = list(result.issues)
    candidate_node = next((node for node in result.nodes if node.path == path), None)
    if candidate_node is not None and candidate_node.kind != kind:
        issues.append(
            ValidationIssue(
                level="error",
                code="node.kind-mismatch",
                message=(
                    f"candidate classifies as {candidate_node.kind!r}, "
                    f"not requested kind {kind!r}"
                ),
                path=path,
            )
        )
    ordered_issues = tuple(
        sorted(
            issues,
            key=lambda issue: (
                issue.path or "",
                issue.level,
                issue.code,
                issue.message,
            ),
        )
    )
    errors = tuple(issue for issue in ordered_issues if issue.level == "error")
    warnings = tuple(issue for issue in ordered_issues if issue.level == "warning")
    return MutationValidation(
        valid=not errors,
        vault_digest=_scan_digest(result, ordered_issues),
        summary={
            "nodes": len(result.nodes),
            "edges": len(result.edges),
            "errors": len(errors),
            "warnings": len(warnings),
        },
        issues=ordered_issues,
    )


def _prospective_validation(
    manifest: VaultManifest,
    *,
    operation: str,
    path: str,
    kind: str,
    candidate: bytes,
) -> MutationValidation:
    if operation == "create":
        result = scan_vault(
            manifest.root,
            create_overlays={path: candidate},
        )
    else:
        result = scan_vault(
            manifest.root,
            overlays={path: candidate},
        )
    if manifest_digest(result.manifest) != manifest_digest(manifest):
        raise MutationError("vault manifest changed during prospective validation")
    return _validation_from_scan(result, path=path, kind=kind)


def _unified_diff(operation: str, path: str, before: bytes, candidate: bytes) -> str:
    before_name = "/dev/null" if operation == "create" else f"a/{path}"
    lines = difflib.unified_diff(
        before.decode("utf-8").splitlines(keepends=True),
        candidate.decode("utf-8").splitlines(keepends=True),
        fromfile=before_name,
        tofile=f"b/{path}",
    )
    rendered = []
    for line in lines:
        rendered.append(line)
        if not line.endswith("\n"):
            rendered.append("\n\\ No newline at end of file\n")
    return "".join(rendered)


def _current_kind(manifest: VaultManifest, path: str) -> str:
    result = scan_vault(manifest.root)
    node = next((item for item in result.nodes if item.path == path), None)
    if node is None:
        raise MutationError("update target is not an included, parseable vault node")
    return node.kind


def plan_node_mutation(
    manifest: VaultManifest,
    request: NodeMutationRequest,
) -> MutationPlan:
    if request.schema_version != NODE_MUTATION_REQUEST_SCHEMA_VERSION:
        raise MutationError("unsupported node mutation request schema version")
    if request.operation not in {"create", "update"}:
        raise MutationError("node mutation operation must be create or update")
    if request.kind not in manifest.node_kinds:
        raise MutationError(
            f"node mutation kind {request.kind!r} is not defined in the manifest"
        )
    selected_manifest_digest = manifest_digest(manifest)
    operation = request.operation
    target = _target_path(
        manifest,
        request.path,
        must_exist=operation == "update",
    )

    if operation == "create":
        before = b""
        properties = dict(request.properties)
        body = request.body
        if body is None:
            raise MutationError("create request requires a complete document body")
        precondition = MutationPrecondition(exists=False)
    else:
        before = _read_target(target)
        before_hash = _source_hash(before)
        if before_hash != request.expected_source_hash:
            raise MutationError("update target hash does not match expectedSourceHash")
        current_kind = _current_kind(manifest, request.path)
        if current_kind != request.kind:
            raise MutationError(
                f"update target classifies as {current_kind!r}, "
                f"not requested kind {request.kind!r}"
            )
        parsed = parse_markdown_bytes(
            before,
            display_path=request.path,
            fallback_stem=target.stem,
            allow_legacy_colon_scalars=manifest.allow_legacy_colon_scalars,
        )
        missing = sorted(
            name for name in request.remove_properties if name not in parsed.properties
        )
        if missing:
            raise MutationError(
                "update cannot remove missing properties: " + ", ".join(missing)
            )
        properties = dict(parsed.properties)
        for name in request.remove_properties:
            del properties[name]
        properties.update(request.properties)
        body = parsed.body if request.body is None else request.body
        precondition = MutationPrecondition(exists=True, source_hash=before_hash)

    candidate_bytes = render_markdown_candidate(
        before,
        properties=properties,
        body=body,
        display_path=request.path,
        allow_legacy_colon_scalars=manifest.allow_legacy_colon_scalars,
    )
    if candidate_bytes == before:
        raise MutationError("node mutation request produces no changes")
    candidate = MutationCandidate(
        source=candidate_bytes.decode("utf-8"),
        source_hash=_source_hash(candidate_bytes),
    )
    validation = _prospective_validation(
        manifest,
        operation=operation,
        path=request.path,
        kind=request.kind,
        candidate=candidate_bytes,
    )

    live_manifest = load_manifest(manifest.root)
    if manifest_digest(live_manifest) != selected_manifest_digest:
        raise MutationError("selected vault manifest changed while planning")
    if operation == "create":
        _target_path(live_manifest, request.path, must_exist=False)
    else:
        live_target = _target_path(live_manifest, request.path, must_exist=True)
        if _read_target(live_target) != before:
            raise MutationError("update target changed while planning")

    diff = _unified_diff(operation, request.path, before, candidate_bytes)
    state = "ready" if validation.valid else "invalid"
    plan_core = {
        "schemaVersion": NODE_MUTATION_PLAN_SCHEMA_VERSION,
        "vaultId": manifest.vault_id,
        "operation": operation,
        "path": request.path,
        "kind": request.kind,
        "engineVersion": __version__,
        "manifestDigest": selected_manifest_digest,
        "precondition": precondition.to_dict(),
        "candidate": candidate.to_dict(),
        "diff": diff,
        "state": state,
        "validation": validation.to_dict(),
    }
    return MutationPlan(
        schema_version=NODE_MUTATION_PLAN_SCHEMA_VERSION,
        plan_id=_digest(plan_core),
        vault_id=manifest.vault_id,
        operation=operation,
        path=request.path,
        kind=request.kind,
        engine_version=__version__,
        manifest_digest=selected_manifest_digest,
        precondition=precondition,
        candidate=candidate,
        diff=diff,
        state=state,
        validation=validation,
    )


def _prepare_live_plan(
    manifest: VaultManifest,
    plan: MutationPlan,
) -> _PreparedNodeMutation:
    node_mutation_plan_from_dict(plan.to_dict())
    live_manifest = load_manifest(manifest.root)
    if manifest_digest(live_manifest) != manifest_digest(manifest):
        raise MutationError("selected vault manifest changed before plan verification")
    if plan.vault_id != live_manifest.vault_id:
        raise MutationError("node mutation plan vaultId does not match selected vault")
    if plan.engine_version != __version__:
        raise MutationError(
            "node mutation plan engine version does not match this vaultctl"
        )
    if plan.manifest_digest != manifest_digest(live_manifest):
        raise MutationError("node mutation plan manifest digest is stale")
    if plan.kind not in live_manifest.node_kinds:
        raise MutationError("node mutation plan kind is not defined in the manifest")

    must_exist = plan.operation == "update"
    target = _target_path(live_manifest, plan.path, must_exist=must_exist)
    before = b"" if plan.operation == "create" else _read_target(target)
    if must_exist and _source_hash(before) != plan.precondition.source_hash:
        raise MutationError("node mutation target hash is stale")

    candidate = plan.candidate.source.encode("utf-8")
    validation = _prospective_validation(
        live_manifest,
        operation=plan.operation,
        path=plan.path,
        kind=plan.kind,
        candidate=candidate,
    )
    if validation.to_dict() != plan.validation.to_dict():
        raise MutationError("prospective vault state no longer matches the plan")
    if _unified_diff(plan.operation, plan.path, before, candidate) != plan.diff:
        raise MutationError("node mutation diff no longer matches the plan")
    if plan.operation == "create":
        _target_path(live_manifest, plan.path, must_exist=False)
    elif _read_target(target) != before:
        raise MutationError("node mutation target changed during plan verification")
    return _PreparedNodeMutation(
        manifest=live_manifest,
        target=target,
        before=before,
        candidate=candidate,
        validation=validation,
    )


def render_node_mutation_plan(
    manifest: VaultManifest,
    plan: MutationPlan,
) -> bytes:
    return _prepare_live_plan(manifest, plan).candidate


def diff_node_mutation_plan(
    manifest: VaultManifest,
    plan: MutationPlan,
) -> str:
    _prepare_live_plan(manifest, plan)
    return plan.diff


@contextmanager
def _write_lock(manifest: VaultManifest) -> Iterator[None]:
    lock_path = manifest.root / ".vaultctl" / "manifest.json"
    try:
        lock_file: BinaryIO = lock_path.open("rb")
    except OSError as exc:
        raise MutationError("cannot open the node mutation write lock") from exc
    try:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise MutationError("node mutation write lock is busy") from exc
        yield
    finally:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


def _stage_bytes(target: Path, content: bytes, mode: int) -> Path:
    try:
        descriptor, name = tempfile.mkstemp(
            prefix=f".{target.name}.vaultctl-",
            suffix=".tmp",
            dir=target.parent,
        )
    except OSError as exc:
        raise MutationError("cannot stage node mutation candidate") from exc
    staged = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception as exc:
        staged.unlink(missing_ok=True)
        raise MutationError("cannot stage node mutation candidate") from exc
    return staged


def _atomic_update(staged: Path, target: Path) -> None:
    os.replace(staged, target)


def _atomic_create(staged: Path, target: Path) -> None:
    os.link(staged, target, follow_symlinks=False)
    staged.unlink()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _file_state(target: Path) -> MutationFileState:
    if target.is_symlink():
        raise MutationError("node mutation target became a symlink")
    try:
        content = target.read_bytes()
    except FileNotFoundError:
        return MutationFileState(exists=False)
    except OSError as exc:
        raise MutationError("cannot inspect node mutation target state") from exc
    if not target.is_file():
        raise MutationError("node mutation target is not a regular file")
    return MutationFileState(exists=True, source_hash=_source_hash(content))


def _receipt(
    *,
    plan: MutationPlan,
    before: MutationFileState,
    after: MutationFileState,
    state: str,
    error: str | None = None,
) -> NodeMutationReceipt:
    plan_digest = _digest(plan.to_dict())
    validation_digest = _digest(plan.validation.to_dict())
    operation_id = _digest(
        {
            "planDigest": plan_digest,
            "validationDigest": validation_digest,
            "before": before.to_dict(),
            "after": after.to_dict(),
            "state": state,
            "error": error,
        }
    )
    return NodeMutationReceipt(
        schema_version=NODE_MUTATION_RECEIPT_SCHEMA_VERSION,
        operation_id=operation_id,
        plan_id=plan.plan_id,
        plan_digest=plan_digest,
        vault_id=plan.vault_id,
        operation=plan.operation,
        path=plan.path,
        state=state,
        before=before,
        after=after,
        candidate_source_hash=plan.candidate.source_hash,
        manifest_digest=plan.manifest_digest,
        engine_version=plan.engine_version,
        validation_digest=validation_digest,
        error=error,
    )


def _failure_message(exc: Exception) -> str:
    if isinstance(exc, MutationError):
        return str(exc)
    return f"{type(exc).__name__}: local atomic mutation failed"


def _rollback(
    prepared: _PreparedNodeMutation,
    *,
    before: MutationFileState,
    mode: int,
) -> MutationFileState:
    current = _file_state(prepared.target)
    candidate_state = MutationFileState(
        exists=True,
        source_hash=_source_hash(prepared.candidate),
    )
    if current != candidate_state:
        raise MutationError(
            "node mutation failed after an unexpected concurrent target change; "
            "automatic rollback is unsafe"
        )

    if before.exists:
        rollback = _stage_bytes(prepared.target, prepared.before, mode)
        try:
            _atomic_update(rollback, prepared.target)
            _fsync_directory(prepared.target.parent)
        except Exception as exc:
            raise MutationError(
                "node mutation failed and rollback could not restore the target"
            ) from exc
        finally:
            rollback.unlink(missing_ok=True)
    else:
        try:
            prepared.target.unlink()
            _fsync_directory(prepared.target.parent)
        except Exception as exc:
            raise MutationError(
                "node mutation failed and rollback could not remove the created target"
            ) from exc

    final = _file_state(prepared.target)
    if final != before:
        raise MutationError("node mutation rollback did not restore the original state")
    return final


def apply_node_mutation_plan(
    manifest: VaultManifest,
    plan: MutationPlan,
) -> NodeMutationReceipt:
    if plan.state != "ready" or not plan.validation.valid:
        raise MutationError("only a ready, valid node mutation plan can be applied")

    with _write_lock(manifest):
        prepared = _prepare_live_plan(manifest, plan)
        if NODE_APPLY_CAPABILITY not in prepared.manifest.capabilities:
            raise MutationError(
                f"node apply requires manifest capability {NODE_APPLY_CAPABILITY!r}"
            )

        before = MutationFileState(
            exists=plan.operation == "update",
            source_hash=(
                _source_hash(prepared.before) if plan.operation == "update" else None
            ),
        )
        mode = (
            stat.S_IMODE(prepared.target.stat().st_mode)
            if plan.operation == "update"
            else stat.S_IMODE(prepared.target.parent.stat().st_mode) & 0o666
        )
        staged = _stage_bytes(prepared.target, prepared.candidate, mode or 0o600)
        try:
            try:
                if plan.operation == "create":
                    _atomic_create(staged, prepared.target)
                else:
                    _atomic_update(staged, prepared.target)
                _fsync_directory(prepared.target.parent)

                applied_state = _file_state(prepared.target)
                expected_state = MutationFileState(
                    exists=True,
                    source_hash=_source_hash(prepared.candidate),
                )
                if applied_state != expected_state:
                    raise MutationError(
                        "atomic node mutation produced an unexpected target state"
                    )

                result = scan_vault(prepared.manifest.root)
                actual_validation = _validation_from_scan(
                    result,
                    path=plan.path,
                    kind=plan.kind,
                )
                if actual_validation.to_dict() != prepared.validation.to_dict():
                    raise MutationError(
                        "post-apply vault state differs from prospective validation"
                    )
            except Exception as exc:
                current = _file_state(prepared.target)
                if current == before:
                    return _receipt(
                        plan=plan,
                        before=before,
                        after=current,
                        state="failed",
                        error=_failure_message(exc),
                    )
                final = _rollback(prepared, before=before, mode=mode or 0o600)
                return _receipt(
                    plan=plan,
                    before=before,
                    after=final,
                    state="rolled-back",
                    error=_failure_message(exc),
                )

            return _receipt(
                plan=plan,
                before=before,
                after=applied_state,
                state="applied",
            )
        finally:
            staged.unlink(missing_ok=True)
