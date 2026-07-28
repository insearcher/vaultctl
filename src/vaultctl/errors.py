class VaultctlError(Exception):
    """Base error for expected user-facing failures."""


class ManifestError(VaultctlError):
    """The vault manifest is missing or invalid."""


class MarkdownError(VaultctlError):
    """A Markdown document cannot be parsed safely."""
