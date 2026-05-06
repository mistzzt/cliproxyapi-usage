"""CLI entry point for cliproxy-usage-collect."""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence

from cliproxy_usage_collect.config import Config, ConfigError, load_config
from cliproxy_usage_collect.db import insert_records, open_db
from cliproxy_usage_collect.parser import SchemaError, iter_records
from cliproxy_usage_collect.queue_client import (
    AuthError,
    TransientError,
    pop_usage_records,
)

QueueClient = Callable[[Config], Sequence[str | bytes]]


def main(
    argv: list[str] | None = None, *, queue_client: QueueClient = pop_usage_records
) -> int:
    """Collect usage data and insert into the local SQLite database.

    Parameters
    ----------
    argv:
        Accepted for future-proofing; not parsed.
    queue_client:
        Callable that drains one batch of raw queue elements. Tests can inject
        a fake callable to avoid touching CLIProxyAPI.

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
        queued_records = list(queue_client(cfg))
        inserted = insert_records(conn, iter_records(queued_records))
    except AuthError as exc:
        print(f"Authentication error: {exc}", file=sys.stderr)
        return 3
    except SchemaError as exc:
        print(f"Schema error: {exc}", file=sys.stderr)
        return 4
    except TransientError as exc:
        print(f"Transient error: {exc}", file=sys.stderr)
        return 1

    print(
        f"inserted {inserted} new records from {len(queued_records)} queued records",
        file=sys.stderr,
    )
    return 0


def _entry() -> None:
    """Console script entry point."""
    sys.exit(main())
