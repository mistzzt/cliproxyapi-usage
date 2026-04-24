"""Pricing endpoint: /api/pricing."""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import APIRouter, Request

from cliproxy_usage_server.pricing import ModelPricing
from cliproxy_usage_server.schemas import PricingEntry, PricingResponse


def _is_tiered(mp: ModelPricing) -> bool:
    """Return True iff any above-200k-tokens field is set on this entry."""
    return any(
        getattr(mp, fld) is not None
        for fld in (
            "input_cost_per_token_above_200k_tokens",
            "output_cost_per_token_above_200k_tokens",
            "cache_creation_input_token_cost_above_200k_tokens",
            "cache_read_input_token_cost_above_200k_tokens",
        )
    )


def build_router() -> APIRouter:
    """Build the pricing API router.

    Pricing data is read from ``request.app.state.pricing``.
    Access control is delegated to the upstream reverse proxy.
    """
    r = APIRouter(prefix="", tags=["pricing"])

    @r.get("/pricing", response_model=PricingResponse)
    def pricing_endpoint(request: Request) -> PricingResponse:
        """Return all known model pricing entries.

        ``tiered`` is True iff the source has any ``*_above_200k_tokens`` field.
        """
        raw: Mapping[str, ModelPricing] = request.app.state.pricing
        out: dict[str, PricingEntry] = {}
        for name, mp in raw.items():
            out[name] = PricingEntry(
                input=mp.input_cost_per_token,
                output=mp.output_cost_per_token,
                cache_read=mp.cache_read_input_token_cost,
                cache_creation=mp.cache_creation_input_token_cost,
                tiered=_is_tiered(mp),
            )
        return PricingResponse(pricing=out)

    return r
