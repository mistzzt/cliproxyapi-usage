"""CLI entry point for cliproxy-usage-collect."""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from cliproxy_usage_collect.config import Config, ConfigError, load_config
from cliproxy_usage_collect.db import insert_records, open_db
from cliproxy_usage_collect.migration import MigrationError
from cliproxy_usage_collect.parser import SchemaError, parse_records
from cliproxy_usage_collect.queue_client import (
    AuthError,
    TransientError,
    pop_usage_records,
)

QueueClient = Callable[[Config], Sequence[Mapping[str, Any]]]


def main(*, queue_client: QueueClient = pop_usage_records) -> int:
    """Collect usage data and insert into the local SQLite database.

    Parameters
    ----------
    queue_client:
        Callable that drains one batch of raw queue elements. Tests can inject
        a fake callable to avoid touching CLIProxyAPI.

    Returns
    -------
    int
        Exit code: 0 success, 1 transient error (migration failures included),
        2 config error, 3 auth error, 4 schema error.
    """
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        conn = open_db(cfg.db_path)
    except MigrationError as exc:
        print(f"Migration error: {exc}", file=sys.stderr)
        return 1

    try:
        queued_records = list(queue_client(cfg))
        batch = parse_records(queued_records)
        inserted = insert_records(conn, batch.records)
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
        f"inserted {inserted} new records from {len(queued_records)} queued records, "
        f"{batch.incomplete} without a token split",
        file=sys.stderr,
    )
    return 0


def _entry() -> None:
    """Console script entry point."""
    sys.exit(main())
