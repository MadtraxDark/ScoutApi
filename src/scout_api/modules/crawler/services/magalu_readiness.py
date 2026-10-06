"""Recognize hydrated Magalu documents before waiting for unrelated network traffic."""

import json
from urllib.parse import urlparse

from parsel import Selector


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
