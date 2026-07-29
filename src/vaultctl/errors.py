class VaultctlError(Exception):
    """Base error for expected user-facing failures."""


class ManifestError(VaultctlError):
    """The vault manifest is missing or invalid."""


class MarkdownError(VaultctlError):
    """A Markdown document cannot be parsed safely."""


class QueryError(VaultctlError):
    """A search or context query violates its manifest contract."""


class MergeError(VaultctlError):
    """A semantic merge plan cannot be produced safely."""
