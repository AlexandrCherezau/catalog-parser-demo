"""Command-line entry point: python -m catalog_parser --pages 50 --out output"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
from tqdm import tqdm

from .crawler import TOTAL_PAGES, collect_product_urls, scrape_products
from .export import summarize, write_csv, write_json, write_xlsx
from .fetcher import USER_AGENT, Fetcher, HtmlCache
from .report import write_report

MAX_CONCURRENCY = 8  # be polite to the sandbox site
FORMATS = ("xlsx", "csv", "json", "html")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="catalog_parser",
        description="Асинхронный парсер каталога books.toscrape.com → Excel/CSV/JSON",
    )
    p.add_argument("--pages", type=int, default=TOTAL_PAGES,
                   help=f"сколько страниц каталога обойти (1..{TOTAL_PAGES}, по умолчанию все)")
    p.add_argument("--out", type=Path, default=Path("output"), help="папка для результатов")
    p.add_argument("--name", default="books", help="базовое имя файлов результата")
    p.add_argument("--formats", default="xlsx,csv,json,html",
                   help=f"через запятую из: {','.join(FORMATS)}")
    p.add_argument("--concurrency", type=int, default=8,
                   help=f"одновременных запросов (макс. {MAX_CONCURRENCY})")
    p.add_argument("--retries", type=int, default=4, help="повторов при ошибке сети/5xx")
    p.add_argument("--delay", type=float, default=0.0, help="пауза после каждого запроса, сек")
    p.add_argument("--timeout", type=float, default=20.0, help="таймаут запроса, сек")
    p.add_argument("--cache-dir", type=Path, default=Path(".cache/html"),
                   help="кэш HTML (позволяет продолжить прерванный запуск)")
    p.add_argument("--no-cache", action="store_true", help="не использовать кэш")
    p.add_argument("-v", "--verbose", action="store_true", help="подробный лог")
    return p


def validate(args: argparse.Namespace) -> list[str]:
    errors = []
    if not 1 <= args.pages <= TOTAL_PAGES:
        errors.append(f"--pages должен быть от 1 до {TOTAL_PAGES}")
    if not 1 <= args.concurrency <= MAX_CONCURRENCY:
        errors.append(f"--concurrency должен быть от 1 до {MAX_CONCURRENCY}")
    if args.retries < 0:
        errors.append("--retries не может быть отрицательным")
    unknown = set(args.formats.split(",")) - set(FORMATS)
    if unknown:
        errors.append(f"неизвестные форматы: {', '.join(sorted(unknown))}")
    return errors


async def run(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    cache = HtmlCache(None if args.no_cache else args.cache_dir)
    limits = httpx.Limits(max_connections=args.concurrency)
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT}, timeout=args.timeout, limits=limits,
        follow_redirects=True, http2=False,
    ) as client:
        fetcher = Fetcher(client, cache, args.concurrency, args.retries, delay=args.delay)

        print(f"→ Шаг 1/2: страницы каталога (1..{args.pages}), потоков: {args.concurrency}")
        with tqdm(total=args.pages, unit="стр", ncols=90, ascii=" ▏▎▍▌▋▊▉█") as bar:
            urls = await collect_product_urls(fetcher, args.pages, bar.update)
        print(f"  найдено ссылок на товары: {len(urls)}")

        print("→ Шаг 2/2: карточки товаров")
        with tqdm(total=len(urls), unit="шт", ncols=90, ascii=" ▏▎▍▌▋▊▉█") as bar:
            books, parse_failed = await scrape_products(fetcher, urls, bar.update)

    elapsed = time.perf_counter() - started
    args.out.mkdir(parents=True, exist_ok=True)
    formats = args.formats.split(",")
    writers = {
        "xlsx": lambda p: write_xlsx(books, p),
        "csv": lambda p: write_csv(books, p),
        "json": lambda p: write_json(books, p),
        "html": lambda p: write_report(books, p, datetime.now().strftime("%d.%m.%Y %H:%M")),
    }
    written = []
    for fmt in formats:
        path = args.out / (f"{args.name}_summary.html" if fmt == "html" else f"{args.name}.{fmt}")
        writers[fmt](path)
        written.append(path)

    print_summary(books, fetcher.stats, parse_failed, elapsed, written)
    return 0 if books and not fetcher.stats.failures and not parse_failed else 1


def print_summary(books, stats, parse_failed, elapsed, written) -> None:
    cats = summarize(books)
    print("\n══════════════ ИТОГ ══════════════")
    print(f"  Книг собрано:      {len(books)}")
    print(f"  Категорий:         {len(cats)}")
    if books:
        print(f"  Средняя цена:      £{sum(b.price for b in books) / len(books):.2f}")
        print(f"  В наличии, шт:     {sum(b.stock_count for b in books)}")
    print(f"  HTTP-запросов:     {stats.network}  (из кэша: {stats.cache_hits}, "
          f"повторов: {stats.retries}, ошибок: {stats.failures})")
    print(f"  Скачано:           {stats.bytes / 1024 / 1024:.1f} МБ")
    print(f"  Ошибок разбора:    {len(parse_failed)}")
    print(f"  Время:             {elapsed:.1f} с")
    print("  Топ-5 категорий:")
    for c in cats[:5]:
        print(f"    {c.category:<20} {c.count:>4} книг   ср. цена £{c.avg_price:.2f}")
    print("  Файлы:")
    for path in written:
        print(f"    {path}")
    for url in stats.failed_urls + parse_failed:
        print(f"  ! не удалось: {url}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    errors = validate(args)
    if errors:
        for e in errors:
            print(f"Ошибка: {e}", file=sys.stderr)
        return 2
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nПрервано. Уже скачанные страницы в кэше — повторный запуск продолжит с места.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
