"""Unit tests for the core EV math in backend/ev_calculator.py.

These tests pin the contract every scraper depends on. If any of these
fail, EVs across the whole app are wrong."""
from __future__ import annotations
import math
import pytest

from backend.ev_calculator import (
    ANNUITY_DEFAULT_CASH_RATIO,
    annuity_present_value,
    calculate_ev,
    calculate_jackpot_odds,
    effective_prize_value,
    find_top_prize,
    find_top_tier,
    parse_prize_amount,
    parse_odds,
)


class TestParsePrizeAmount:
    @pytest.mark.parametrize("raw,expected", [
        ("$1000", 1000.0),
        ("$1,000", 1000.0),
        ("1000", 1000.0),
        ("$1,000,000", 1_000_000.0),
        ("$5K", 5_000.0),
        ("$10K", 10_000.0),
        ("$1M", 1_000_000.0),
        ("$2.5M", 2_500_000.0),
        ("$1B", 1_000_000_000.0),
        ("  $5,000  ", 5_000.0),
    ])
    def test_valid(self, raw, expected):
        assert parse_prize_amount(raw) == expected

    @pytest.mark.parametrize("raw", [
        None, "", "FOR LIFE", "$10K/Wk/Life", "$2,500 WK/LIFE",
        "$50,000YR/LIFE", "abc", "$"
    ])
    def test_invalid_returns_none(self, raw):
        assert parse_prize_amount(raw) is None


class TestParseOdds:
    @pytest.mark.parametrize("raw,expected", [
        ("1 in 5", 5.0),
        ("1 in 5.5", 5.5),
        ("1-in-5", 5.0),
        ("1:5", 5.0),
        ("5", 5.0),
        ("1 in 1,000", 1000.0),
        ("1 in 8,163,900.00", 8_163_900.0),
    ])
    def test_valid(self, raw, expected):
        assert parse_odds(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "abc", "1 in abc"])
    def test_invalid_returns_none(self, raw):
        assert parse_odds(raw) is None


class TestAnnuityPresentValue:
    def test_zero_annual(self):
        assert annuity_present_value(0, 20) == 0.0

    def test_zero_years(self):
        assert annuity_present_value(10000, 0) == 0.0

    def test_known_value_4pct_20yr(self):
        # PV of $1/yr for 20 years at 4% ≈ 13.5903
        pv = annuity_present_value(1, 20, rate=0.04)
        assert pv == pytest.approx(13.5903, rel=1e-3)

    def test_known_for_life_10k_per_week(self):
        # $10K/wk = $520K/yr, 20yr @ 4% NPV ≈ $7.067M
        annual = 10_000 * 52
        pv = annuity_present_value(annual, 20, rate=0.04)
        assert pv == pytest.approx(7_066_969.7, rel=1e-4)

    def test_zero_rate_is_nominal_total(self):
        assert annuity_present_value(10_000, 20, rate=0) == 200_000


class TestEffectivePrizeValue:
    def test_non_annuity_returns_face(self):
        assert effective_prize_value({"prize_amount": 500}) == 500

    def test_annuity_prefers_explicit_cash_value(self):
        tier = {"prize_amount": 10_000, "is_annuity": True, "cash_value": 7_000_000}
        assert effective_prize_value(tier) == 7_000_000

    def test_annuity_falls_back_to_annual_pv(self):
        tier = {
            "prize_amount": 10_000, "is_annuity": True,
            "annuity_annual": 520_000, "annuity_years": 20,
        }
        assert effective_prize_value(tier) == pytest.approx(7_066_969.7, rel=1e-4)

    def test_annuity_fallback_uses_default_ratio_when_no_pv_data(self):
        tier = {"prize_amount": 1000, "is_annuity": True}
        assert effective_prize_value(tier) == pytest.approx(1000 * ANNUITY_DEFAULT_CASH_RATIO)

    def test_missing_prize_returns_zero(self):
        assert effective_prize_value({}) == 0


class TestFindTopTier:
    def test_picks_highest_face_when_no_annuity(self):
        tiers = [{"prize_amount": 100}, {"prize_amount": 500}, {"prize_amount": 250}]
        assert find_top_tier(tiers)["prize_amount"] == 500

    def test_annuity_tier_wins_tie_via_npv(self):
        """The bug we fixed for NY/FL: for-life face = $10K, cash tier = $10K. The
        annuity tier (NPV $7M) should be the headline tier, not the cash tier."""
        cash = {"prize_amount": 10_000, "prizes_remaining": 53}
        for_life = {"prize_amount": 10_000, "is_annuity": True, "cash_value": 7_066_970,
                    "prizes_remaining": 0}
        assert find_top_tier([cash, for_life]) is for_life
        assert find_top_tier([for_life, cash]) is for_life

    def test_empty(self):
        assert find_top_tier([]) == {}


class TestFindTopPrize:
    def test_returns_face_and_remaining(self):
        tiers = [{"prize_amount": 100, "prizes_remaining": 5},
                 {"prize_amount": 500, "prizes_remaining": 1}]
        assert find_top_prize(tiers) == (500, 1)

    def test_returns_face_not_cash_value_for_annuity(self):
        """Convention: top_prize is the displayed face (per-period amount or
        published prize-table value), top_prize_cash_value carries the NPV."""
        tiers = [{"prize_amount": 10_000, "is_annuity": True, "cash_value": 7_000_000,
                  "prizes_remaining": 2}]
        face, rem = find_top_prize(tiers)
        assert face == 10_000
        assert rem == 2


class TestCalculateEV:
    def test_simple_single_tier(self):
        tiers = [{"prize_amount": 5, "odds_one_in": 5, "prizes_total": 100,
                  "prizes_remaining": 100}]
        result = calculate_ev(price=1, tiers=tiers, tickets_remaining=500)
        # 100 prizes × $5 / 500 tickets = $1 gross; net = 0
        assert result["ev"] == pytest.approx(0, abs=1e-4)
        assert result["return_pct"] == pytest.approx(100.0, rel=1e-3)

    def test_uses_cash_value_for_annuity(self):
        """For-life tier with NPV $7M contributes via cash_value, not face."""
        for_life = {
            "prize_amount": 10_000, "is_annuity": True, "cash_value": 7_000_000,
            "odds_one_in": 1_000_000, "prizes_total": 4, "prizes_remaining": 2,
        }
        result = calculate_ev(price=20, tiers=[for_life], tickets_remaining=1_000_000)
        # 2 prizes × $7M / 1M tickets = $14 gross; net = -$6
        assert result["ev"] == pytest.approx(-6, abs=0.01)

    def test_zero_price_returns_none(self):
        result = calculate_ev(price=0, tiers=[{"prize_amount": 5, "odds_one_in": 5}], tickets_remaining=100)
        assert result == {"ev": None, "return_pct": None}

    def test_falls_back_to_odds_when_remaining_missing(self):
        """Without tickets_remaining or prizes_remaining, use 1/odds_one_in."""
        tiers = [{"prize_amount": 5, "odds_one_in": 5}]
        result = calculate_ev(price=1, tiers=tiers, tickets_remaining=None)
        # 1/5 × $5 = $1 gross; net = 0
        assert result["ev"] == pytest.approx(0, abs=1e-4)


class TestJackpotOdds:
    def test_no_million_dollar_tiers_returns_none(self):
        tiers = [{"prize_amount": 100, "odds_one_in": 50, "prizes_remaining": 100}]
        assert calculate_jackpot_odds(tiers, tickets_remaining=10_000) is None

    def test_uses_remaining_when_available(self):
        tiers = [{"prize_amount": 1_000_000, "prizes_remaining": 2, "odds_one_in": 5_000_000}]
        # 10_000_000 tickets / 2 remaining = 5_000_000
        odds = calculate_jackpot_odds(tiers, tickets_remaining=10_000_000)
        assert odds == pytest.approx(5_000_000, rel=1e-3)
