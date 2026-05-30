"""End-to-end regression tests for the specific for-life bugs we shipped fixes
for. Each test drives the real scraper's parsing function with a fixture row
that mirrors what the state API actually returns, and asserts the output
matches the expected (post-fix) shape.

These tests are the safety net: a future scraper edit that silently re-drops
the for-life tier — or a regex change that breaks one state's encoding — will
turn red here BEFORE shipping.

Fixture rows are pinned snapshots from production API responses (captured
2026-05-30). When a state changes its API, update the fixture AND verify the
expected EV by hand."""
from __future__ import annotations
import pytest

from backend.scraper.states.new_york import NewYorkScraper
from backend.scraper.states.florida import FloridaScraper
from backend.scraper.states.georgia import GeorgiaScraper


# ──────────────────────────────────────────────────────────────────────────
# NY: for-life encoded as "$10K/Wk/Life" in prize_amount string
# ──────────────────────────────────────────────────────────────────────────

NY_10K_A_WEEK_FOR_LIFE_ROW = {
    "title": "$10,000 A WEEK FOR LIFE",
    "nid": "9999",
    "game_number": "9999",
    "ticket_price": "20.00",
    "overall_odds": "1 in 3.74",
    "alias": "/scratch-off-games/test",
    "art": [],
    "odds_prizes": [
        {"prize_amount": "$10K/Wk/Life", "overall_odds": "1 in 8,137,133.33",
         "prizes_remaining": "0", "prizes_paid_out": "3"},
        {"prize_amount": "$10,000", "overall_odds": "1 in 93,530.27",
         "prizes_remaining": "53", "prizes_paid_out": "208"},
        {"prize_amount": "$2,500", "overall_odds": "1 in 29,446.80",
         "prizes_remaining": "165", "prizes_paid_out": "664"},
        {"prize_amount": "$500", "overall_odds": "1 in 639.26",
         "prizes_remaining": "7230", "prizes_paid_out": "30957"},
        {"prize_amount": "$200", "overall_odds": "1 in 100.07",
         "prizes_remaining": "46204", "prizes_paid_out": "197740"},
        {"prize_amount": "$100", "overall_odds": "1 in 35.71",
         "prizes_remaining": "128504", "prizes_paid_out": "555068"},
        {"prize_amount": "$50", "overall_odds": "1 in 19.23",
         "prizes_remaining": "251427", "prizes_paid_out": "1018137"},
        {"prize_amount": "$40", "overall_odds": "1 in 13.16",
         "prizes_remaining": "360234", "prizes_paid_out": "1494843"},
        {"prize_amount": "$20", "overall_odds": "1 in 10.00",
         "prizes_remaining": "489342", "prizes_paid_out": "1951483"},
    ],
}


class TestNewYork10kAWeekForLife:
    """Pre-fix bug: $10K/Wk/Life string dropped → heuristic NPV'd the $10K cash
    tier (208 paid + 53 remaining) → return_pct 455%."""

    @pytest.fixture
    def game(self):
        return NewYorkScraper()._parse_game(NY_10K_A_WEEK_FOR_LIFE_ROW)

    def test_returns_a_game(self, game):
        assert game is not None

    def test_for_life_tier_recognized(self, game):
        """The for-life tier must be present and is_annuity-marked."""
        for_life_tiers = [t for t in game["tiers"] if t.get("is_annuity")]
        assert len(for_life_tiers) == 1
        ft = for_life_tiers[0]
        assert ft["prize_amount"] == 10_000  # per-period face preserved
        assert ft["annuity_annual"] == 10_000 * 52
        assert ft["cash_value"] == pytest.approx(7_066_969.7, rel=1e-4)

    def test_cash_10k_tier_not_marked_annuity(self, game):
        """The 53-remaining $10K CASH tier must NOT be annuity-marked. If it
        were, NPV would explode EV. Identify it by total ≥ 100 (vs for-life's 3)."""
        cash_tiers = [t for t in game["tiers"]
                      if t["prize_amount"] == 10_000 and not t.get("is_annuity")]
        assert len(cash_tiers) == 1
        assert cash_tiers[0]["prizes_remaining"] == 53

    def test_return_pct_in_sane_range(self, game):
        """Pre-fix return was 455%. Post-fix should be 50-90% (typical lottery)."""
        assert 40 <= game["return_pct"] <= 100, f"return_pct={game['return_pct']}"

    def test_top_prize_face_preserved(self, game):
        """Top displayed prize stays at the per-period face ($10K) per the
        published-prize-table convention."""
        assert game["top_prize"] == 10_000
        assert game["top_prize_is_annuity"] is True
        assert game["top_prize_cash_value"] == pytest.approx(7_066_969.7, rel=1e-4)


# ──────────────────────────────────────────────────────────────────────────
# FL: for-life encoded as "$2,500 WK/LIFE" (space separator), "$50,000YR/LIFE"
# ──────────────────────────────────────────────────────────────────────────

FL_2500_A_WEEK_ROW = {
    "GameName": "$2,500 A WEEK FOR LIFE",
    "Id": "9999",
    "TicketPrice": 5,
    "OverallOdds": 3.96,
    "EndDate": "9999-01-01",
    "OddsTiers": [
        {"PrizeAmount": "$2,500 WK/LIFE", "WinningOdds": "1-in-7760595",
         "TotalPrizes": 4, "PrizesRemaining": 1},
        {"PrizeAmount": "$10,000.00", "WinningOdds": "1-in-388030",
         "TotalPrizes": 80, "PrizesRemaining": 19},
        {"PrizeAmount": "$2,000.00", "WinningOdds": "1-in-59468",
         "TotalPrizes": 522, "PrizesRemaining": 117},
        {"PrizeAmount": "$1,000.00", "WinningOdds": "1-in-9998",
         "TotalPrizes": 3105, "PrizesRemaining": 724},
        {"PrizeAmount": "$500.00", "WinningOdds": "1-in-2397",
         "TotalPrizes": 12948, "PrizesRemaining": 2784},
        {"PrizeAmount": "$100.00", "WinningOdds": "1-in-240",
         "TotalPrizes": 129282, "PrizesRemaining": 28667},
        {"PrizeAmount": "$50.00", "WinningOdds": "1-in-240",
         "TotalPrizes": 129535, "PrizesRemaining": 28602},
        {"PrizeAmount": "$20.00", "WinningOdds": "1-in-60",
         "TotalPrizes": 517373, "PrizesRemaining": 116839},
        {"PrizeAmount": "$10.00", "WinningOdds": "1-in-9",
         "TotalPrizes": 3621610, "PrizesRemaining": 838867},
        {"PrizeAmount": "$5.00", "WinningOdds": "1-in-10",
         "TotalPrizes": 3104292, "PrizesRemaining": 782676},
    ],
}


class TestFlorida2500AWeekForLife:
    """Pre-fix bug: "$2,500 WK/LIFE" tier dropped → heuristic NPV'd $10K cash
    tier (80 printed) → return_pct 153%."""

    @pytest.fixture
    def game(self):
        return FloridaScraper()._parse_game(FL_2500_A_WEEK_ROW, image_map={})

    def test_for_life_tier_recognized(self, game):
        for_life_tiers = [t for t in game["tiers"] if t.get("is_annuity")]
        assert len(for_life_tiers) == 1
        ft = for_life_tiers[0]
        assert ft["prize_amount"] == 2_500
        assert ft["annuity_annual"] == 2_500 * 52
        assert ft["cash_value"] == pytest.approx(1_766_742.42, rel=1e-4)

    def test_cash_10k_tier_not_marked_annuity(self, game):
        """The 80-printed $10K cash tier — the one that NPV-exploded pre-fix —
        must stay a plain cash tier."""
        cash = [t for t in game["tiers"]
                if t["prize_amount"] == 10_000 and not t.get("is_annuity")]
        assert len(cash) == 1
        assert cash[0]["prizes_total"] == 80

    def test_return_pct_in_sane_range(self, game):
        assert 40 <= game["return_pct"] <= 100, f"return_pct={game['return_pct']}"


FL_50K_A_YEAR_ROW = {
    "GameName": "$50K A YR FOR LIFE",
    "Id": "9998",
    "TicketPrice": 2,
    "OverallOdds": 4.43,
    "EndDate": "9999-01-01",
    "OddsTiers": [
        # Edge case: no separator between amount and period — "$50,000YR/LIFE"
        {"PrizeAmount": "$50,000YR/LIFE", "WinningOdds": "1-in-4815975",
         "TotalPrizes": 8, "PrizesRemaining": 2},
        {"PrizeAmount": "$10,000.00", "WinningOdds": "1-in-566585",
         "TotalPrizes": 68, "PrizesRemaining": 20},
        {"PrizeAmount": "$2.00", "WinningOdds": "1-in-10",
         "TotalPrizes": 3852888, "PrizesRemaining": 1297851},
    ],
}


class TestFlorida50kAYearNoSeparator:
    """Tightest FL edge case: "$50,000YR/LIFE" has no character between the
    amount and the period word. This was a real production tier shape."""

    def test_parses_yearly_for_life(self):
        game = FloridaScraper()._parse_game(FL_50K_A_YEAR_ROW, image_map={})
        for_life_tiers = [t for t in game["tiers"] if t.get("is_annuity")]
        assert len(for_life_tiers) == 1
        ft = for_life_tiers[0]
        assert ft["prize_amount"] == 50_000
        assert ft["annuity_annual"] == 50_000  # yearly, not weekly


# ──────────────────────────────────────────────────────────────────────────
# GA: for-life encoded as prizeAmount=0 (sentinel) + name-based reconstruction
# ──────────────────────────────────────────────────────────────────────────

GA_WIN_1K_MONTH_ROW = {
    "gameId": "9999",
    "gameName": "WIN $1,000 A MONTH FOR LIFE",
    "validationStatus": "ACTIVE",
    "ticketPrice": 200,
    "disableDate": 9999999999999,
    "prizeTiers": [
        {"tierNumber": 1, "prizeAmount": 20000, "winningTickets": 585346, "paidTickets": 502206},
        {"tierNumber": 2, "prizeAmount": 30000, "winningTickets": 292648, "paidTickets": 259144},
        {"tierNumber": 3, "prizeAmount": 1000000, "winningTickets": 1950, "paidTickets": 1731},
        {"tierNumber": 4, "prizeAmount": 5000000, "winningTickets": 487, "paidTickets": 421},
        # for-life tier — GA encodes it as prizeAmount=0
        {"tierNumber": 5, "prizeAmount": 0, "winningTickets": 4, "paidTickets": 2},
    ],
}


GA_NON_FOR_LIFE_ZERO_TIER_ROW = {
    "gameId": "9998",
    "gameName": "MILLION DOLLAR GIVEAWAY!",
    "validationStatus": "ACTIVE",
    "ticketPrice": 3000,
    "disableDate": 9999999999999,
    "prizeTiers": [
        {"tierNumber": 1, "prizeAmount": 30000, "winningTickets": 100000, "paidTickets": 50000},
        {"tierNumber": 2, "prizeAmount": 500000000, "winningTickets": 6, "paidTickets": 6},
        # Zero tier with 19 prizes — likely $1M each, but NOT a for-life prize.
        # We can't reconstruct without external data, so we drop it. EV
        # understates rather than explodes.
        {"tierNumber": 3, "prizeAmount": 0, "winningTickets": 19, "paidTickets": 18},
    ],
}


class TestGeorgiaWin1kPerMonthForLife:
    """Pre-fix bug: GA encodes the for-life as prizeAmount=0. Scraper dropped
    it. Heuristic then NPV'd the $500 cash tier (487 printed). return_pct 829%."""

    @pytest.fixture
    def game(self):
        return GeorgiaScraper()._parse_game(GA_WIN_1K_MONTH_ROW, odds_map={"9999": 3.97})

    def test_for_life_tier_reconstructed_from_zero_tier(self, game):
        for_life_tiers = [t for t in game["tiers"] if t.get("is_annuity")]
        assert len(for_life_tiers) == 1
        ft = for_life_tiers[0]
        assert ft["prize_amount"] == 1_000        # per-period from name
        assert ft["annuity_annual"] == 1_000 * 12  # monthly
        assert ft["cash_value"] == pytest.approx(163_083.92, rel=1e-4)
        # Counts come from the zero-tier
        assert ft["prizes_total"] == 4
        assert ft["prizes_remaining"] == 2

    def test_cash_500_tier_not_marked_annuity(self, game):
        """The $500 cash tier (487 printed) — the one that NPV-exploded
        pre-fix — must stay a plain cash tier."""
        cash = [t for t in game["tiers"]
                if t["prize_amount"] == 500 and not t.get("is_annuity")]
        assert len(cash) == 1
        assert cash[0]["prizes_total"] == 487

    def test_return_pct_in_sane_range(self, game):
        assert 40 <= game["return_pct"] <= 100, f"return_pct={game['return_pct']}"


class TestGeorgiaNonForLifeZeroTier:
    """Counter-test: GA games with zero-tier that aren't for-life (e.g.
    'MILLION DOLLAR GIVEAWAY') must NOT have those tiers reconstructed —
    we'd be inventing prize amounts. They stay dropped; EV understates."""

    def test_zero_tier_dropped_when_name_not_for_life(self):
        game = GeorgiaScraper()._parse_game(
            GA_NON_FOR_LIFE_ZERO_TIER_ROW, odds_map={"9998": 3.5}
        )
        # No tier should be annuity-marked
        for_life_tiers = [t for t in game["tiers"] if t.get("is_annuity")]
        assert for_life_tiers == []
        # 19-prize zero tier dropped: total kept tiers' prizes_total should
        # exclude it
        all_totals = [t.get("prizes_total") for t in game["tiers"]]
        assert 19 not in all_totals
