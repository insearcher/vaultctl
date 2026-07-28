# Security policy

## Reporting

Please report vulnerabilities through GitHub private vulnerability reporting.
Do not open a public issue for a suspected path traversal, arbitrary code
execution, credential exposure, or destructive write flaw.

## Security boundaries

- A command operates on exactly one resolved vault root.
- Paths must remain inside that root after symlink resolution.
- Manifests are declarative and cannot execute repository-local code.
- Credentials and runtime state do not belong in a vault manifest.
- The current pre-alpha release is read-only.
