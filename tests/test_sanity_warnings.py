"""Tests for the build_game sanity gates. Impossible-EV checks are the last
line of defense — if a scraper bug slips past unit tests, these gates null
the bad numbers so the row can't rank or display +EV in production."""
from __future__ import annotations
import logging
import pytest

from backend.scraper.base import BaseScraper


class _DummyScraper(BaseScraper):
    state_code = "XX"
    state_name = "Test"
    base_url = "https://example.test"

    def scrape(self):
        return []


@pytest.fixture
def scraper():
    return _DummyScraper()


class TestOutlierReturnPctGate:
    def test_logs_error_above_300_pct(self, scraper, caplog):
        """Recreate the pre-fix NY shape: $10K cash tier (53 remaining) wrongly
        marked annuity with $7M NPV. Return jumps above 300%."""
        tiers = [{
            "prize_amount": 10_000, "is_annuity": True, "cash_value": 7_000_000,
            "annuity_annual": 520_000, "annuity_years": 20,
            "odds_one_in": 95_000, "prizes_total": 261, "prizes_remaining": 53,
        }]
        with caplog.at_level(logging.ERROR):
            scraper.build_game(
                game_id="x", name="$10,000 A WEEK FOR LIFE",
                price=20, tiers=tiers, tickets_remaining=5_000_000,
            )
        assert any("REJECT EV" in r.message and "impossible" in r.message
                   for r in caplog.records)

    def test_gate_nulls_ev_on_impossible_return(self, scraper):
        """The 2026-06-11 MN bug: price parsed as $1 instead of $10 on
        "Red Hot $1,000's" → 698.1% return → fake #1 ranking. The gate must
        null ev/return_pct/prize_pool_left so the row can't rank or feature."""
        tiers = [
            {"prize_amount": 10, "odds_one_in": 7.69, "prizes_total": 253564},
            {"prize_amount": 20, "odds_one_in": 15.38, "prizes_total": 126786},
            {"prize_amount": 1000, "odds_one_in": 770.3, "prizes_total": 2532},
        ]
        game = scraper.build_game(
            game_id="red-hot-1-000s", name="Red Hot $1,000's",
            price=1.0, tiers=tiers,
        )
        assert game["ev"] is None
        assert game["return_pct"] is None
        assert game["prize_pool_left"] is None
        # Game still ships — top prize info preserved for display
        assert game["top_prize"] == 1000
        assert game["name"] == "Red Hot $1,000's"

    def test_gate_preserves_valid_game(self, scraper):
        """Same tiers but correct price → row ships normally."""
        tiers = [
            {"prize_amount": 10, "odds_one_in": 7.69, "prizes_total": 253564},
            {"prize_amount": 20, "odds_one_in": 15.38, "prizes_total": 126786},
            {"prize_amount": 1000, "odds_one_in": 770.3, "prizes_total": 2532},
        ]
        game = scraper.build_game(
            game_id="red-hot-1-000s", name="Red Hot $1,000's",
            price=10.0, tiers=tiers,
        )
        assert game["ev"] is not None
        assert game["return_pct"] is not None
        assert 0 < game["return_pct"] < 300

    def test_no_log_for_normal_game(self, scraper, caplog):
        tiers = [{"prize_amount": 5, "odds_one_in": 5, "prizes_total": 100,
                  "prizes_remaining": 100}]
        with caplog.at_level(logging.ERROR):
            scraper.build_game(
                game_id="x", name="Normal Game",
                price=1, tiers=tiers, tickets_remaining=500,
            )
        assert not any("REJECT EV" in r.message for r in caplog.records)


class TestForLifeWithoutAnnuityWarning:
    def test_warns_when_for_life_name_has_no_annuity_tier(self, scraper, caplog):
        """The exact pre-fix shape: game name says "FOR LIFE" but the for-life
        tier was dropped, ratio guard correctly refused to NPV-explode the
        cash tier — so we end up with no annuity at all. Warn so we know to
        extend the parser."""
        tiers = [{
            "prize_amount": 500, "odds_one_in": 10_000,
            "prizes_total": 487, "prizes_remaining": 66,
        }]
        with caplog.at_level(logging.WARNING):
            scraper.build_game(
                game_id="x", name="Win $1,000 A Month For Life",
                price=2, tiers=tiers, tickets_remaining=500_000,
            )
        assert any("no annuity tier was found" in r.message
                   for r in caplog.records)

    def test_no_warn_when_for_life_tier_present(self, scraper, caplog):
        tiers = [
            {"prize_amount": 1_000, "is_annuity": True, "cash_value": 163_084,
             "annuity_annual": 12_000, "annuity_years": 20,
             "odds_one_in": 5_000_000, "prizes_total": 4, "prizes_remaining": 2},
            {"prize_amount": 500, "odds_one_in": 10_000,
             "prizes_total": 487, "prizes_remaining": 66},
        ]
        with caplog.at_level(logging.WARNING):
            scraper.build_game(
                game_id="x", name="Win $1,000 A Month For Life",
                price=2, tiers=tiers, tickets_remaining=500_000,
            )
        assert not any("no annuity tier" in r.message for r in caplog.records)

    def test_no_warn_when_name_not_for_life(self, scraper, caplog):
        tiers = [{"prize_amount": 100, "odds_one_in": 50,
                  "prizes_total": 100, "prizes_remaining": 50}]
        with caplog.at_level(logging.WARNING):
            scraper.build_game(
                game_id="x", name="Million Dollar Giveaway",
                price=10, tiers=tiers, tickets_remaining=1000,
            )
        assert not any("for-life game but no annuity tier" in r.message
                       for r in caplog.records)
