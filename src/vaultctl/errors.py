class VaultctlError(Exception):
    """Base error for expected user-facing failures."""


class ManifestError(VaultctlError):
    """The vault manifest is missing or invalid."""


class MarkdownError(VaultctlError):
    """A Markdown document cannot be parsed safely."""


class QueryError(VaultctlError):
    """A read query violates its manifest contract."""


class MergeError(VaultctlError):
    """A semantic merge plan cannot be produced safely."""


class MutationError(VaultctlError):
    """A node mutation request or plan cannot be handled safely."""


class CacheError(VaultctlError):
    """The disposable read-index cache is unavailable or unusable."""
