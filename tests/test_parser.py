"""Tests for cliproxy_usage.parser."""

import json

import pytest

from cliproxy_usage.parser import SchemaError, iter_records

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load(path):
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# Corpus-level tests
# ---------------------------------------------------------------------------


class TestIterRecordsCorpus:
    def test_returns_iterator_not_list(self, usage_export_json):
        export = _load(usage_export_json)
        result = iter_records(export)
        assert not isinstance(result, list)
        assert hasattr(result, "__next__")

    def test_total_record_count(self, usage_export_json):
        export = _load(usage_export_json)
        assert sum(1 for _ in iter_records(export)) == 138

    def test_earliest_timestamp(self, usage_export_json):
        export = _load(usage_export_json)
        earliest = min(r.timestamp for r in iter_records(export))
        assert earliest == "2026-04-22T20:16:08.582370572-05:00"

    def test_latest_timestamp(self, usage_export_json):
        export = _load(usage_export_json)
        latest = max(r.timestamp for r in iter_records(export))
        assert latest == "2026-04-22T20:55:15.805802264-05:00"


# ---------------------------------------------------------------------------
# Spot-check a specific record
# ---------------------------------------------------------------------------


class TestFirstRecordForKnownKey:
    API_KEY = "sk-REDACTED000000000000000000000000"
    MODEL = "gpt-5.4"

    def _first_match(self, export):
        for r in iter_records(export):
            if r.api_key == self.API_KEY and r.model == self.MODEL:
                return r
        pytest.fail("No record found for expected api_key/model combination")

    def test_source(self, usage_export_json):
        r = self._first_match(_load(usage_export_json))
        assert r.source == "codex-user-1@example.com"

    def test_cached_tokens(self, usage_export_json):
        r = self._first_match(_load(usage_export_json))
        assert r.cached_tokens == 0

    def test_total_tokens(self, usage_export_json):
        r = self._first_match(_load(usage_export_json))
        assert r.total_tokens == 2013

    def test_failed_is_false(self, usage_export_json):
        r = self._first_match(_load(usage_export_json))
        assert r.failed is False


# ---------------------------------------------------------------------------
# SchemaError tests (fabricated minimal payloads)
# ---------------------------------------------------------------------------


def _minimal_export(detail: dict) -> dict:
    """Wrap a single detail dict into the minimal export structure."""
    return {
        "usage": {
            "apis": {
                "test-key": {
                    "models": {
                        "test-model": {
                            "details": [detail],
                        }
                    }
                }
            }
        }
    }


VALID_DETAIL = {
    "timestamp": "2026-01-01T00:00:00Z",
    "source": "user@example.com",
    "auth_index": "abc123",
    "latency_ms": 100,
    "failed": False,
    "tokens": {
        "input_tokens": 10,
        "output_tokens": 5,
        "reasoning_tokens": 0,
        "cached_tokens": 2,
        "total_tokens": 15,
    },
}


def test_schema_error_missing_timestamp():
    detail = {k: v for k, v in VALID_DETAIL.items() if k != "timestamp"}
    with pytest.raises(SchemaError):
        list(iter_records(_minimal_export(detail)))


def test_schema_error_missing_source():
    detail = {k: v for k, v in VALID_DETAIL.items() if k != "source"}
    with pytest.raises(SchemaError):
        list(iter_records(_minimal_export(detail)))


def test_schema_error_missing_auth_index():
    detail = {k: v for k, v in VALID_DETAIL.items() if k != "auth_index"}
    with pytest.raises(SchemaError):
        list(iter_records(_minimal_export(detail)))


def test_schema_error_missing_latency_ms():
    detail = {k: v for k, v in VALID_DETAIL.items() if k != "latency_ms"}
    with pytest.raises(SchemaError):
        list(iter_records(_minimal_export(detail)))


def test_schema_error_missing_failed():
    detail = {k: v for k, v in VALID_DETAIL.items() if k != "failed"}
    with pytest.raises(SchemaError):
        list(iter_records(_minimal_export(detail)))


def test_schema_error_missing_tokens_block():
    detail = {k: v for k, v in VALID_DETAIL.items() if k != "tokens"}
    with pytest.raises(SchemaError):
        list(iter_records(_minimal_export(detail)))


@pytest.mark.parametrize(
    "token_field",
    [
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cached_tokens",
        "total_tokens",
    ],
)
def test_schema_error_missing_token_field(token_field):
    import copy

    detail = copy.deepcopy(VALID_DETAIL)
    del detail["tokens"][token_field]
    with pytest.raises(SchemaError):
        list(iter_records(_minimal_export(detail)))


def test_schema_error_missing_top_level_usage_key():
    with pytest.raises(SchemaError):
        list(iter_records({}))


def test_schema_error_wrong_type_for_latency_ms():
    import copy

    detail = copy.deepcopy(VALID_DETAIL)
    detail["latency_ms"] = "not-an-int"  # can't coerce to int
    # int("not-an-int") raises ValueError which we catch as SchemaError
    with pytest.raises(SchemaError):
        list(iter_records(_minimal_export(detail)))
