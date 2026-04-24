"""Tests for quota response DTOs."""

from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from cliproxy_usage_server.schemas import (
    ProviderQuota,
    QuotaAccount,
    QuotaAccountsResponse,
    QuotaError,
    QuotaResponse,
    QuotaWindow,
)


class TestQuotaResponse:
    """Tests for QuotaResponse validation rules."""

    def test_both_null_raises_validation_error(self) -> None:
        """QuotaResponse with both quota=None and error=None raises ValidationError."""
        with pytest.raises(ValidationError):
            QuotaResponse(
                quota=None,
                error=None,
                fetched_at=datetime.now(),
                stale_at=datetime.now() + timedelta(hours=1),
            )

    def test_both_populated_raises_validation_error(self) -> None:
        """QuotaResponse with both quota and error populated raises ValidationError."""
        now = datetime.now()
        with pytest.raises(ValidationError):
            QuotaResponse(
                quota=ProviderQuota(
                    provider="claude",
                    auth_name="test-auth",
                    plan_type="free",
                    windows=[],
                    extra={},
                ),
                error=QuotaError(
                    kind="transient",
                    message="test error",
                    upstream_status=None,
                ),
                fetched_at=now,
                stale_at=now + timedelta(hours=1),
            )

    def test_quota_only_validates(self) -> None:
        """QuotaResponse with only quota populated validates cleanly."""
        now = datetime.now()
        response = QuotaResponse(
            quota=ProviderQuota(
                provider="claude",
                auth_name="test-auth",
                plan_type="free",
                windows=[],
                extra={},
            ),
            error=None,
            fetched_at=now,
            stale_at=now + timedelta(hours=1),
        )
        assert response.quota is not None
        assert response.error is None

    def test_error_only_validates(self) -> None:
        """QuotaResponse with only error populated validates cleanly."""
        now = datetime.now()
        response = QuotaResponse(
            quota=None,
            error=QuotaError(
                kind="auth",
                message="Invalid credentials",
                upstream_status=401,
            ),
            fetched_at=now,
            stale_at=now + timedelta(hours=1),
        )
        assert response.quota is None
        assert response.error is not None


class TestQuotaWindow:
    """Tests for QuotaWindow optional fields."""

    def test_optional_used_percent(self) -> None:
        """QuotaWindow accepts used_percent=None."""
        window = QuotaWindow(
            id="window-1",
            label="monthly",
            used_percent=None,
            resets_at=None,
        )
        assert window.used_percent is None

    def test_optional_resets_at(self) -> None:
        """QuotaWindow accepts resets_at=None."""
        window = QuotaWindow(
            id="window-1",
            label="monthly",
            used_percent=50.0,
            resets_at=None,
        )
        assert window.resets_at is None

    def test_both_optional_fields_none(self) -> None:
        """QuotaWindow accepts both optional fields as None."""
        window = QuotaWindow(
            id="window-1",
            label="monthly",
            used_percent=None,
            resets_at=None,
        )
        assert window.used_percent is None
        assert window.resets_at is None


class TestQuotaAccount:
    """Tests for QuotaAccount validation."""

    def test_provider_gemini_rejected(self) -> None:
        """QuotaAccount rejects provider='gemini' (literal mismatch)."""
        with pytest.raises(ValidationError):
            QuotaAccount(
                provider="gemini",  # type: ignore[arg-type]
                auth_name="test-auth",
                display_name="Test Account",
            )

    def test_provider_claude_accepted(self) -> None:
        """QuotaAccount accepts provider='claude'."""
        account = QuotaAccount(
            provider="claude",
            auth_name="test-auth",
            display_name="Test Account",
        )
        assert account.provider == "claude"

    def test_provider_codex_accepted(self) -> None:
        """QuotaAccount accepts provider='codex'."""
        account = QuotaAccount(
            provider="codex",
            auth_name="test-auth",
            display_name="Test Account",
        )
        assert account.provider == "codex"

    def test_display_name_optional(self) -> None:
        """QuotaAccount accepts display_name=None."""
        account = QuotaAccount(
            provider="claude",
            auth_name="test-auth",
            display_name=None,
        )
        assert account.display_name is None


class TestQuotaAccountsResponse:
    """Tests for QuotaAccountsResponse."""

    def test_empty_accounts(self) -> None:
        """QuotaAccountsResponse accepts empty accounts list."""
        response = QuotaAccountsResponse(accounts=[])
        assert response.accounts == []

    def test_multiple_accounts(self) -> None:
        """QuotaAccountsResponse accepts multiple accounts."""
        response = QuotaAccountsResponse(
            accounts=[
                QuotaAccount(
                    provider="claude",
                    auth_name="auth-1",
                    display_name="Account 1",
                ),
                QuotaAccount(
                    provider="codex",
                    auth_name="auth-2",
                    display_name="Account 2",
                ),
            ]
        )
        assert len(response.accounts) == 2


class TestProviderQuotaExtraDefault:
    """Tests for ProviderQuota extra field default."""

    def test_extra_defaults_to_empty_dict(self) -> None:
        """ProviderQuota.extra defaults to empty dict when not provided."""
        quota = ProviderQuota(
            provider="claude",
            auth_name="test-auth",
            plan_type="pro",
            windows=[],
        )
        assert quota.extra == {}
