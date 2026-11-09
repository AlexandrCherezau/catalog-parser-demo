import asyncio
import csv
from pathlib import Path

import httpx
import pytest
from openpyxl import load_workbook

from catalog_parser.cli import main, validate, build_parser
from catalog_parser.crawler import collect_product_urls, scrape_products
from catalog_parser.export import summarize, write_csv, write_xlsx
from catalog_parser.fetcher import Fetcher, HtmlCache
from catalog_parser.models import Book
from catalog_parser.parsing import (
    ParseError, parse_listing, parse_price, parse_product, parse_rating, parse_stock,
)
from catalog_parser.report import build_report

FIX = Path(__file__).parent / "fixtures"
PRODUCT_URL = "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"


def read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def make_book(**kw) -> Book:
    base = dict(title="T", price=10.0, currency="£", in_stock=True, stock_count=3, rating=4,
                category="Poetry", upc="u1", product_url="https://x/1", image_url="https://x/1.jpg")
    return Book(**{**base, **kw})


# --- parsing -----------------------------------------------------------------

def test_parse_price_handles_currency_and_thousands():
    assert parse_price("£51.77") == (51.77, "£")
    assert parse_price("$1,234.50") == (1234.5, "$")


def test_parse_price_raises_on_garbage():
    with pytest.raises(ParseError):
        parse_price("free")


def test_parse_stock_extracts_count():
    assert parse_stock("\n  In stock (22 available)\n") == (True, 22)
    assert parse_stock("Out of stock") == (False, 0)


def test_parse_rating_words():
    assert parse_rating("star-rating Three") == 3
    assert parse_rating("star-rating") == 0


def test_parse_listing_page1_has_20_links_and_next():
    links, nxt = parse_listing(read("listing_page1.html"),
                               "https://books.toscrape.com/catalogue/page-1.html")
    assert len(links) == 20
    assert links[0] == PRODUCT_URL
    assert nxt == "https://books.toscrape.com/catalogue/page-2.html"


def test_parse_listing_last_page_has_no_next():
    _, nxt = parse_listing(read("listing_page50.html"),
                           "https://books.toscrape.com/catalogue/page-50.html")
    assert nxt is None


def test_parse_product_all_fields():
    book = parse_product(read("product.html"), PRODUCT_URL)
    assert book.title == "A Light in the Attic"
    assert book.price == 51.77 and book.currency == "£"
    assert book.in_stock and book.stock_count == 22
    assert book.rating == 3
    assert book.category == "Poetry"
    assert book.upc == "a897fe39b1053632"
    assert book.image_url.startswith("https://books.toscrape.com/media/cache/")


def test_parse_product_rejects_wrong_page():
    with pytest.raises(ParseError):
        parse_product("<html><body>nope</body></html>", PRODUCT_URL)


# --- fetcher -----------------------------------------------------------------

def run_with_transport(handler, coro_factory, tmp_path=None, retries=3):
    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            fetcher = Fetcher(client, HtmlCache(tmp_path), concurrency=4,
                              max_retries=retries, backoff_base=0.001)
            return fetcher, await coro_factory(fetcher)
    return asyncio.run(go())


def test_fetcher_retries_on_503_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200, text="ok")

    fetcher, html = run_with_transport(handler, lambda f: f.get("https://x/"))
    assert html == "ok"
    assert fetcher.stats.retries == 2 and fetcher.stats.failures == 0


def test_fetcher_gives_up_after_max_retries():
    fetcher, html = run_with_transport(lambda r: httpx.Response(500),
                                       lambda f: f.get("https://x/"), retries=2)
    assert html is None
    assert fetcher.stats.failures == 1 and fetcher.stats.retries == 2


def test_fetcher_does_not_retry_404():
    fetcher, html = run_with_transport(lambda r: httpx.Response(404),
                                       lambda f: f.get("https://x/"))
    assert html is None and fetcher.stats.retries == 0


def test_fetcher_retries_transport_errors():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, text="ok")

    _, html = run_with_transport(handler, lambda f: f.get("https://x/"))
    assert html == "ok"


def test_cache_serves_second_request_without_network(tmp_path):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, text="cached-body")

    async def twice(f):
        await f.get("https://x/a")
        return await f.get("https://x/a")

    fetcher, html = run_with_transport(handler, twice, tmp_path=tmp_path)
    assert html == "cached-body" and calls["n"] == 1 and fetcher.stats.cache_hits == 1


def test_crawler_end_to_end_with_mock_site():
    listing = read("listing_page1.html")
    product = read("product.html")

    def handler(request):
        return httpx.Response(200, text=listing if "page-" in str(request.url) else product)

    async def crawl(f):
        urls = await collect_product_urls(f, pages=2)
        return urls, await scrape_products(f, urls)

    _, (urls, (books, failed)) = run_with_transport(handler, crawl)
    assert len(urls) == 20  # page 2 returns same links -> deduplicated
    assert len(books) == 20 and failed == []


# --- exports -----------------------------------------------------------------

def test_summarize_groups_and_sorts():
    books = [make_book(category="A", price=10), make_book(category="A", price=20),
             make_book(category="B", price=5)]
    stats = summarize(books)
    assert [s.category for s in stats] == ["A", "B"]
    assert stats[0].count == 2 and stats[0].avg_price == 15.0


def test_write_xlsx_formatting(tmp_path):
    books = [make_book(upc=f"u{i}", price=10.5 + i, category="A" if i % 2 else "B")
             for i in range(5)]
    path = tmp_path / "b.xlsx"
    write_xlsx(books, path)
    wb = load_workbook(path)
    ws = wb["Книги"]
    assert ws.max_row == 6
    assert ws.freeze_panes == "A2"
    assert ws.auto_filter.ref == "A1:I6"
    assert ws["A1"].font.bold
    assert isinstance(ws["C2"].value, float) and "£" in ws["C2"].number_format
    summary = wb["Сводка"]
    assert summary["A1"].value == "Категория"
    assert {summary["A2"].value, summary["A3"].value} == {"A", "B"}
    assert summary["A4"].value == "ИТОГО"
    assert len(summary._charts) == 1
    # formula text must not inflate column widths
    assert summary.column_dimensions["C"].width < 25


def test_write_csv_roundtrip(tmp_path):
    path = tmp_path / "b.csv"
    write_csv([make_book(title='Quote "x"; semi')], path)
    rows = list(csv.reader(path.open(encoding="utf-8-sig"), delimiter=";"))
    assert rows[0][0] == "Название" and rows[1][0] == 'Quote "x"; semi'


def test_exports_neutralize_formula_injection(tmp_path):
    evil = make_book(title="=HYPERLINK(\"http://evil\")", category="+cmd")
    write_xlsx([evil], tmp_path / "e.xlsx")
    ws = load_workbook(tmp_path / "e.xlsx")["Книги"]
    assert ws["A2"].data_type == "s" and ws["A2"].value.startswith("'=")
    write_csv([evil], tmp_path / "e.csv")
    rows = list(csv.reader((tmp_path / "e.csv").open(encoding="utf-8-sig"), delimiter=";"))
    assert rows[1][0].startswith("'=") and rows[1][1] == "'+cmd"


def test_fetcher_non_transport_request_error_is_not_fatal():
    def handler(request):
        raise httpx.TooManyRedirects("loop", request=request)

    fetcher, html = run_with_transport(handler, lambda f: f.get("https://x/"))
    assert html is None and fetcher.stats.failures == 1 and fetcher.stats.retries == 0


def test_report_escapes_html():
    html = build_report([make_book(category="<script>")], "now")
    assert "<script>" not in html and "&lt;script&gt;" in html


# --- cli ---------------------------------------------------------------------

@pytest.mark.parametrize("argv", [["--pages", "0"], ["--pages", "51"],
                                  ["--concurrency", "20"], ["--formats", "xlsx,pdf"]])
def test_cli_rejects_bad_args(argv):
    assert validate(build_parser().parse_args(argv))
    assert main(argv) == 2
