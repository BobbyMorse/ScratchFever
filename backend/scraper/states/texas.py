"""
Texas Lottery scratch-off scraper.
CSV: https://www.texaslottery.com/export/sites/lottery/Games/Scratch_Offs/scratchoff.csv
  Columns: Game Number, Game Name, Game Close Date, Ticket Price (whole dollars),
           Prize Level (prize amount in dollars), Total Prizes in Level, Prizes Claimed
  One row per prize tier per game; "TOTAL" row skipped.
  EV: estimated from prize pool assuming ~65% return rate (TX: ~67% mandated return).
"""
import csv
import io
import logging
from collections import defaultdict
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)

CSV_URL = "https://www.texaslottery.com/export/sites/lottery/Games/Scratch_Offs/scratchoff.csv"
BASE_URL = "https://www.texaslottery.com"
DETAIL_BASE = f"{BASE_URL}/export/sites/lottery/Games/Scratch_Offs"

_PAYOUT_RATE = 0.65


class TexasScraper(BaseScraper):
    state_code = "TX"
    state_name = "Texas"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        resp = self.get(CSV_URL)
        lines = resp.text.splitlines()

        # First line is metadata, second line is actual header
        reader = csv.reader(io.StringIO("\n".join(lines[1:])))
        next(reader)  # skip header row

        # Group rows by game number
        games_raw = defaultdict(list)
        for row in reader:
            if len(row) < 7:
                continue
            game_num = row[0].strip()
            if not game_num:
                continue
            games_raw[game_num].append(row)

        logger.info("TX: %d games in CSV", len(games_raw))

        games = []
        for game_num, rows in games_raw.items():
            game = self._parse_game(game_num, rows)
            if game:
                games.append(game)

        logger.info("TX: %d games parsed", len(games))
        return games

    def _parse_game(self, game_num: str, rows: list) -> dict | None:
        if not rows:
            return None

        name = rows[0][1].strip()
        if not name:
            return None

        try:
            price = float(rows[0][3])
        except (ValueError, IndexError):
            return None
        if price <= 0:
            return None

        tiers = []
        total_prizes_printed = 0
        total_prizes_remaining = 0
        total_prize_value = 0.0

        for row in rows:
            level = row[4].strip()
            if level.upper() == "TOTAL":
                continue
            try:
                prize = float(level)
                total = int(row[5].replace(",", ""))
                claimed = int(row[6].replace(",", ""))
            except (ValueError, IndexError):
                continue
            if prize <= 0 or total <= 0:
                continue

            remaining = max(total - claimed, 0)
            total_prizes_printed += total
            total_prizes_remaining += remaining
            total_prize_value += prize * total

            tiers.append({
                "prize_amount":     prize,
                "odds_one_in":      None,
                "prizes_total":     total,
                "prizes_remaining": remaining,
            })

        if not tiers:
            return None

        total_tickets = None
        tickets_remaining = None
        if total_prize_value > 0 and price > 0:
            total_tickets = round(total_prize_value / (_PAYOUT_RATE * price))
            if total_prizes_printed > 0:
                fraction_rem = total_prizes_remaining / total_prizes_printed
                tickets_remaining = round(total_tickets * fraction_rem)
            for t in tiers:
                if t["prizes_total"] and total_tickets:
                    t["odds_one_in"] = round(total_tickets / t["prizes_total"], 2)

        return self.build_game(
            game_id=game_num,
            name=name,
            price=price,
            tiers=tiers,
            total_tickets=total_tickets,
            tickets_remaining=tickets_remaining,
            detail_url=DETAIL_BASE,
        )
