# EV calculation tests

This suite is the safety net for EV math and scraper parsing. Every time we
shipped a wrong number, the root cause fit one of these patterns:

1. A state's API encodes a prize tier in a format our parser can't read, so
   the tier silently drops.
2. The base annuity heuristic then misidentifies a regular cash tier as the
   for-life prize and applies NPV conversion to it.
3. Nothing catches the resulting nonsense before it reaches production.

The tests below pin each layer so a future scraper edit can't silently
regress the math.

## Layout

- `test_ev_calculator.py` — pure math (`parse_prize_amount`, `parse_odds`,
  `annuity_present_value`, `effective_prize_value`, `calculate_ev`,
  `find_top_tier`/`find_top_prize`, `calculate_jackpot_odds`).
- `test_for_life_parser.py` — the shared `parse_for_life_tier` that decodes
  per-tier for-life prize strings (NY's `$10K/Wk/Life`, FL's
  `$2,500 WK/LIFE`, FL's `$50,000YR/LIFE`).
- `test_annuity_heuristic.py` — the name-based heuristic and its three
  guards: (a) skip if any tier pre-marked annuity, (b) ratio guard against
  NPV-exploding the wrong tier when the real for-life tier was dropped,
  (c) regex coverage for K/M/B suffixes and capitalization variants.
- `test_scraper_for_life_regression.py` — end-to-end fixtures for the
  specific shipped bugs in NY, FL, and GA. Each fixture mirrors a real API
  response captured 2026-05-30; expectations check both the tier shape and
  the resulting `return_pct`/`top_prize`/`top_prize_is_annuity` outputs.
- `test_sanity_warnings.py` — the post-build warning checks (`return_pct >
  300%`, for-life name with no annuity tier, `ev > 5×price`).

## Running

```
pytest tests/
```

## Adding a new state with a for-life game

1. Hit the state's prize API and locate the for-life tier. Capture the raw
   `prize_amount` string and the count fields.
2. **Add a fixture and a regression test first**
   (`test_scraper_for_life_regression.py`). The test will fail; that's
   correct — the parser hasn't been extended yet.
3. If the encoding fits `parse_for_life_tier`'s regex
   (`<amount><sep><period>/LIFE`), wire the scraper through it the same way
   `florida.py` and `new_york.py` do (NPV pre-marking).
4. If the encoding is novel, either widen `FOR_LIFE_TIER_RE` in
   `backend/scraper/base.py` (preferred — shared with other states) or
   write a state-specific parser like `_ga_for_life_from_name` in
   `georgia.py`.
5. Test passes → ship.

## Adding new sanity checks

Sanity checks live in `_check_sanity` in `backend/scraper/base.py`. There are
two tiers:

1. **Gating checks** (impossible-EV): return False, which causes `build_game`
   to null `ev`/`return_pct`/`prize_pool_left`. Use this when firing means the
   math is broken (e.g. return % > 300, ev > 5×price). Log at ERROR level so
   the rejection is visible in production scrape logs. The row still ships —
   it just can't rank or display +EV — so downstream pages get a "—" instead
   of a fake #1.
2. **Warning-only checks**: log at WARNING level for shapes that are
   suspicious but recoverable (e.g. for-life-named game with no annuity tier
   present). The row ships with its numbers intact.

Add a corresponding test in `test_sanity_warnings.py` confirming the gate
nulls fields for the buggy shape and stays silent for the correct shape.
