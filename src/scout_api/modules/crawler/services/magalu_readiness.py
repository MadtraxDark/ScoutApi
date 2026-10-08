"""Recognize hydrated Magalu documents before waiting for unrelated network traffic."""

import json
from urllib.parse import urlparse

from parsel import Selector


def magalu_search_link_count(html: str, url: str) -> int:
    """How many distinct product links a SERP already exposes.

    Analytics keep the network busy after those links exist. One early card is
    not enough: the rest of the grid often hydrates in the next few seconds.
    """
    parsed = urlparse(url)
    if (parsed.hostname or "").removeprefix("www.") != "magazineluiza.com.br":
        return 0
    if "/busca/" not in (parsed.path or "").casefold():
        return 0
    selector = Selector(text=html or "")
    seen: set[str] = set()
    for href in selector.css(
        "a[data-testid='product-card-link']::attr(href), a[href*='/p/']::attr(href)"
    ).getall():
        path = (href or "").split("?", 1)[0].casefold()
        if "/p/" in path and "/busca/" not in path:
            seen.add(path)
    return len(seen)


def magalu_search_ready(html: str, url: str) -> bool:
    """True when a SERP already exposes at least one product link."""
    return magalu_search_link_count(html, url) > 0


def magalu_document_ready(html: str, url: str) -> bool:
    parsed = urlparse(url)
    if (parsed.hostname or "").removeprefix("www.") != "magazineluiza.com.br":
        return False
    raw = Selector(text=html).css("script#__NEXT_DATA__::text").get()
    if not raw:
        return False
    try:
        state = json.loads(raw)
    except (ValueError, TypeError):
        return False
    if not isinstance(state, dict):
        return False
    props = state.get("props")
    page_props = props.get("pageProps") if isinstance(props, dict) else None
    if not isinstance(page_props, dict):
        return False
    if "/p/" not in parsed.path:
        return False
    data = page_props.get("data")
    item = data.get("item") if isinstance(data, dict) else None
    if not isinstance(item, dict) or not item.get("id") or not item.get("title"):
        return False
    offers = item.get("offers")
    return isinstance(offers, list) and any(isinstance(offer, dict) for offer in offers)
