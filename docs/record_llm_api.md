# Recording LLM API exchanges

Status: implementation plan, recorder not implemented.

## Outcome

Archive API exchanges offline using stock CLIProxyAPI. Prioritize compression ratio over compression speed and random access. Capture is best effort: the logger can omit logs or silently drop chunks, even when a terminal event survives.

Capture client-facing HTTP JSON/SSE on `/v1/messages`, `/v1/responses`, and `/v1/chat/completions`. WebSockets, upstream bodies, conversation reconstruction, and a browsing UI are outside scope.

Store whole batches as compressed JSONL, with records sharing compression history. The archive consists of self-contained daily files, without a SQLite body store, separate index, or manifest.

## Producer contract

The audit is pinned to CLIProxyAPI [commit 5208aec703b5ce7e3445f6e9d91cc13b3e78003a](https://github.com/router-for-me/CLIProxyAPI/tree/5208aec703b5ce7e3445f6e9d91cc13b3e78003a). Record the deployed version and synthetic logs in `tests/fixtures/record/`, with provenance in `tests/fixtures/record/README.md`, and verify support with fixture tests.

Enable `request-log: true` and `commercial-mode: false`; configure the actual log directory. Logging adds proxy overhead. The recorder never modifies producer files. Upstream `logs-max-total-size-mb` can cap completed logs but excludes active/abandoned temporary body files. Deployment owns raw-file cleanup and must retain inputs long enough for the batch cadence and intended outage window.

Logs include numbered upstream attempts and unescaped body sections. Parse the supported client exchange, skip upstream sections, and reject ambiguous framing. Preserve recoverable body content without promising exact wire bytes or log-added padding. Sources: [formatting](https://github.com/router-for-me/CLIProxyAPI/blob/5208aec703b5ce7e3445f6e9d91cc13b3e78003a/internal/logging/request_logger_format.go), [stream loss](https://github.com/router-for-me/CLIProxyAPI/blob/5208aec703b5ce7e3445f6e9d91cc13b3e78003a/internal/logging/request_logger_streaming.go), [usage IDs](https://github.com/router-for-me/CLIProxyAPI/blob/5208aec703b5ce7e3445f6e9d91cc13b3e78003a/internal/redisqueue/plugin.go).

## Credential groups and usage links

Identify a credential group by SHA-256 of its already-masked token. Support exactly one credential source: canonical `Authorization: Bearer <token>` or `x-api-key`, without extra token whitespace. Check every credential source recognized by the deployed proxy; skip empty, multiple, query, and otherwise unsupported credentials.

Keys sharing a mask form one group; exclusion and deletion cover them together. The `groups` command lists group fingerprints and masked labels from usage keys using the deployed proxy's masking rule, never the dashboard's `redact_key`. Capture does not depend on usage delivery.

Preserve the queue's `request_id` in a new `request_ids` table in `usage.db`, keyed by `requests.timestamp`, atomically with each newly inserted usage row. No migration or historical backfill is needed. Collector code lives in `src/cliproxy_usage_collect/{schemas,parser,db}.py`; existing usage queries keep using `requests`.

Use one proxy per usage DB and archive. The 32-bit upstream IDs are not archive identities. Export optionally adds usage-row timestamp candidates having the same ID within one hour of the logged timestamp. Return all candidates, including an empty list; these links never determine ownership or export eligibility.

## Batch recorder and format

Add `cliproxy-usage-record` under `src/cliproxy_usage_record/`, using `AGENTS.md` conventions. Run daily by default; shorter cadences reduce dependence on raw retention but recompress changed day files more often. Scan recognized filenames, consider files at least 120 seconds old, and compare file identity, size, and mtime before and after reading. Defer unstable reads; age is not proof of a closed writer.

Archive to `<RECORD_DIR>/YYYY-MM-DD.jsonl.xz`. The partition date is the literal calendar date in the producer filename, so changed or malformed source bodies still map to the same file. Record timestamps remain UTC and govern retention.

Each JSONL record has these fields:

| Field | Type and meaning |
|---|---|
| `source_name`, `source_digest` | Full source filename (record identity) and hex SHA-256 of source bytes |
| `request_id`, `proxy_version` | Strings from the filename and request-info section |
| `timestamp` | Logged timestamp as an ISO-8601 UTC string |
| `credential_group` | Hex SHA-256 of the masked token |
| `endpoint`, `model` | Supported path and requested model string |
| `status`, `response_kind` | HTTP status integer and `json` or `sse`, determined from the response |
| `response_state` | `success`, `error`, `incomplete`, or `malformed` |
| `request_body`, `response_body` | Recoverable client-facing body strings |

Require a request JSON object with a model string. Keep malformed response bodies when framing is unambiguous. Non-2xx or protocol errors are `error`, invalid JSON/SSE is `malformed`, missing completion is `incomplete`, otherwise `success`. Validate endpoint-specific success, including tools; no state certifies complete capture. Store no headers, upstream sections, or raw-log copies.

For each affected day, merge existing archive records and accepted source observations by `source_name`. Identical digests are no-ops; replace changed records, removing stale records when a stable changed source becomes inadmissible. Preserve archived records whose producer files disappeared. Apply retention and exclusions even to unchanged records. Do not rewrite unchanged day files.

One archive lock serializes recording, export, and pruning. Publish each changed day through a complete, validated, durable temporary file followed by atomic replacement and durable directory update. A crash leaves either the old or new complete day; retry merges safely. Ignore unfinished temporary outputs. Publication is atomic per day, not across the archive. Never rebuild a damaged existing archive from only the remaining producer logs.

## Compression

Use compact UTF-8 JSONL ordered by `request_body`, then `response_body`, then `source_name`, keeping similar prompts close together. Compress the whole day as one XZ stream with LZMA2 preset `9 | PRESET_EXTREME`; do not concatenate separately compressed records or reset compression history per row. Python's standard-library `lzma` supports this format. [LZMA documentation](https://docs.python.org/3/library/lzma.html)

Measure compressed bytes, peak memory, and compression/decompression time on a fixed representative fixture corpus. Compare independent-record gzip, batch gzip, batch XZ presets 6/9/9e, and high-level Zstandard using identical serialized records, with and without the proposed ordering. Select the smallest batch result for the final plan before implementing storage; do not build a runtime codec-selection framework. XZ 9e is the current choice, supported by a synthetic repeated-history comparison, not a production compression estimate.

## Configuration, export, and retention

Use pydantic-settings with env-var names in errors: required `CLIPROXY_REQUEST_LOG_DIR`, `RECORD_DIR`, and positive `RECORD_RETENTION_DAYS`; `RECORD_GRACE_SECONDS` defaults to 120. Optional `RECORD_EXCLUDE_GROUPS` lists fingerprints. `USAGE_DB_PATH` is used only for group listing and optional export annotations. Exit 0 for success, 1 for I/O failure or a held lock, 2 for invalid configuration, and 4 for ambiguous/unsupported log structure on supported endpoints. Report per-record outcomes and committed day counts.

`export` streams decompressed JSONL, defaulting to `response_state = success` and current retention/exclusions. Consumers parse endpoint-specific bodies to produce training examples; completeness remains unknown. Compressed files are also directly readable through an XZ decompressor and JSONL reader, including records that default export filters out.

Use a UTC cutoff of now minus `RECORD_RETENTION_DAYS` for both source ingestion and archive pruning. Remove wholly expired day files; rewrite mixed days after filtering their record timestamps. The filename date alone does not establish expiry. Excluded groups are removed during recording or explicit pruning, preventing reimport. Explicit group deletion rewrites affected days and is repeatable after interruption; configure exclusion first. Tell users what is captured and choose retention before logging. Opt-out cannot stop temporary raw logging by the stock proxy.

## Definition of done

Add the named suites below; they are implementation acceptance checks, not existing tests. Run from the repository root inside `nix develop`.

| Success criterion | Runnable pass/fail check |
|---|---|
| Usage IDs commit atomically; existing queries work. Candidate links handle late/missing usage, aliases, retries, and collisions without affecting capture. | `uv run pytest tests/test_queue_parser.py tests/test_db.py tests/test_record_identity.py -q` |
| Deployed-release fixtures cover each endpoint, JSON/SSE, retries, tools, malformed bodies, protocol errors, ambiguous framing, and supported/unsupported credential grouping. | `uv run pytest tests/test_record_parser.py -q` |
| Repeat scans deduplicate; shared IDs in different filenames survive. Changed invalid sources remove stale records; missing raw files preserve archives. Faults at each publication boundary leave an intact old/new day and safe retry; locks, damaged archives, and late arrivals follow the contract. Producer files are untouched. | `uv run pytest tests/test_record_store.py -q` |
| JSONL round-trips exactly, including Unicode and SSE. Whole-batch compression beats per-record gzip on the repeated-history fixture. Record all candidate codec sizes, memory, and timings on the fixed representative corpus and select the smallest result. | `uv run pytest tests/test_record_compression.py -q -s` |
| Export matches fixtures and filters unsuccessful/expired/excluded records. Partial-day UTC pruning, interrupted group deletion, and rescan cannot reimport filtered records; group listing matches logged masks. | `uv run pytest tests/test_record_export.py -q` |
| CLI packaging and backend checks pass. | `uv run cliproxy-usage-record --help`; `uv run pytest`; `uv run ruff check`; `uv run basedpyright` |
