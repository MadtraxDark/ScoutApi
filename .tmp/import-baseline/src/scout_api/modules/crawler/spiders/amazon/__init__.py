"""Shared Amazon marketplace parsing helpers (not a store spider)."""

from .marketplace import AMAZON_BR, AMAZON_US, AmazonMarketplace
from .parsing import (
    extract_amazon_details,
    extract_amazon_images,
    extract_amazon_offer,
)

__all__ = [
    "AMAZON_BR",
    "AMAZON_US",
    "AmazonMarketplace",
    "extract_amazon_details",
    "extract_amazon_images",
    "extract_amazon_offer",
]
