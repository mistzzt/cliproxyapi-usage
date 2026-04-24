"""CLI entry point for cliproxy-usage-collect."""

from __future__ import annotations

import sys

import httpx

from cliproxy_usage.client import AuthError, TransientError, fetch_export
from cliproxy_usage.config import ConfigError, load_config
from cliproxy_usage.db import insert_records, open_db
from cliproxy_usage.parser import SchemaError, iter_records


def main(
    argv: list[str] | None = None, *, http_client: httpx.Client | None = None
) -> int:
    """Collect usage data and insert into the local SQLite database.

    Parameters
    ----------
    argv:
        Accepted for future-proofing; not parsed.
    http_client:
        Optional httpx.Client to pass to fetch_export (useful for testing
        with MockTransport).

    Returns
    -------
    int
        Exit code: 0 success, 1 transient error, 2 config error,
        3 auth error, 4 schema error.
    """
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        conn = open_db(cfg.db_path)
        export = fetch_export(cfg, client=http_client)
        inserted = insert_records(conn, iter_records(export))
    except AuthError as exc:
        print(f"Authentication error: {exc}", file=sys.stderr)
        return 3
    except SchemaError as exc:
        print(f"Schema error: {exc}", file=sys.stderr)
        return 4
    except TransientError as exc:
        print(f"Transient error: {exc}", file=sys.stderr)
        return 1

    print(f"inserted {inserted} new records", file=sys.stderr)
    return 0


def _entry() -> None:
    """Console script entry point."""
    sys.exit(main())
