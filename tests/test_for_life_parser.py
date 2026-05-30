"""Tests for the shared per-tier for-life prize-string parser in
backend/scraper/base.py.

Every regression case here corresponds to a real state-API encoding observed
in production. Adding a new state with a new encoding means adding a case
here BEFORE the scraper change."""
from __future__ import annotations
import pytest

from backend.scraper.base import (
    FOR_LIFE_DEFAULT_YEARS,
    parse_for_life_tier,
)
from backend.ev_calculator import annuity_present_value


def _npv(per_period: float, periods_per_year: int) -> float:
    return annuity_present_value(per_period * periods_per_year, FOR_LIFE_DEFAULT_YEARS)


class TestForLifeTierString:
    # NY encodes its for-life tier inside the prize_amount string.
    @pytest.mark.parametrize("raw,per_period,periods", [
        ("$10,000/WEEK/LIFE", 10_000, 52),
        ("$10K/Wk/Life",     10_000, 52),
        ("$5K/WK/LIFE",       5_000, 52),
        ("$1,000/WEEK/LIFE",  1_000, 52),
        ("$1K/WK/LIFE",       1_000, 52),
    ])
    def test_ny_encodings(self, raw, per_period, periods):
        result = parse_for_life_tier(raw)
        assert result is not None
        amt, annual, cash = result
        assert amt == per_period
        assert annual == per_period * periods
        assert cash == pytest.approx(_npv(per_period, periods), rel=1e-4)

    # FL has slightly different separators — space, slash, or no separator.
    @pytest.mark.parametrize("raw,per_period,periods", [
        ("$10,000/WK/LIFE",  10_000, 52),
        ("$5,000 WK/LIFE",    5_000, 52),  # space separator
        ("$2,500 WK/LIFE",    2_500, 52),
        ("$1,000 WK/LIFE",    1_000, 52),
        ("$500 WK/LIFE",        500, 52),
        ("$50,000YR/LIFE",   50_000,  1),  # no separator between amount and period
    ])
    def test_fl_encodings(self, raw, per_period, periods):
        result = parse_for_life_tier(raw)
        assert result is not None
        amt, annual, cash = result
        assert amt == per_period
        assert annual == per_period * periods


class TestForLifeRejections:
    """Inputs that should NOT match the for-life tier parser — they're either
    not for-life prizes at all, or they're game NAME-level patterns handled
    by the heuristic separately."""
    @pytest.mark.parametrize("raw", [
        None,
        "",
        "$10,000.00",         # plain cash prize
        "$10K",               # plain amount, no period/life
        "$100 A WEEK FOR LIFE",  # game-name pattern (heuristic handles this)
        "FOR LIFE",           # no amount
        "$10K/WK",            # missing LIFE
        "$10K/LIFE",          # missing period
    ])
    def test_returns_none(self, raw):
        assert parse_for_life_tier(raw) is None


class TestForLifeNpvSanity:
    """Anchor a few NPVs so future tweaks to the discount rate or default
    years break loudly rather than silently shifting headline EV."""

    def test_10k_per_week_for_life(self):
        # Anchored against NY $10K A WEEK FOR LIFE production NPV.
        _, _, cash = parse_for_life_tier("$10K/Wk/Life")
        assert cash == pytest.approx(7_066_969.7, rel=1e-4)

    def test_2500_per_week_for_life(self):
        # Anchored against FL $2,500 A Week For Life production NPV.
        _, _, cash = parse_for_life_tier("$2,500 WK/LIFE")
        assert cash == pytest.approx(1_766_742.42, rel=1e-4)

    def test_1000_per_month_for_life(self):
        # GA $1,000/Month/Life NPV (used for reconstruction from name, not tier
        # string, but the math should match).
        annual = 1_000 * 12
        cash = annuity_present_value(annual, FOR_LIFE_DEFAULT_YEARS)
        assert cash == pytest.approx(163_083.92, rel=1e-4)
