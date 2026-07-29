from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from pathlib import Path, PurePosixPath

from vaultctl.errors import MergeError, VaultctlError
from vaultctl.manifest import load_manifest
from vaultctl.markdown import parse_markdown_bytes, render_markdown_candidate
from vaultctl.merge import _digest, manifest_digest, merge_plan_digest, plan_merge
from vaultctl.model import GitDriverReceipt, MergePlan, VaultManifest

GIT_DRIVER_SCHEMA_VERSION = "vaultctl.merge-driver/v1"


def _source_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _read_regular_file(path: Path, role: str) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise MergeError(f"cannot inspect Git {role} input") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise MergeError(f"Git {role} input must be a regular non-symlink file")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise MergeError(f"cannot read Git {role} input") from exc


def _stage_bytes(target: Path, content: bytes, mode: int) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{target.name}.vaultctl-driver-",
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
    manifest: VaultManifest,
    plan: MergePlan,
    *,
    state: str,
    before_hash: str,
    after_hash: str,
    error: str | None = None,
) -> GitDriverReceipt:
    plan_digest = merge_plan_digest(plan)
    conflicts = tuple(
        {
            "id": conflict.id,
            "kind": conflict.kind,
            "location": conflict.location,
        }
        for conflict in plan.conflicts
    )
    core = {
        "schemaVersion": GIT_DRIVER_SCHEMA_VERSION,
        "vaultId": manifest.vault_id,
        "path": plan.path,
        "state": state,
        "planId": plan.plan_id,
        "planDigest": plan_digest,
        "inputRevisions": {
            name: merge_input.revision
            for name, merge_input in sorted(plan.inputs.items())
        },
        "manifestDigest": plan.manifest_digest,
        "engineVersion": plan.engine_version,
        "beforeHash": before_hash,
        "afterHash": after_hash,
        "conflicts": list(conflicts),
    }
    if error is not None:
        core["error"] = error
    return GitDriverReceipt(
        schema_version=GIT_DRIVER_SCHEMA_VERSION,
        operation_id=_digest(core),
        vault_id=manifest.vault_id,
        path=plan.path,
        state=state,
        plan_id=plan.plan_id,
        plan_digest=plan_digest,
        input_revisions={
            name: merge_input.revision
            for name, merge_input in sorted(plan.inputs.items())
        },
        manifest_digest=plan.manifest_digest,
        engine_version=plan.engine_version,
        before_hash=before_hash,
        after_hash=after_hash,
        conflicts=conflicts,
        error=error,
    )


def _failure_message(exc: Exception) -> str:
    if isinstance(exc, VaultctlError):
        return str(exc)
    return f"{type(exc).__name__}: Git merge driver could not replace ours"


def run_git_merge_driver(
    manifest: VaultManifest,
    *,
    path: str,
    base_path: Path,
    ours_path: Path,
    theirs_path: Path,
) -> GitDriverReceipt:
    """Resolve one Git merge triple and mutate only Git's ``ours`` file."""

    base_raw = _read_regular_file(base_path, "base")
    ours_raw = _read_regular_file(ours_path, "ours")
    theirs_raw = _read_regular_file(theirs_path, "theirs")
    fallback_stem = PurePosixPath(path).stem
    base = parse_markdown_bytes(
        base_raw,
        display_path=f"{path} (base)",
        fallback_stem=fallback_stem,
        allow_legacy_colon_scalars=manifest.allow_legacy_colon_scalars,
    )
    ours = parse_markdown_bytes(
        ours_raw,
        display_path=f"{path} (ours)",
        fallback_stem=fallback_stem,
        allow_legacy_colon_scalars=manifest.allow_legacy_colon_scalars,
    )
    theirs = parse_markdown_bytes(
        theirs_raw,
        display_path=f"{path} (theirs)",
        fallback_stem=fallback_stem,
        allow_legacy_colon_scalars=manifest.allow_legacy_colon_scalars,
    )
    plan = plan_merge(
        manifest,
        path=path,
        base=base,
        ours=ours,
        theirs=theirs,
        base_revision=base.source_hash,
        ours_revision=ours.source_hash,
        theirs_revision=theirs.source_hash,
    )
    before_hash = ours.source_hash
    if plan.state == "conflict":
        return _receipt(
            manifest,
            plan,
            state="conflict",
            before_hash=before_hash,
            after_hash=before_hash,
        )

    if plan.candidate is None:
        raise MergeError("clean Git merge plan has no candidate")
    candidate = render_markdown_candidate(
        ours_raw,
        properties=plan.candidate.properties,
        body=plan.candidate.body,
        display_path=path,
        allow_legacy_colon_scalars=manifest.allow_legacy_colon_scalars,
    )
    parsed_candidate = parse_markdown_bytes(
        candidate,
        display_path=path,
        fallback_stem=fallback_stem,
        allow_legacy_colon_scalars=manifest.allow_legacy_colon_scalars,
    )
    if (
        _digest(
            {
                "properties": parsed_candidate.properties,
                "body": parsed_candidate.body,
            }
        )
        != plan.candidate.content_hash
    ):
        raise MergeError("rendered Git driver output does not match the merge plan")

    after_hash = _source_hash(candidate)
    if candidate == ours_raw:
        return _receipt(
            manifest,
            plan,
            state="unchanged",
            before_hash=before_hash,
            after_hash=after_hash,
        )

    try:
        ours_metadata = ours_path.lstat()
    except OSError as exc:
        raise MergeError("cannot inspect Git ours input before replacement") from exc
    if stat.S_ISLNK(ours_metadata.st_mode) or not stat.S_ISREG(ours_metadata.st_mode):
        raise MergeError("Git ours input must remain a regular non-symlink file")
    mode = stat.S_IMODE(ours_metadata.st_mode)
    staged: Path | None = None
    try:
        staged = _stage_bytes(ours_path, candidate, mode)
        live_manifest = load_manifest(manifest.root)
        if manifest_digest(live_manifest) != plan.manifest_digest:
            raise MergeError("vault manifest changed while the driver was running")
        if _read_regular_file(ours_path, "ours") != ours_raw:
            raise MergeError("Git ours input changed while the driver was running")
        _atomic_replace(staged, ours_path)
        _fsync_directory(ours_path.parent)
        if _read_regular_file(ours_path, "ours") != candidate:
            raise MergeError("Git driver replacement produced unexpected content")
    except Exception as exc:
        try:
            current = _read_regular_file(ours_path, "ours")
        except MergeError:
            current = None
        if current != ours_raw:
            rollback = _stage_bytes(ours_path, ours_raw, mode)
            try:
                _atomic_replace(rollback, ours_path)
                _fsync_directory(ours_path.parent)
            except Exception as rollback_exc:
                raise MergeError(
                    "Git merge driver failed and could not restore ours"
                ) from rollback_exc
            finally:
                rollback.unlink(missing_ok=True)
            if _read_regular_file(ours_path, "ours") != ours_raw:
                raise MergeError(
                    "Git merge driver rollback did not restore ours"
                ) from exc
            state = "rolled-back"
        else:
            state = "failed"
        return _receipt(
            manifest,
            plan,
            state=state,
            before_hash=before_hash,
            after_hash=_source_hash(_read_regular_file(ours_path, "ours")),
            error=_failure_message(exc),
        )
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)

    return _receipt(
        manifest,
        plan,
        state="applied",
        before_hash=before_hash,
        after_hash=after_hash,
    )
