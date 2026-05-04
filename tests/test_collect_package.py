"""Import-level checks for the collector package name."""

import importlib
import importlib.util


def test_collector_package_is_only_available_under_collect_name() -> None:
    module = importlib.import_module("cliproxy_usage_collect.schemas")

    assert module.RequestRecord is not None
    assert importlib.util.find_spec("cliproxy_usage") is None
