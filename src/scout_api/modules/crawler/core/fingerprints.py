from hashlib import sha256
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

TRACKING_KEYS = {
    "fbclid",
    "gclid",
    "gbraid",
    "wbraid",
    "gad_source",
    "gad_campaignid",
    "ref",
    "ref_",
    "source",
    "from",
    "sp_atk",
    "utm_campaign",
    "utm_medium",
    "utm_source",
    "utm_term",
    "utm_content",
    "xptdk",
    # AliExpress SERP / campaign tracking (sku selection uses sku_id separately).
    "spm",
    "algo_pvid",
    "algo_exp_id",
    "pdp_ext_f",
    "pdp_npi",
    "curpageloguid",
    "utparam-url",
    "tblci",
    "aff_fcid",
    "aff_fsk",
    "aff_platform",
    "aff_trace_key",
    "terminal_id",
    "af",
    "dp",
}

# Marketing / affiliate prefixes (Mercado Livre Google Shopping, etc.)
TRACKING_PREFIXES = ("matt_", "cq_", "gad_")


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    kept: list[tuple[str, str]] = []
    for key, value in parse_qsl(parts.query):
        lowered = key.lower()
        if lowered in TRACKING_KEYS:
            continue
        if any(lowered.startswith(prefix) for prefix in TRACKING_PREFIXES):
            continue
        kept.append((key, value))
    query = urlencode(kept)
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/") or "/",
            query,
            "",
        )
    )


def title_hint_from_url(url: str | None) -> str | None:
    """Best-effort product title from a PDP URL slug when SERP omits title text.

    Magalu-style paths ``/{slug}/p/{id}/`` expose a readable slug even when the
    search card has no title in static HTML. Opaque numeric item IDs are not
    titles. Used for cheap prefilter/ranking — not as a substitute for PDP
    extraction.
    """
    if not url:
        return None
    path = unquote(urlsplit(url).path or "").strip("/")
    if not path:
        return None
    parts = [part for part in path.split("/") if part]
    slug: str | None = None
    if "p" in parts:
        index = parts.index("p")
        if index > 0:
            slug = parts[index - 1]
    if slug is None and parts:
        leaf = parts[-1]
        numeric_id = leaf.removesuffix(".html").removesuffix(".htm")
        if numeric_id.isdigit() and len(numeric_id) >= 9:
            return None
        if leaf.lower() not in {"dp", "gp", "product"} and len(leaf) > 8:
            slug = leaf
    if not slug:
        return None
    text = slug.replace("-", " ").replace("_", " ").strip()
    while "  " in text:
        text = text.replace("  ", " ")
    return text or None


def request_fingerprint(
    store: str, url: str, product_id: str, variant: str | None = None
) -> str:
    value = "|".join((store, canonicalize_url(url), product_id, variant or ""))
    return sha256(value.encode()).hexdigest()
