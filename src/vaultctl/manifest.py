from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from vaultctl.errors import ManifestError
from vaultctl.model import VaultManifest

MANIFEST_RELATIVE_PATH = Path(".vaultctl/manifest.json")


def resolve_vault_root(
    explicit: str | Path | None = None,
    *,
    start: Path | None = None,
) -> Path:
    if explicit is not None:
        root = Path(explicit).expanduser().resolve()
        if not (root / MANIFEST_RELATIVE_PATH).is_file():
            raise ManifestError(f"no {MANIFEST_RELATIVE_PATH.as_posix()} under {root}")
        return root

    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / MANIFEST_RELATIVE_PATH).is_file():
            return candidate
    raise ManifestError(
        f"could not find {MANIFEST_RELATIVE_PATH.as_posix()} above {current}"
    )


def _load_schema() -> dict[str, Any]:
    schema_path = files("vaultctl").joinpath("schemas/manifest-v1.schema.json")
    return json.loads(schema_path.read_text(encoding="utf-8"))


def load_manifest(root: Path) -> VaultManifest:
    path = root / MANIFEST_RELATIVE_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ManifestError(f"cannot read manifest: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(
            f"invalid JSON in manifest at line {exc.lineno}, column {exc.colno}"
        ) from exc

    validator = Draft202012Validator(_load_schema())
    errors = sorted(validator.iter_errors(data), key=lambda error: list(error.path))
    if errors:
        messages = []
        for error in errors:
            location = ".".join(str(item) for item in error.path) or "<root>"
            messages.append(f"{location}: {error.message}")
        raise ManifestError("invalid manifest:\n- " + "\n- ".join(messages))

    node_kinds = dict(data["nodeKinds"])
    default_kind = data.get("defaultKind")
    if default_kind is not None and default_kind not in node_kinds:
        raise ManifestError(f"defaultKind {default_kind!r} is not defined in nodeKinds")

    for relation_name, relation in data.get("relations", {}).items():
        unknown = sorted(set(relation.get("targetKinds", ())) - set(node_kinds))
        if unknown:
            joined = ", ".join(unknown)
            raise ManifestError(
                f"relation {relation_name!r} references unknown target kinds: {joined}"
            )

    return VaultManifest(
        api_version=data["apiVersion"],
        vault_id=data["vaultId"],
        root=root,
        node_kinds=node_kinds,
        relations=dict(data.get("relations", {})),
        default_kind=default_kind,
        ignore=tuple(data.get("ignore", ())),
        capabilities=tuple(data.get("capabilities", ())),
        raw=data,
    )
