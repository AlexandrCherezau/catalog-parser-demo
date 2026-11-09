"""Crawl orchestration: listing pages -> product pages -> Book records."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from .fetcher import Fetcher
from .models import Book
from .parsing import ParseError, parse_listing, parse_product

log = logging.getLogger(__name__)

BASE_URL = "https://books.toscrape.com/"
TOTAL_PAGES = 50

ProgressHook = Callable[[int], None]


def listing_url(page: int) -> str:
    return f"{BASE_URL}catalogue/page-{page}.html"


async def collect_product_urls(
    fetcher: Fetcher, pages: int, on_progress: ProgressHook | None = None
) -> list[str]:
    """Fetch listing pages 1..pages concurrently, keep catalogue order, dedupe."""

    async def one(page: int) -> list[str]:
        url = listing_url(page)
        html = await fetcher.get(url)
        if on_progress:
            on_progress(1)
        if html is None:
            return []
        links, _ = parse_listing(html, url)
        return links

    per_page = await asyncio.gather(*(one(p) for p in range(1, pages + 1)))
    seen: set[str] = set()
    ordered: list[str] = []
    for links in per_page:
        for link in links:
            if link not in seen:
                seen.add(link)
                ordered.append(link)
    return ordered


async def scrape_products(
    fetcher: Fetcher, urls: list[str], on_progress: ProgressHook | None = None
) -> tuple[list[Book], list[str]]:
    """Fetch + parse product pages. Returns (books in input order, parse-failed urls)."""
    parse_failed: list[str] = []

    async def one(url: str) -> Book | None:
        html = await fetcher.get(url)
        if on_progress:
            on_progress(1)
        if html is None:
            return None
        try:
            return parse_product(html, url)
        except ParseError as exc:
            log.warning("parse error on %s: %s", url, exc)
            parse_failed.append(url)
            return None

    results = await asyncio.gather(*(one(u) for u in urls))
    return [b for b in results if b is not None], parse_failed
