"""Amazon United States (amazon.com) store adapter."""

from scrapy.http import Response

from ...models.product import ProductDetails, ProductOffer
from ..amazon.marketplace import AMAZON_US
from ..amazon.parsing import (
    extract_amazon_details,
    extract_amazon_images,
    extract_amazon_offer,
    prepare_amazon_fetch_url,
)
from ..base import BaseStoreSpider


class AmazonUSSpider(BaseStoreSpider):
    """Amazon.com — independent US marketplace offers (USD).

    Availability is US-market stock, not shipping eligibility to Brazil
    (see ADR 0013 / ADR 0015).
    """

    name = "amazon_us"
    store, country, currency = "amazon", "US", "USD"
    allowed_domains = ["amazon.com", "www.amazon.com"]
    start_urls: list[str] = []

    def prepare_fetch_url(self, url: str) -> str:
        return prepare_amazon_fetch_url(url, AMAZON_US.host)

    def extract_offer(self, response: Response) -> ProductOffer:
        return extract_amazon_offer(self, response, AMAZON_US)

    def extract_details(self, response: Response) -> ProductDetails:
        return extract_amazon_details(self, response, AMAZON_US)

    def extract_images(self, response: Response) -> list[str]:
        return extract_amazon_images(response)
