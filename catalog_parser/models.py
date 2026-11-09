"""Data model for a scraped book."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields


@dataclass(frozen=True, slots=True)
class Book:
    title: str
    price: float
    currency: str
    in_stock: bool
    stock_count: int
    rating: int
    category: str
    upc: str
    product_url: str
    image_url: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Book":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})


# Column order + human-readable (Russian) headers for exports.
COLUMNS: list[tuple[str, str]] = [
    ("title", "Название"),
    ("category", "Категория"),
    ("price", "Цена, £"),
    ("rating", "Рейтинг"),
    ("in_stock", "В наличии"),
    ("stock_count", "Остаток, шт"),
    ("upc", "UPC"),
    ("product_url", "Ссылка на товар"),
    ("image_url", "Ссылка на фото"),
]
