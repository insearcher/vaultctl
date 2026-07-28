# Repository rules

`vaultctl` is developed as a public open-source project, even while its
repository is private.

- Never add real vault contents, private schemas, credentials, internal URLs,
  usernames, email addresses, or machine-specific absolute paths.
- Use synthetic fixtures with fictional data.
- Keep the manifest declarative. It must not execute repository-local code or
  shell commands.
- Preserve headless operation; live application integrations are optional
  capabilities.
- Keep changes small and prove them with the narrowest relevant tests.
- Do not add write behavior until it has an explicit mutation plan, validation,
  rollback, and receipt contract.
