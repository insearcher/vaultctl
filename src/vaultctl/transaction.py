from __future__ import annotations

import fcntl
import hashlib
import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from vaultctl import __version__
from vaultctl.engine import scan_vault
from vaultctl.errors import MergeError
from vaultctl.manifest import load_manifest
from vaultctl.markdown import parse_markdown_bytes, render_markdown_candidate
from vaultctl.merge import (
    _digest,
    manifest_digest,
    merge_plan_digest,
    merge_plan_from_dict,
)
from vaultctl.model import (
    MergePlan,
    ProspectiveValidation,
    Receipt,
    ScanResult,
    VaultManifest,
)

MERGE_VALIDATION_SCHEMA_VERSION = "vaultctl.merge-validation/v1"
RECEIPT_SCHEMA_VERSION = "vaultctl.receipt/v1"
APPLY_CAPABILITY = "semantic-merge-apply"


@dataclass(frozen=True)
class _PreparedMerge:
    manifest: VaultManifest
    target: Path
    before: bytes
    candidate: bytes
    validation: ProspectiveValidation


def _source_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _scan_digest(result: ScanResult) -> str:
    return _digest(
        {
            "vaultId": result.manifest.vault_id,
            "manifestDigest": manifest_digest(result.manifest),
            "nodes": [
                {"path": node.path, "sourceHash": node.source_hash}
                for node in result.nodes
            ],
            "edges": [edge.to_dict() for edge in result.edges],
            "issues": [issue.to_dict() for issue in result.issues],
        }
    )


def _target_path(manifest: VaultManifest, logical_path: str) -> Path:
    root = manifest.root.resolve()
    relative = PurePosixPath(logical_path)
    target = root.joinpath(*relative.parts)
    if target.is_symlink():
        raise MergeError("merge apply rejects symlinked targets and parent paths")
    try:
        resolved = target.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise MergeError(
            "merge target must be an existing path inside the vault"
        ) from exc
    if resolved != target.absolute():
        raise MergeError("merge apply rejects symlinked targets and parent paths")
    if not target.is_file():
        raise MergeError("merge target must be an existing regular file")
    return target


def _verify_plan(manifest: VaultManifest, plan: MergePlan) -> Path:
    merge_plan_from_dict(plan.to_dict())
    if plan.state != "clean" or plan.candidate is None:
        raise MergeError("only a clean merge plan with a candidate can be validated")
    if plan.vault_id != manifest.vault_id:
        raise MergeError("merge plan vaultId does not match the selected vault")
    if plan.engine_version != __version__:
        raise MergeError("merge plan engine version does not match this vaultctl")
    if plan.manifest_digest != manifest_digest(manifest):
        raise MergeError("merge plan manifest digest is stale")
    return _target_path(manifest, plan.path)


def _prepare_merge(
    manifest: VaultManifest,
    plan: MergePlan,
) -> _PreparedMerge:
    live_manifest = load_manifest(manifest.root)
    if manifest_digest(live_manifest) != manifest_digest(manifest):
        raise MergeError("selected vault manifest changed before validation")
    target = _verify_plan(live_manifest, plan)
    try:
        before = target.read_bytes()
    except OSError as exc:
        raise MergeError("cannot read merge target") from exc
    if _source_hash(before) != plan.inputs["ours"].source_hash:
        raise MergeError("merge target hash no longer matches the plan's ours input")

    candidate = render_markdown_candidate(
        before,
        properties=plan.candidate.properties,
        body=plan.candidate.body,
        display_path=plan.path,
        allow_legacy_colon_scalars=live_manifest.allow_legacy_colon_scalars,
    )
    parsed = parse_markdown_bytes(
        candidate,
        display_path=plan.path,
        fallback_stem=target.stem,
        allow_legacy_colon_scalars=live_manifest.allow_legacy_colon_scalars,
    )
    rendered_candidate_digest = _digest(
        {
            "properties": parsed.properties,
            "body": parsed.body,
        }
    )
    if rendered_candidate_digest != plan.candidate.content_hash:
        raise MergeError("rendered Markdown does not reproduce the merge candidate")

    result = scan_vault(live_manifest.root, overlays={plan.path: candidate})
    if manifest_digest(result.manifest) != plan.manifest_digest:
        raise MergeError("vault manifest changed during prospective validation")
    try:
        current = target.read_bytes()
    except OSError as exc:
        raise MergeError(
            "merge target became unavailable during prospective validation"
        ) from exc
    if current != before:
        raise MergeError("merge target changed during prospective validation")
    validation = ProspectiveValidation(
        schema_version=MERGE_VALIDATION_SCHEMA_VERSION,
        vault_id=live_manifest.vault_id,
        plan_id=plan.plan_id,
        path=plan.path,
        valid=not result.errors,
        candidate_source_hash=_source_hash(candidate),
        vault_digest=_scan_digest(result),
        summary={
            "nodes": len(result.nodes),
            "edges": len(result.edges),
            "errors": len(result.errors),
            "warnings": len(result.warnings),
        },
        issues=result.issues,
    )
    return _PreparedMerge(
        manifest=live_manifest,
        target=target,
        before=before,
        candidate=candidate,
        validation=validation,
    )


def validate_merge_plan(
    manifest: VaultManifest,
    plan: MergePlan,
) -> ProspectiveValidation:
    return _prepare_merge(manifest, plan).validation


@contextmanager
def _write_lock(manifest: VaultManifest) -> Iterator[None]:
    lock_path = manifest.root / ".vaultctl" / "manifest.json"
    try:
        lock_file: BinaryIO = lock_path.open("rb")
    except OSError as exc:
        raise MergeError("cannot open the vault write lock") from exc
    try:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise MergeError("vault write lock is busy") from exc
        yield
    finally:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


def _stage_bytes(target: Path, content: bytes, mode: int) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{target.name}.vaultctl-",
        suffix=".tmp",
        dir=target.parent,
    )
    staged = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        staged.unlink(missing_ok=True)
        raise
    return staged


def _atomic_replace(staged: Path, target: Path) -> None:
    os.replace(staged, target)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _receipt(
    *,
    manifest: VaultManifest,
    plan: MergePlan,
    validation: ProspectiveValidation,
    before_hash: str,
    after_hash: str,
    state: str,
    error: str | None = None,
) -> Receipt:
    plan_digest = merge_plan_digest(plan)
    validation_digest = _digest(validation.to_dict())
    operation_id = _digest(
        {
            "planDigest": plan_digest,
            "validationDigest": validation_digest,
            "beforeHash": before_hash,
            "afterHash": after_hash,
            "state": state,
            "error": error,
        }
    )
    return Receipt(
        schema_version=RECEIPT_SCHEMA_VERSION,
        vault_id=manifest.vault_id,
        operation_id=operation_id,
        paths=(plan.path,),
        before_hashes={plan.path: before_hash},
        after_hashes={plan.path: after_hash},
        state=state,
        plan_id=plan.plan_id,
        plan_digest=plan_digest,
        input_revisions={
            name: merge_input.revision for name, merge_input in plan.inputs.items()
        },
        manifest_digest=plan.manifest_digest,
        engine_version=plan.engine_version,
        validation_digest=validation_digest,
        error=error,
    )


def _failure_message(exc: Exception) -> str:
    if isinstance(exc, MergeError):
        return str(exc)
    return f"{type(exc).__name__}: local atomic replacement failed"


def apply_merge_plan(
    manifest: VaultManifest,
    plan: MergePlan,
) -> Receipt:
    with _write_lock(manifest):
        prepared = _prepare_merge(manifest, plan)
        if APPLY_CAPABILITY not in prepared.manifest.capabilities:
            raise MergeError(
                f"merge apply requires manifest capability {APPLY_CAPABILITY!r}"
            )
        if not prepared.validation.valid:
            raise MergeError("prospective whole-vault validation failed")

        before_hash = _source_hash(prepared.before)
        candidate_hash = _source_hash(prepared.candidate)
        mode = stat.S_IMODE(prepared.target.stat().st_mode)
        staged = _stage_bytes(prepared.target, prepared.candidate, mode)
        try:
            try:
                _atomic_replace(staged, prepared.target)
                _fsync_directory(prepared.target.parent)
                if _source_hash(prepared.target.read_bytes()) != candidate_hash:
                    raise MergeError("atomic replacement produced an unexpected hash")
                applied_result = scan_vault(manifest.root)
                if (
                    applied_result.errors
                    or _scan_digest(applied_result) != prepared.validation.vault_digest
                ):
                    raise MergeError(
                        "post-apply vault state differs from prospective validation"
                    )
            except Exception as exc:
                try:
                    current = prepared.target.read_bytes()
                except OSError:
                    current = None
                if current != prepared.before:
                    rollback = _stage_bytes(prepared.target, prepared.before, mode)
                    try:
                        _atomic_replace(rollback, prepared.target)
                        _fsync_directory(prepared.target.parent)
                    except Exception as rollback_exc:
                        raise MergeError(
                            "merge apply failed and rollback could not restore "
                            "the target"
                        ) from rollback_exc
                    finally:
                        rollback.unlink(missing_ok=True)
                    if prepared.target.read_bytes() != prepared.before:
                        raise MergeError(
                            "rollback did not restore the original content"
                        ) from exc
                    state = "rolled-back"
                else:
                    state = "failed"
                return _receipt(
                    manifest=prepared.manifest,
                    plan=plan,
                    validation=prepared.validation,
                    before_hash=before_hash,
                    after_hash=_source_hash(prepared.target.read_bytes()),
                    state=state,
                    error=_failure_message(exc),
                )

            return _receipt(
                manifest=prepared.manifest,
                plan=plan,
                validation=prepared.validation,
                before_hash=before_hash,
                after_hash=candidate_hash,
                state="applied",
            )
        finally:
            staged.unlink(missing_ok=True)
