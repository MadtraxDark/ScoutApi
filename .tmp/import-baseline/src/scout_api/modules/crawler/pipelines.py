from typing import Any

from .models.product import ProductPriceItem


class PriceHistoryPipeline:
    """Persistence seam: replace with repository/DB implementation later."""

    def open_spider(self, spider: Any) -> None:
        self.items: list[ProductPriceItem] = []

    def process_item(self, item: ProductPriceItem, spider: Any) -> ProductPriceItem:
        self.items.append(item)
        return item

    def close_spider(self, spider: Any) -> None:
        del self.items
