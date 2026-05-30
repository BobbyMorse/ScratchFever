"""Tests for the build_game sanity warnings. These warnings are the last
line of defense — if a scraper bug slips past unit tests, the warning shows
up in production scrape logs."""
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


class TestOutlierReturnPctWarning:
    def test_warns_above_300_pct(self, scraper, caplog):
        """Recreate the pre-fix NY shape: $10K cash tier (53 remaining) wrongly
        marked annuity with $7M NPV. Return jumps above 300%."""
        tiers = [{
            "prize_amount": 10_000, "is_annuity": True, "cash_value": 7_000_000,
            "annuity_annual": 520_000, "annuity_years": 20,
            "odds_one_in": 95_000, "prizes_total": 261, "prizes_remaining": 53,
        }]
        with caplog.at_level(logging.WARNING):
            scraper.build_game(
                game_id="x", name="$10,000 A WEEK FOR LIFE",
                price=20, tiers=tiers, tickets_remaining=5_000_000,
            )
        assert any("looks impossibly high" in r.message for r in caplog.records)

    def test_no_warn_for_normal_game(self, scraper, caplog):
        tiers = [{"prize_amount": 5, "odds_one_in": 5, "prizes_total": 100,
                  "prizes_remaining": 100}]
        with caplog.at_level(logging.WARNING):
            scraper.build_game(
                game_id="x", name="Normal Game",
                price=1, tiers=tiers, tickets_remaining=500,
            )
        assert not any("impossibly high" in r.message for r in caplog.records)


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
