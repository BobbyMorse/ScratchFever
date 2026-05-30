"""Tests for the name-pattern annuity heuristic in backend/scraper/base.py.

The heuristic exists for states that don't pre-mark for-life tiers (MA via
HTML scraping, mainly). Every variation here was either a real production bug
or a real production case the heuristic must continue to handle correctly."""
from __future__ import annotations
import pytest

from backend.scraper.base import _apply_annuity_heuristic


class TestHeuristicApplies:
    def test_ma_style_face_is_lifetime_total(self):
        """MA encodes the for-life tier with face = lifetime nominal payout
        (~$80K for $100/wk × 15 yrs). The heuristic should still mark it as
        annuity and attach NPV cash_value."""
        tiers = [
            {"prize_amount": 80_000, "prizes_total": 1, "prizes_remaining": 1},
            {"prize_amount": 5_000, "prizes_total": 20, "prizes_remaining": 5},
        ]
        _apply_annuity_heuristic("$100 A WEEK FOR LIFE", tiers)
        top = tiers[0]
        assert top["is_annuity"] is True
        assert top["annuity_annual"] == 100 * 52
        assert top["annuity_years"] == 20
        assert top["cash_value"] == pytest.approx(70_669.7, rel=1e-3)

    def test_k_suffix_in_name(self):
        """'$10K A WEEK FOR LIFE' must extract per-period $10,000 and compute NPV.
        Face is the lifetime nominal payout (~NPV scale) so the ratio guard
        doesn't fire — same shape as MA's real face/NPV relationship."""
        tiers = [{"prize_amount": 10_000_000, "prizes_total": 1, "prizes_remaining": 1}]
        _apply_annuity_heuristic("$10K A WEEK FOR LIFE", tiers)
        assert tiers[0]["is_annuity"] is True
        assert tiers[0]["annuity_annual"] == 10_000 * 52

    def test_monthly_pattern(self):
        tiers = [{"prize_amount": 50_000, "prizes_total": 1, "prizes_remaining": 1}]
        _apply_annuity_heuristic("$1,000 A MONTH FOR LIFE", tiers)
        assert tiers[0]["is_annuity"] is True
        assert tiers[0]["annuity_annual"] == 1_000 * 12

    def test_yearly_for_n_years(self):
        """'$X A YEAR FOR 25 YEARS' captures the year count from the regex."""
        tiers = [{"prize_amount": 100_000, "prizes_total": 1, "prizes_remaining": 1}]
        _apply_annuity_heuristic("$5,000 A YEAR FOR 25 YEARS", tiers)
        assert tiers[0]["is_annuity"] is True
        assert tiers[0]["annuity_years"] == 25


class TestHeuristicSkips:
    def test_no_match_in_name(self):
        tiers = [{"prize_amount": 100_000, "prizes_total": 1, "prizes_remaining": 1}]
        _apply_annuity_heuristic("Million Dollar Giveaway", tiers)
        assert "is_annuity" not in tiers[0]

    def test_scraper_pre_marked_no_double_apply(self):
        """When a scraper already pre-marked is_annuity (NY/FL/GA cases), the
        heuristic must NOT re-apply NPV to a different tier."""
        for_life = {"prize_amount": 10_000, "is_annuity": True, "cash_value": 7_066_970,
                    "annuity_annual": 520_000, "annuity_years": 20,
                    "prizes_total": 4, "prizes_remaining": 1}
        cash = {"prize_amount": 10_000, "prizes_total": 80, "prizes_remaining": 19}
        tiers = [for_life, cash]
        _apply_annuity_heuristic("$10,000 A WEEK FOR LIFE", tiers)
        # cash tier must not have been marked
        assert "is_annuity" not in cash
        # for_life tier untouched
        assert for_life["cash_value"] == 7_066_970

    def test_ratio_guard_skips_when_npv_dwarfs_top_face(self):
        """The guard against the WA-class bug: if the real for-life tier was
        dropped, the heuristic sees a much smaller cash tier as 'top'. NPV /
        face > 10 means we'd be NPV-exploding a regular cash prize. Skip."""
        # Game name says $10K A WEEK FOR LIFE → NPV ~$7M, but only a $1,000
        # cash tier is present (for-life was silently dropped).
        cash_only = {"prize_amount": 1_000, "prizes_total": 100, "prizes_remaining": 50}
        _apply_annuity_heuristic("$10,000 A WEEK FOR LIFE", [cash_only])
        assert "is_annuity" not in cash_only

    def test_ratio_guard_allows_ma_ratios(self):
        """MA-class games have face ≈ NPV (ratio < 2). Guard must NOT fire."""
        ma_tier = {"prize_amount": 80_000, "prizes_total": 1, "prizes_remaining": 1}
        _apply_annuity_heuristic("$100 A WEEK FOR LIFE", [ma_tier])
        assert ma_tier["is_annuity"] is True

    def test_empty_inputs(self):
        _apply_annuity_heuristic("", [])
        _apply_annuity_heuristic("Some Game", [])
        # no exceptions, no mutation


class TestRegexCoverage:
    """Names observed in real state data — must match the heuristic regex.
    Face values here are sized large enough that the ratio guard never fires,
    so the test isolates regex coverage from the guard."""
    @pytest.mark.parametrize("name", [
        "$100 A WEEK FOR LIFE",
        "$200 A WEEK FOR LIFE",
        "$1,000 A WEEK FOR LIFE",
        "$2,500 A WEEK FOR LIFE",
        "$10,000 A WEEK FOR LIFE",
        "$10K A WEEK FOR LIFE",
        "$5,000 a week for life",
        "Win $1,000 A Month For Life",
        "$50,000 A YEAR FOR LIFE",
    ])
    def test_match(self, name):
        big_tier = {"prize_amount": 100_000_000, "prizes_total": 1, "prizes_remaining": 1}
        _apply_annuity_heuristic(name, [big_tier])
        assert big_tier.get("is_annuity") is True, f"regex failed to match: {name!r}"

    @pytest.mark.parametrize("name", [
        "Million Dollar Giveaway",
        "300X The Money",
        "Holiday Cash Blowout",
        "Set For Life",  # no $-amount → no match (heuristic relies on amount in name)
    ])
    def test_no_match(self, name):
        big_tier = {"prize_amount": 100_000_000, "prizes_total": 1, "prizes_remaining": 1}
        _apply_annuity_heuristic(name, [big_tier])
        assert "is_annuity" not in big_tier
