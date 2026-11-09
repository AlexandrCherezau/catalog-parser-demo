"""Polite async HTTP fetcher: concurrency limit, retries with backoff, disk cache."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
USER_AGENT = "catalog-parser/1.0 (+portfolio demo; polite crawler)"


@dataclass
class FetchStats:
    network: int = 0
    cache_hits: int = 0
    retries: int = 0
    failures: int = 0
    bytes: int = 0
    failed_urls: list[str] = field(default_factory=list)


class HtmlCache:
    """Stores raw HTML on disk keyed by URL hash — gives free resume after a crash."""

    def __init__(self, directory: Path | None):
        self.directory = directory
        if directory:
            directory.mkdir(parents=True, exist_ok=True)

    def _path(self, url: str) -> Path:
        assert self.directory is not None
        return self.directory / (hashlib.sha1(url.encode()).hexdigest() + ".html")

    def get(self, url: str) -> str | None:
        if not self.directory:
            return None
        path = self._path(url)
        return path.read_text(encoding="utf-8") if path.exists() else None

    def put(self, url: str, html: str) -> None:
        if not self.directory:
            return
        path = self._path(url)
        tmp = path.with_suffix(".part")
        tmp.write_text(html, encoding="utf-8")
        tmp.replace(path)  # atomic: no half-written files on interrupt


class Fetcher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        cache: HtmlCache,
        concurrency: int = 8,
        max_retries: int = 4,
        backoff_base: float = 0.5,
        delay: float = 0.0,
    ):
        self.client = client
        self.cache = cache
        self.semaphore = asyncio.Semaphore(concurrency)
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.delay = delay
        self.stats = FetchStats()

    async def get(self, url: str) -> str | None:
        """Return page HTML, or None after all retries are exhausted."""
        cached = self.cache.get(url)
        if cached is not None:
            self.stats.cache_hits += 1
            return cached

        for attempt in range(self.max_retries + 1):
            try:
                async with self.semaphore:
                    response = await self.client.get(url)
                    if self.delay:
                        await asyncio.sleep(self.delay)
                if response.status_code in RETRYABLE_STATUS:
                    raise httpx.HTTPStatusError(
                        f"retryable status {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status is not None and status not in RETRYABLE_STATUS:
                    log.warning("GET %s -> %s (not retryable)", url, status)
                    break
                if isinstance(exc, httpx.RequestError) and not isinstance(exc, httpx.TransportError):
                    log.warning("GET %s failed (not retryable): %s", url, exc)
                    break  # e.g. redirect loop, broken encoding
                if attempt == self.max_retries:
                    log.warning("GET %s failed after %d attempts: %s", url, attempt + 1, exc)
                    break
                self.stats.retries += 1
                wait = self.backoff_base * (2**attempt) + random.uniform(0, 0.3)
                log.info("retry %d for %s in %.1fs (%s)", attempt + 1, url, wait, exc)
                await asyncio.sleep(wait)
                continue

            html = response.content.decode("utf-8", errors="replace")
            self.stats.network += 1
            self.stats.bytes += len(response.content)
            self.cache.put(url, html)
            return html

        self.stats.failures += 1
        self.stats.failed_urls.append(url)
        return None
