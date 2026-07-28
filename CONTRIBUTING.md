# Contributing

## Public-data rule

Treat every commit as immediately public.

Do not commit:

- real notes or exports from a personal or company vault;
- private manifests, hostnames, repository URLs, or ticket identifiers;
- credentials, tokens, cookies, key material, or `.env` files;
- usernames, email addresses, or machine-specific absolute paths.

Tests and examples must use fictional, synthetic content. Reproduce a schema
shape rather than copying a real schema or document.

## Local checks

```bash
python -m pip install -e '.[dev]'
ruff check .
pytest
python scripts/check_public_tree.py
```

Keep pull requests focused. Changes to output formats, manifests, plans, or
receipts require a versioned contract update.
