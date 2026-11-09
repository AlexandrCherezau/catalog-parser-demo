"""Pure HTML parsing functions (no network) — easy to unit-test."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from .models import Book

RATING_WORDS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}
_PRICE_RE = re.compile(r"([^\d\s.,]*)\s*([\d.,]+)")
_STOCK_RE = re.compile(r"\((\d+)\s+available\)")


class ParseError(ValueError):
    """Raised when a page does not have the expected structure."""


def parse_price(text: str) -> tuple[float, str]:
    match = _PRICE_RE.search(text.strip())
    if not match:
        raise ParseError(f"Cannot parse price from {text!r}")
    currency = match.group(1) or ""
    return float(match.group(2).replace(",", "")), currency


def parse_stock(text: str) -> tuple[bool, int]:
    normalized = " ".join(text.split())
    in_stock = normalized.lower().startswith("in stock")
    match = _STOCK_RE.search(normalized)
    return in_stock, int(match.group(1)) if match else 0


def parse_rating(class_attr: str) -> int:
    for token in class_attr.split():
        if token in RATING_WORDS:
            return RATING_WORDS[token]
    return 0


def parse_listing(html: str, page_url: str) -> tuple[list[str], str | None]:
    """Return absolute product URLs on a listing page and the next-page URL."""
    tree = HTMLParser(html)
    links = [
        urljoin(page_url, a.attributes["href"])
        for a in tree.css("article.product_pod h3 a")
        if a.attributes.get("href")
    ]
    next_a = tree.css_first("li.next a")
    next_url = urljoin(page_url, next_a.attributes["href"]) if next_a else None
    return links, next_url


def _text(tree: HTMLParser, selector: str) -> str:
    node = tree.css_first(selector)
    if node is None:
        raise ParseError(f"Selector not found: {selector}")
    return node.text(strip=True)


def _info_table(tree: HTMLParser) -> dict[str, str]:
    table: dict[str, str] = {}
    for row in tree.css("table.table-striped tr"):
        th, td = row.css_first("th"), row.css_first("td")
        if th and td:
            table[th.text(strip=True)] = td.text(strip=True)
    return table


def parse_product(html: str, product_url: str) -> Book:
    tree = HTMLParser(html)
    title = _text(tree, "div.product_main h1")
    price, currency = parse_price(_text(tree, "div.product_main p.price_color"))

    stock_node = tree.css_first("div.product_main p.availability")
    in_stock, stock_count = parse_stock(stock_node.text() if stock_node else "")

    rating_node = tree.css_first("div.product_main p.star-rating")
    rating = parse_rating(rating_node.attributes.get("class", "") if rating_node else "")

    crumbs = tree.css("ul.breadcrumb li a")
    category = crumbs[2].text(strip=True) if len(crumbs) >= 3 else ""

    img = tree.css_first("#product_gallery img")
    image_url = urljoin(product_url, img.attributes.get("src", "")) if img else ""

    upc = _info_table(tree).get("UPC", "")
    if not upc:
        raise ParseError(f"UPC missing on {product_url}")

    return Book(
        title=title,
        price=price,
        currency=currency,
        in_stock=in_stock,
        stock_count=stock_count,
        rating=rating,
        category=category,
        upc=upc,
        product_url=product_url,
        image_url=image_url,
    )
