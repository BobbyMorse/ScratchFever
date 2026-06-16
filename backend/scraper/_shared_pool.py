"""
Shared ThreadPoolExecutor for state-scraper detail-page fetches.

Why this exists: every state scraper that fanned out detail fetches used to
open its own `with ThreadPoolExecutor(max_workers=N) as pool:` block. With
HTTP_CONCURRENCY=4 state scrapers running in parallel and per-state pools
sized 5-10, the burst could create ~30+ inner worker threads. Each request
also spawns transient curl_cffi/getaddrinfo DNS threads. On Railway's 512MB
dyno this was repeatedly hitting `pthread_create` EAGAIN, surfaced as
"can't start new thread" failures on whichever state happened to be next.

A single, process-lifetime pool fixes both problems:
- threads are created once and reused, so there's no per-cycle churn
- total inner parallelism is hard-capped regardless of how many states are
  scraping simultaneously

8 workers is the same cap as the asyncio default executor in scraper_worker,
keeping the two thread pools symmetric and predictable.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor

DETAIL_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="scraper-detail")
