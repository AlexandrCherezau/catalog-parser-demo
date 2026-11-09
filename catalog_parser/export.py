"""Exports: CSV, JSON and a formatted Excel workbook with a summary sheet."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .models import COLUMNS, Book

HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(bold=True, color="FFFFFF")
TOTAL_FONT = Font(bold=True)
THIN = Side(style="thin", color="D0D7DE")
BORDER = Border(bottom=THIN)
PRICE_FORMAT = '#,##0.00 "£"'
MAX_COL_WIDTH = 60
FILTER_BUTTON_PAD = 3  # header cells carry the autofilter dropdown


@dataclass(frozen=True)
class CategoryStat:
    category: str
    count: int
    avg_price: float
    min_price: float
    max_price: float
    avg_rating: float
    in_stock_units: int


def summarize(books: list[Book]) -> list[CategoryStat]:
    groups: dict[str, list[Book]] = defaultdict(list)
    for book in books:
        groups[book.category or "—"].append(book)
    stats = [
        CategoryStat(
            category=name,
            count=len(items),
            avg_price=round(sum(b.price for b in items) / len(items), 2),
            min_price=min(b.price for b in items),
            max_price=max(b.price for b in items),
            avg_rating=round(sum(b.rating for b in items) / len(items), 2),
            in_stock_units=sum(b.stock_count for b in items),
        )
        for name, items in groups.items()
    ]
    return sorted(stats, key=lambda s: (-s.count, s.category))


def write_csv(books: list[Book], path: Path) -> None:
    # utf-8-sig so Excel on Windows opens Cyrillic headers correctly
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow([header for _, header in COLUMNS])
        for book in books:
            row = book.to_dict()
            writer.writerow([_cell_value(key, row[key]) for key, _ in COLUMNS])


def write_json(books: list[Book], path: Path) -> None:
    path.write_text(
        json.dumps([b.to_dict() for b in books], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _cell_value(key: str, value):
    if key == "in_stock":
        return "да" if value else "нет"
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value  # scraped text must never become a live formula
    return value


def _style_header(ws, ncols: int) -> None:
    for col in range(1, ncols + 1):
        cell = ws.cell(row=1, column=col)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "A2"


def _display_len(cell) -> int:
    value = cell.value
    if value is None or (isinstance(value, str) and value.startswith("=")):
        return 0  # formulas: their text is not what the user sees
    if isinstance(value, float):
        return len(f"{value:,.2f}") + 2  # room for the currency suffix
    length = len(str(value))
    return length + FILTER_BUTTON_PAD if cell.row == 1 else length


def _autofit(ws, min_width: int = 8) -> None:
    for col_cells in ws.columns:
        letter = get_column_letter(col_cells[0].column)
        longest = max(_display_len(c) for c in col_cells)
        ws.column_dimensions[letter].width = max(min_width, min(longest + 2, MAX_COL_WIDTH))


def _books_sheet(ws, books: list[Book]) -> None:
    ws.title = "Книги"
    ws.append([header for _, header in COLUMNS])
    for book in books:
        row = book.to_dict()
        ws.append([_cell_value(key, row[key]) for key, _ in COLUMNS])

    keys = [key for key, _ in COLUMNS]
    price_col = keys.index("price") + 1
    url_cols = [keys.index("product_url") + 1, keys.index("image_url") + 1]
    for r in range(2, ws.max_row + 1):
        ws.cell(row=r, column=price_col).number_format = PRICE_FORMAT
        for c in url_cols:
            cell = ws.cell(row=r, column=c)
            cell.hyperlink = cell.value
            cell.font = Font(color="0563C1", underline="single")

    _style_header(ws, len(COLUMNS))
    ws.auto_filter.ref = ws.dimensions
    _autofit(ws)


def _summary_sheet(ws, stats: list[CategoryStat], books: list[Book]) -> None:
    headers = ["Категория", "Книг", "Средняя цена", "Мин. цена", "Макс. цена",
               "Средний рейтинг", "Остаток, шт"]
    ws.append(headers)
    for s in stats:
        ws.append([s.category, s.count, s.avg_price, s.min_price, s.max_price,
                   s.avg_rating, s.in_stock_units])
    last = ws.max_row
    ws.append(["ИТОГО", f"=SUM(B2:B{last})", f"=ROUND(AVERAGE(Книги!C2:C{len(books) + 1}),2)",
               f"=MIN(D2:D{last})", f"=MAX(E2:E{last})",
               f"=ROUND(AVERAGE(Книги!D2:D{len(books) + 1}),2)", f"=SUM(G2:G{last})"])
    total_row = ws.max_row
    for c in range(1, len(headers) + 1):
        ws.cell(row=total_row, column=c).font = TOTAL_FONT
        ws.cell(row=total_row, column=c).border = Border(top=Side(style="medium"))
    for r in range(2, total_row + 1):
        for c in (3, 4, 5):
            ws.cell(row=r, column=c).number_format = PRICE_FORMAT
        ws.cell(row=r, column=6).number_format = "0.00"

    _style_header(ws, len(headers))
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{last}"
    _autofit(ws, min_width=12)
    ws.column_dimensions["A"].width = 24

    chart = BarChart()
    chart.type = "bar"
    chart.title = "Топ-15 категорий по количеству книг"
    chart.y_axis.title = "Книг"
    top = min(15, len(stats))
    chart.add_data(Reference(ws, min_col=2, min_row=1, max_row=top + 1), titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=1, min_row=2, max_row=top + 1))
    chart.x_axis.scaling.orientation = "maxMin"
    chart.legend = None
    chart.height, chart.width = 12, 18
    ws.add_chart(chart, "I2")


def write_xlsx(books: list[Book], path: Path) -> None:
    wb = Workbook()
    _books_sheet(wb.active, books)
    _summary_sheet(wb.create_sheet("Сводка"), summarize(books), books)
    wb.save(path)
