"""Amazon "Continue shopping" interstitial (no image CAPTCHA).

Amazon sometimes answers a PDP with a tiny page whose only content is a GET form
posting to ``/errors_page/validateCaptcha`` with hidden tokens (``amzn``,
``amzn-r``, ``field-keywords``) and a "Continue shopping" button. Submitting the
form as a browser would mints the session cookie and the PDP then loads. This is
the challenge-resolution path of ADR 0017 (no third-party solver involved).
"""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import urlencode, urljoin

_FORM_RE = re.compile(
    r"<form\b[^>]*\baction=[\"']([^\"']*validateCaptcha[^\"']*)[\"'][^>]*>(.*?)</form>",
    re.I | re.S,
)
_INPUT_RE = re.compile(r"<input\b([^>]*)>", re.I | re.S)
_ATTR_RE = re.compile(r"([\w-]+)\s*=\s*[\"']([^\"']*)[\"']")


def continue_interstitial_url(html: str, *, base_url: str) -> str | None:
    """GET URL that submits the "Continue shopping" form, or ``None``.

    Returns ``None`` for real image CAPTCHAs (a text input / captcha image is
    present), which keep going through the offline image solver.
    """
    text = html or ""
    if len(text) > 80_000:
        return None
    match = _FORM_RE.search(text)
    if match is None:
        return None
    action, body = match.group(1), match.group(2)
    if re.search(r"<img\b[^>]*captcha", text, re.I):
        return None
    params: list[tuple[str, str]] = []
    for raw in _INPUT_RE.findall(body):
        attrs = {k.lower(): unescape(v) for k, v in _ATTR_RE.findall(raw)}
        kind = attrs.get("type", "text").lower()
        name = attrs.get("name")
        if not name:
            continue
        if kind == "hidden":
            params.append((name, attrs.get("value", "")))
        elif (
            kind in {"text", ""}
            and "captchacharacters" in (attrs.get("id", "") + name).lower()
        ):
            return None  # asks the user to type characters: real CAPTCHA
    if not params:
        return None
    target = urljoin(base_url, unescape(action))
    sep = "&" if "?" in target else "?"
    return f"{target}{sep}{urlencode(params)}"
