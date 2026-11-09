"""Static HTML summary page (no JS, no external assets) built from the scrape result."""

from __future__ import annotations

from html import escape
from pathlib import Path

from .export import CategoryStat, summarize
from .models import Book

_CSS = """
:root{--bg:#f6f8fa;--card:#fff;--ink:#1f2328;--muted:#656d76;--bar:#1f4e78;--line:#d0d7de}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--card:#161b22;--ink:#e6edf3;--muted:#8d96a0;--bar:#4c8ed9;--line:#30363d}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,sans-serif}
main{max-width:1100px;margin:0 auto;padding:32px 16px}h1{margin:0 0 4px;font-size:28px}
.sub{color:var(--muted);margin:0 0 24px}.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:24px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.kpi b{display:block;font-size:26px;font-variant-numeric:tabular-nums}.kpi span{color:var(--muted);font-size:13px}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;margin-bottom:24px;overflow-x:auto}
h2{margin:0 0 12px;font-size:18px}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:6px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}th{color:var(--muted);font-weight:600;font-size:13px}
.bar{display:inline-block;height:10px;background:var(--bar);border-radius:2px;vertical-align:middle;margin-right:8px}
"""


def _row(s: CategoryStat, max_count: int) -> str:
    width = max(2, round(160 * s.count / max_count))
    return (
        f"<tr><td>{escape(s.category)}</td>"
        f"<td style='text-align:left'><span class='bar' style='width:{width}px'></span>{s.count}</td>"
        f"<td>£{s.avg_price:.2f}</td><td>£{s.min_price:.2f}</td><td>£{s.max_price:.2f}</td>"
        f"<td>{s.avg_rating:.2f}</td><td>{s.in_stock_units}</td></tr>"
    )


def build_report(books: list[Book], generated_at: str) -> str:
    stats = summarize(books)
    total = len(books)
    avg = sum(b.price for b in books) / total if total else 0
    units = sum(b.stock_count for b in books)
    max_count = max((s.count for s in stats), default=1)
    kpis = [
        (f"{total}", "книг собрано"),
        (f"{len(stats)}", "категорий"),
        (f"£{avg:.2f}", "средняя цена"),
        (f"{units}", "единиц на складе"),
    ]
    kpi_html = "".join(f"<div class='kpi'><b>{v}</b><span>{label}</span></div>" for v, label in kpis)
    rows = "".join(_row(s, max_count) for s in stats)
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Сводка каталога</title><style>{_CSS}</style></head><body><main>
<h1>Сводка по каталогу books.toscrape.com</h1>
<p class="sub">Сформировано {escape(generated_at)} · catalog-parser</p>
<div class="kpis">{kpi_html}</div>
<section><h2>Категории</h2><table><thead><tr><th>Категория</th><th style="text-align:left">Книг</th>
<th>Средняя цена</th><th>Мин.</th><th>Макс.</th><th>Рейтинг</th><th>Остаток, шт</th></tr></thead>
<tbody>{rows}</tbody></table></section></main></body></html>"""


def write_report(books: list[Book], path: Path, generated_at: str) -> None:
    path.write_text(build_report(books, generated_at), encoding="utf-8")
