"""Challenge / CAPTCHA / auth-wall classification and resolution (ADR 0017, 0018).

Amazon image captchas are solved offline with ``amazoncaptcha`` (no API key).
Auth walls: operator credentials (Amazon/Shopee) or credential-free session
bypass (Mercado Livre Snoopy + origin warm + resume). Never commit secrets.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO
from typing import Any, Protocol
from urllib.parse import parse_qs, quote, unquote, urlparse
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


class ChallengeKind(StrEnum):
    HARD_BLOCK = "hard_block"
    CLOUDFLARE_JS = "cloudflare_js"
    AKAMAI_SEC_CPT = "akamai_sec_cpt"
    AMAZON_IMAGE_CAPTCHA = "amazon_image_captcha"
    MERCADOLIVRE_SNOOPY = "mercadolivre_snoopy"
    AUTH_WALL = "auth_wall"
    GENERIC = "generic"


@dataclass(frozen=True, slots=True)
class ChallengeAssessment:
    kind: ChallengeKind
    resolvable: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class StoreAuthCredentials:
    """Operator-owned store credentials (from Settings / env — never Git)."""

    amazon_email: str | None = None
    amazon_password: str | None = None
    shopee_email: str | None = None
    shopee_password: str | None = None


_AMAZON_CAPTCHA_IMG_RE = re.compile(
    r'<img[^>]+src=["\'](https?://[^"\']*captcha[^"\']*)["\']',
    re.I,
)
_AMAZON_CAPTCHA_IMG_RE_ALT = re.compile(
    r'src=["\'](https?://(?:images-na\.ssl-images-amazon\.com|[^"\']+)'
    r'/captcha/[^"\']+)["\']',
    re.I,
)


def classify_challenge(
    html: str,
    *,
    title: str | None = None,
    url: str | None = None,
) -> ChallengeAssessment | None:
    """Return challenge/auth-wall kind when the page is an anti-bot interstitial."""
    # Local imports avoided to prevent cycles with ``html_fetcher``.
    from .html_fetcher import (  # noqa: PLC0415 — intentional late import
        is_akamai_sec_cpt_page,
        is_amazon_robot_check,
        is_auth_wall_page,
        is_challenge_page,
        is_hard_block_page,
        is_mercadolivre_snoopy_challenge,
        is_shopee_traffic_block,
    )

    if is_hard_block_page(html, title=title):
        return ChallengeAssessment(
            kind=ChallengeKind.HARD_BLOCK,
            resolvable=False,
            detail="cloudflare-or-waf-hard-block",
        )
    # Mercado Livre: Snoopy PoW often coexists with /gz/account-verification
    # wrappers — resolve PoW/session before treating the page as a login form.
    if is_mercadolivre_snoopy_challenge(html):
        return ChallengeAssessment(
            kind=ChallengeKind.MERCADOLIVRE_SNOOPY,
            resolvable=True,
            detail="mercadolivre-snoopy-pow",
        )
    if is_shopee_traffic_block(url or "", html) or is_auth_wall_page(
        html, url=url, title=title
    ):
        return ChallengeAssessment(
            kind=ChallengeKind.AUTH_WALL,
            resolvable=True,
            detail="login-or-session-gate",
        )
    if not is_challenge_page(html, title=title) and not is_amazon_robot_check(
        html, title=title
    ):
        return None

    title_text = (title or "").strip().casefold()
    lower = (html or "")[:40_000].casefold()
    if is_amazon_robot_check(html, title=title) or "validatecaptcha" in lower:
        return ChallengeAssessment(
            kind=ChallengeKind.AMAZON_IMAGE_CAPTCHA,
            resolvable=True,
            detail="amazon-validatecaptcha",
        )
    if is_akamai_sec_cpt_page(html):
        return ChallengeAssessment(
            kind=ChallengeKind.AKAMAI_SEC_CPT,
            resolvable=True,
            detail="akamai-sec-cpt-behavioral",
        )
    if (
        "just a moment" in title_text
        or "un momento" in title_text
        or "cf-challenge" in lower
        or "challenge-platform" in lower
        or "performing security verification" in lower
        or "turnstile" in lower
    ):
        return ChallengeAssessment(
            kind=ChallengeKind.CLOUDFLARE_JS,
            resolvable=True,
            detail="cloudflare-js-or-turnstile",
        )
    return ChallengeAssessment(
        kind=ChallengeKind.GENERIC,
        resolvable=True,
        detail="generic-challenge",
    )


def extract_amazon_captcha_image_url(html: str) -> str | None:
    for pattern in (_AMAZON_CAPTCHA_IMG_RE, _AMAZON_CAPTCHA_IMG_RE_ALT):
        match = pattern.search(html or "")
        if match:
            return match.group(1)
    return None


def extract_auth_resume_url(
    page_url: str,
    *,
    fallback: str | None = None,
) -> str | None:
    """Best-effort product URL to reopen after login (query ``next`` / return_to)."""
    parsed = urlparse(page_url or "")
    qs = parse_qs(parsed.query)
    for key in ("next", "openid.return_to", "return_to", "redirectURL", "rd", "go"):
        values = qs.get(key) or []
        if values and values[0].strip():
            candidate = unquote(values[0].strip())
            if candidate.startswith("http://") or candidate.startswith("https://"):
                return candidate
            if candidate.startswith("/"):
                origin = f"{parsed.scheme}://{parsed.netloc}"
                return f"{origin}{candidate}"
    return fallback


class ImageCaptchaSolver(Protocol):
    def solve_image(self, image_bytes: bytes) -> str:
        """Return the human-readable CAPTCHA text for an image payload."""


class MissingCaptchaSolverError(RuntimeError):
    """Raised when an image CAPTCHA must be solved but no solver is configured."""


class RequestErrorAdapter(RuntimeError):
    """Solver errors (mapped to UPSTREAM_BLOCKED upstream)."""


class AmazonCaptchaLocalSolver:
    """Offline Amazon text-captcha solver (``amazoncaptcha`` + Pillow)."""

    def solve_image(self, image_bytes: bytes) -> str:
        if not image_bytes:
            raise RequestErrorAdapter("amazoncaptcha: empty image payload")
        try:
            from amazoncaptcha import AmazonCaptcha
        except ImportError as exc:
            raise MissingCaptchaSolverError(
                "amazoncaptcha is not installed (pip install --no-deps amazoncaptcha)"
            ) from exc
        try:
            captcha = AmazonCaptcha(BytesIO(image_bytes))
            solution = str(captcha.solve() or "").strip()
        except Exception as exc:
            raise RequestErrorAdapter(f"amazoncaptcha failed: {exc}") from exc
        if not solution or solution.casefold() == "not solved":
            raise RequestErrorAdapter("amazoncaptcha could not solve image")
        return solution


def build_image_captcha_solver(
    *,
    enabled: bool,
    provider: str = "amazoncaptcha",
) -> ImageCaptchaSolver | None:
    if not enabled:
        return None
    provider_name = (provider or "amazoncaptcha").strip().lower()
    if provider_name in {"amazoncaptcha", "local", "offline"}:
        return AmazonCaptchaLocalSolver()
    logger.warning(
        "captcha_solver_provider_unsupported",
        extra={"provider": provider_name},
    )
    return None


@dataclass
class ChallengeResolver:
    """Clear anti-bot interstitials and auth walls on a live browser page."""

    image_solver: ImageCaptchaSolver | None = None
    max_attempts: int = 2
    soft_wait_ms: int = 5_000
    enabled: bool = True
    auth_bypass_enabled: bool = True
    credentials: StoreAuthCredentials | None = None

    def try_resolve(
        self,
        page: Any,
        *,
        html: str,
        title: str,
        page_url: str,
        resume_url: str | None = None,
    ) -> bool:
        """Return True when the page no longer looks like a challenge/auth wall."""
        if not self.enabled:
            return False
        assessment = classify_challenge(html, title=title, url=page_url)
        if assessment is None:
            return True
        if not assessment.resolvable:
            logger.info(
                "challenge_not_resolvable",
                extra={"kind": assessment.kind.value, "url": page_url},
            )
            return False
        if assessment.kind is ChallengeKind.AUTH_WALL and not self.auth_bypass_enabled:
            logger.info("auth_bypass_disabled", extra={"url": page_url})
            return False

        target_resume = extract_auth_resume_url(page_url, fallback=resume_url)
        for attempt in range(1, max(1, self.max_attempts) + 1):
            logger.info(
                "challenge_resolve_attempt",
                extra={
                    "kind": assessment.kind.value,
                    "attempt": attempt,
                    "url": page_url,
                },
            )
            try:
                if assessment.kind is ChallengeKind.AMAZON_IMAGE_CAPTCHA:
                    ok = self._resolve_amazon_image_captcha(page, html)
                elif assessment.kind is ChallengeKind.MERCADOLIVRE_SNOOPY:
                    ok = self._resolve_mercadolivre_snoopy(page)
                elif assessment.kind is ChallengeKind.CLOUDFLARE_JS:
                    ok = self._resolve_cloudflare_js(page)
                elif assessment.kind is ChallengeKind.AKAMAI_SEC_CPT:
                    ok = self._resolve_akamai_sec_cpt(page, resume_url=target_resume)
                elif assessment.kind is ChallengeKind.AUTH_WALL:
                    ok = self._resolve_auth_wall(
                        page,
                        page_url=page_url,
                        resume_url=target_resume,
                    )
                else:
                    ok = self._resolve_generic(page)
            except (MissingCaptchaSolverError, RequestErrorAdapter) as exc:
                logger.warning(
                    "challenge_resolve_failed",
                    extra={
                        "kind": assessment.kind.value,
                        "attempt": attempt,
                        "error": str(exc)[:200],
                    },
                )
                ok = False
            except Exception:
                logger.exception(
                    "challenge_resolve_unexpected_error",
                    extra={"kind": assessment.kind.value, "attempt": attempt},
                )
                ok = False

            html = _safe_content(page)
            title = _safe_title(page)
            page_url = _safe_url(page) or page_url
            if classify_challenge(html, title=title, url=page_url) is None:
                logger.info(
                    "challenge_resolved",
                    extra={"kind": assessment.kind.value, "attempt": attempt},
                )
                return True
            if not ok:
                continue
            assessment = (
                classify_challenge(html, title=title, url=page_url) or assessment
            )
        cleared = classify_challenge(
            _safe_content(page),
            title=_safe_title(page),
            url=_safe_url(page) or page_url,
        )
        return cleared is None

    def _resolve_auth_wall(
        self,
        page: Any,
        *,
        page_url: str,
        resume_url: str | None,
    ) -> bool:
        """Soft wait → optional credential login → reopen product URL."""
        try:
            page.wait_for_timeout(min(self.soft_wait_ms, 3_000))
        except Exception:
            pass

        host = (urlparse(page_url).hostname or "").casefold()
        logged_in = False
        if self._is_amazon_host(host):
            logged_in = self._login_amazon(page)
        elif self._is_shopee_host(host):
            # /verify/traffic has no password form — open buyer login first.
            if "/verify/traffic" in (page_url or "").casefold():
                target = (
                    resume_url
                    or extract_auth_resume_url(page_url, fallback=None)
                    or "https://shopee.com.br/"
                )
                login_url = "https://shopee.com.br/buyer/login?next=" + quote(
                    target, safe=""
                )
                try:
                    page.goto(
                        login_url,
                        wait_until="domcontentloaded",
                        timeout=max(15_000, self.soft_wait_ms * 2),
                    )
                    page.wait_for_timeout(min(self.soft_wait_ms, 2_500))
                except Exception:
                    logger.warning(
                        "shopee_traffic_login_navigation_failed",
                        exc_info=True,
                    )
            logged_in = self._login_shopee(page)
        elif self._is_mercadolivre_host(host):
            # Credential-free session bypass (Snoopy + origin warm + resume).
            return self._bypass_mercadolivre_auth_wall(
                page,
                page_url=page_url,
                resume_url=resume_url,
            )
        else:
            logged_in = self._login_generic(page)

        if resume_url:
            try:
                page.goto(
                    resume_url,
                    wait_until="domcontentloaded",
                    timeout=max(15_000, self.soft_wait_ms * 2),
                )
            except Exception:
                logger.warning("auth_wall_resume_navigation_failed", exc_info=True)
                return logged_in
        try:
            page.wait_for_timeout(min(self.soft_wait_ms, 5_000))
        except Exception:
            pass
        return logged_in

    def _login_amazon(self, page: Any) -> bool:
        creds = self.credentials
        email = (creds.amazon_email if creds else None) or ""
        password = (creds.amazon_password if creds else None) or ""
        if not email.strip() or not password.strip():
            logger.info("amazon_auth_credentials_missing")
            return False
        email_ok = _fill_first(
            page,
            (
                "#ap_email",
                "input[name='email']",
                "input[type='email']",
            ),
            email.strip(),
        )
        if email_ok:
            _click_first(
                page,
                (
                    "#continue",
                    "input#continue",
                    "button[type='submit']",
                ),
            )
            try:
                page.wait_for_timeout(1_500)
            except Exception:
                pass
        password_ok = _fill_first(
            page,
            (
                "#ap_password",
                "input[name='password']",
                "input[type='password']",
            ),
            password.strip(),
        )
        if not password_ok:
            logger.warning("amazon_auth_password_field_missing")
            return False
        _click_first(
            page,
            (
                "#signInSubmit",
                "input#signInSubmit",
                "button[type='submit']",
            ),
        )
        try:
            page.wait_for_timeout(min(self.soft_wait_ms, 5_000))
        except Exception:
            pass
        return True

    def _login_shopee(self, page: Any) -> bool:
        creds = self.credentials
        email = (creds.shopee_email if creds else None) or ""
        password = (creds.shopee_password if creds else None) or ""
        if not email.strip() or not password.strip():
            logger.info("shopee_auth_credentials_missing")
            return False
        _click_first(
            page,
            (
                "button:has-text('Log in with password')",
                "button:has-text('Entre com a senha')",
                "div:has-text('Log in with Password')",
            ),
        )
        email_ok = _fill_first(
            page,
            (
                "input[name='loginKey']",
                "input[type='text']",
                "input[type='email']",
                "input[autocomplete='username']",
            ),
            email.strip(),
        )
        password_ok = _fill_first(
            page,
            (
                "input[name='password']",
                "input[type='password']",
                "input[autocomplete='current-password']",
            ),
            password.strip(),
        )
        if not email_ok or not password_ok:
            logger.warning("shopee_auth_fields_missing")
            return False
        _click_first(
            page,
            (
                "button[type='submit']",
                "button:has-text('Log In')",
                "button:has-text('Entrar')",
            ),
        )
        try:
            page.wait_for_timeout(min(self.soft_wait_ms, 5_000))
        except Exception:
            pass
        # Credentials filled ≠ session established. Only report success when the
        # interstitial is gone (caller otherwise retries login for minutes).
        from .html_fetcher import (  # noqa: PLC0415 — avoid import cycle
            is_auth_wall_page,
            is_shopee_traffic_block,
        )

        cur_url = _safe_url(page) or ""
        cur_html = _safe_content(page)
        if is_shopee_traffic_block(cur_url, cur_html):
            return False
        if is_auth_wall_page(
            cur_html,
            url=cur_url,
            title=_safe_title(page),
        ):
            return False
        return True

    def _bypass_mercadolivre_auth_wall(
        self,
        page: Any,
        *,
        page_url: str,
        resume_url: str | None,
    ) -> bool:
        """Clear ML soft-auth without ``MERCADOLIVRE_AUTH_*`` credentials.

        Strategy (ADR 0018): resolve Snoopy/PoW if present, soft-dismiss
        continue buttons, warm the public origin, then reopen the ``go`` /
        resume URL. Persistent Camoufox profile cookies are reused; password
        login is intentionally not used.
        """
        from .html_fetcher import (  # noqa: PLC0415
            is_auth_wall_page,
            is_mercadolivre_snoopy_challenge,
        )

        html = _safe_content(page)
        if is_mercadolivre_snoopy_challenge(html) or (
            "continue-button" in html.casefold()
        ):
            self._resolve_mercadolivre_snoopy(page)

        _click_first(
            page,
            (
                "#continue-button:not([disabled])",
                "button#continue-button",
                "button.micro-landing-button",
                "button:has-text('Continuar navegando')",
                "a:has-text('Continuar navegando')",
                "button:has-text('Entendi')",
                "button:has-text('Continuar')",
            ),
        )
        try:
            page.wait_for_timeout(min(2_000, self.soft_wait_ms))
        except Exception:
            pass

        target = (
            resume_url
            or extract_auth_resume_url(page_url, fallback=None)
            or "https://lista.mercadolivre.com.br/"
        )
        goto_timeout = max(15_000, self.soft_wait_ms * 2)

        # Origin warm mints public session cookies without an account login.
        try:
            page.goto(
                "https://www.mercadolivre.com.br/",
                wait_until="domcontentloaded",
                timeout=goto_timeout,
            )
            page.wait_for_timeout(min(2_500, self.soft_wait_ms))
        except Exception:
            logger.warning("mercadolivre_origin_warm_failed", exc_info=True)

        home_html = _safe_content(page)
        if is_mercadolivre_snoopy_challenge(home_html):
            self._resolve_mercadolivre_snoopy(page)

        try:
            page.goto(target, wait_until="domcontentloaded", timeout=goto_timeout)
            page.wait_for_timeout(min(3_000, self.soft_wait_ms))
        except Exception:
            logger.warning(
                "mercadolivre_auth_bypass_resume_failed",
                exc_info=True,
                extra={"url": target[:160]},
            )
            return False

        target_html = _safe_content(page)
        if is_mercadolivre_snoopy_challenge(target_html):
            self._resolve_mercadolivre_snoopy(page)
            target_html = _safe_content(page)

        cur_url = _safe_url(page) or ""
        if is_auth_wall_page(
            target_html, url=cur_url, title=_safe_title(page)
        ):
            logger.info(
                "mercadolivre_auth_bypass_wall_persists",
                extra={"url": cur_url[:160]},
            )
            return False
        logger.info(
            "mercadolivre_auth_bypass_ok",
            extra={"url": cur_url[:160]},
        )
        return True

    def _login_generic(self, page: Any) -> bool:
        del page
        logger.info("auth_wall_generic_soft_wait_only")
        return False

    @staticmethod
    def _is_amazon_host(host: str) -> bool:
        return (
            host == "amazon.com"
            or host.endswith(".amazon.com")
            or host == "amazon.com.br"
            or host.endswith(".amazon.com.br")
        )

    @staticmethod
    def _is_shopee_host(host: str) -> bool:
        return host == "shopee.com.br" or host.endswith(".shopee.com.br")

    @staticmethod
    def _is_mercadolivre_host(host: str) -> bool:
        return (
            host == "mercadolivre.com.br"
            or host.endswith(".mercadolivre.com.br")
            or host == "mercadolibre.com"
            or host.endswith(".mercadolibre.com")
        )

    def _resolve_amazon_image_captcha(self, page: Any, html: str) -> bool:
        if self.image_solver is None:
            raise MissingCaptchaSolverError(
                "Image CAPTCHA requires captcha solver (provider=amazoncaptcha)"
            )
        image_url = extract_amazon_captcha_image_url(html)
        if not image_url:
            try:
                locator = page.locator(
                    "img[src*='captcha'], form[action*='validateCaptcha'] img"
                ).first
                image_url = locator.get_attribute("src")
            except Exception:
                image_url = None
        if not image_url:
            logger.warning("amazon_captcha_image_not_found")
            return False

        image_bytes = self._download_image(page, image_url)
        solution = self.image_solver.solve_image(image_bytes)
        if not solution:
            return False
        return self._submit_amazon_captcha(page, solution)

    def _download_image(self, page: Any, image_url: str) -> bytes:
        try:
            raw = page.evaluate(
                """async (url) => {
                    const res = await fetch(url, {credentials: 'include'});
                    const buf = await res.arrayBuffer();
                    return Array.from(new Uint8Array(buf));
                }""",
                image_url,
            )
            if isinstance(raw, list) and raw:
                return bytes(int(x) & 0xFF for x in raw)
        except Exception:
            logger.debug("amazon_captcha_image_fetch_inpage_failed", exc_info=True)
        req = Request(image_url, method="GET")
        with urlopen(req, timeout=30) as response:
            payload = response.read()
            return bytes(payload)

    def _submit_amazon_captcha(self, page: Any, solution: str) -> bool:
        selectors = (
            "#captchacharacters",
            "input[name='field-keywords']",
            "form[action*='validateCaptcha'] input[type='text']",
        )
        filled = False
        for selector in selectors:
            try:
                handle = page.query_selector(selector)
                if handle is None:
                    continue
                handle.fill(solution)
                filled = True
                break
            except Exception:
                try:
                    loc = page.locator(selector).first
                    loc.fill(solution)
                    filled = True
                    break
                except Exception:
                    continue
        if not filled:
            logger.warning("amazon_captcha_input_not_found")
            return False
        try:
            submit = page.query_selector(
                "form[action*='validateCaptcha'] button[type='submit'], "
                "form[action*='validateCaptcha'] input[type='submit']"
            )
            if submit is not None:
                submit.click()
            else:
                page.keyboard.press("Enter")
        except Exception:
            try:
                page.keyboard.press("Enter")
            except Exception:
                logger.warning("amazon_captcha_submit_failed", exc_info=True)
                return False
        try:
            page.wait_for_timeout(min(self.soft_wait_ms, 8_000))
        except Exception:
            pass
        return True

    def _resolve_cloudflare_js(self, page: Any) -> bool:
        try:
            page.wait_for_timeout(self.soft_wait_ms)
        except Exception:
            pass
        try:
            for frame in page.frames:
                try:
                    box = frame.locator(
                        "input[type='checkbox'], .cf-turnstile, #challenge-stage input"
                    ).first
                    if box.count() > 0:
                        box.click(timeout=2_000)
                        page.wait_for_timeout(min(self.soft_wait_ms, 5_000))
                        break
                except Exception:
                    continue
        except Exception:
            logger.debug("cloudflare_turnstile_click_skipped", exc_info=True)
        return True

    def _resolve_akamai_sec_cpt(
        self, page: Any, *, resume_url: str | None = None
    ) -> bool:
        """Behavioral / sec-cpt: sensor JS + pointer telemetry + optional hold.

        Inspired by open-source Akamai waiters (e.g. germondai/trawl akamaiWait):
        wander the mouse, press-and-hold the progress button when present, wait
        for the interstitial to clear, and re-navigate once if reload stalls.
        """
        from .html_fetcher import is_akamai_sec_cpt_page  # noqa: PLC0415

        deadline_ms = max(12_000, self.soft_wait_ms * 3)
        steps = max(4, deadline_ms // 1_500)
        navigated_once = False
        for step in range(steps):
            html = _safe_content(page)
            if html and not is_akamai_sec_cpt_page(html):
                return True
            self._akamai_wander_mouse(page)
            self._akamai_press_and_hold(page)
            try:
                page.wait_for_timeout(1_500)
            except Exception:
                pass
            # If sensor scored but reload stalled, reopen the product URL once.
            if (
                not navigated_once
                and resume_url
                and step >= max(2, steps // 3)
                and is_akamai_sec_cpt_page(_safe_content(page))
            ):
                navigated_once = True
                try:
                    page.goto(
                        resume_url,
                        wait_until="domcontentloaded",
                        timeout=max(15_000, self.soft_wait_ms * 2),
                    )
                except Exception:
                    logger.debug("akamai_sec_cpt_renavigate_failed", exc_info=True)
        return not is_akamai_sec_cpt_page(_safe_content(page))

    @staticmethod
    def _akamai_wander_mouse(page: Any) -> None:
        try:
            viewport = page.viewport_size or {"width": 1280, "height": 720}
            width = int(viewport.get("width") or 1280)
            height = int(viewport.get("height") or 720)
            mouse = page.mouse
            for i in range(6):
                x = 60 + (width // 7) * ((i + 1) % 6)
                y = 80 + (height // 8) * ((i + 2) % 5)
                mouse.move(min(width - 20, max(20, x)), min(height - 20, max(20, y)))
                page.wait_for_timeout(80)
        except Exception:
            logger.debug("akamai_mouse_wander_skipped", exc_info=True)

    @staticmethod
    def _akamai_press_and_hold(page: Any) -> bool:
        selectors = (
            "#progress-button",
            ".behavioral-button",
            "#sec-if-cpt-container [role='button']",
            "#sec-bc-tile-parent button",
            "#sec-if-cpt-container button",
        )
        for selector in selectors:
            try:
                loc = page.locator(selector).first
                if loc.count() == 0:
                    continue
                box = loc.bounding_box()
                if not box or box.get("width", 0) < 4 or box.get("height", 0) < 4:
                    continue
                cx = float(box["x"]) + float(box["width"]) / 2
                cy = float(box["y"]) + float(box["height"]) / 2
                page.mouse.move(cx - 12, cy - 8)
                page.mouse.move(cx, cy)
                page.mouse.down()
                page.wait_for_timeout(2_500)
                page.mouse.up()
                return True
            except Exception:
                continue
        return False

    def _resolve_mercadolivre_snoopy(self, page: Any) -> bool:
        """Wait for Snoopy PoW, click Continuar if needed, settle on PDP."""
        wait_ms = max(15_000, min(60_000, self.soft_wait_ms * 6))
        try:
            page.wait_for_function(
                """() => {
                    const btn = document.getElementById('continue-button');
                    if (!btn) return true;
                    return !btn.disabled;
                }""",
                timeout=wait_ms,
            )
        except Exception:
            logger.debug("mercadolivre_snoopy_wait_button_timeout", exc_info=True)

        clicked = _click_first(
            page,
            (
                "#continue-button:not([disabled])",
                "button#continue-button",
                "button.micro-landing-button",
            ),
        )
        if clicked:
            try:
                page.wait_for_timeout(min(5_000, max(1_500, self.soft_wait_ms)))
            except Exception:
                pass

        try:
            page.wait_for_function(
                """() => {
                    if (document.getElementById('continue-button')
                        && document.querySelector('script[src*=\"snoopy\"]')) {
                        return false;
                    }
                    return !!(
                        document.querySelector('script[type=\"application/ld+json\"]')
                        || document.querySelector('.ui-pdp-price')
                        || document.querySelector('h1.ui-pdp-title')
                        || document.querySelector('.ui-search-layout')
                        || document.querySelector('a.poly-component__title')
                        || document.querySelector('.ui-search-results')
                    );
                }""",
                timeout=wait_ms,
            )
            return True
        except Exception:
            logger.debug("mercadolivre_snoopy_wait_pdp_timeout", exc_info=True)
            try:
                page.wait_for_timeout(min(self.soft_wait_ms, 5_000))
            except Exception:
                pass
            return True

    def _resolve_generic(self, page: Any) -> bool:
        try:
            page.wait_for_timeout(self.soft_wait_ms)
        except Exception:
            pass
        return True


def _fill_first(page: Any, selectors: tuple[str, ...], value: str) -> bool:
    for selector in selectors:
        try:
            handle = page.query_selector(selector)
            if handle is None:
                continue
            handle.fill(value)
            return True
        except Exception:
            try:
                loc = page.locator(selector).first
                loc.fill(value)
                return True
            except Exception:
                continue
    return False


def _click_first(page: Any, selectors: tuple[str, ...]) -> bool:
    for selector in selectors:
        try:
            handle = page.query_selector(selector)
            if handle is None:
                continue
            handle.click()
            return True
        except Exception:
            try:
                loc = page.locator(selector).first
                loc.click(timeout=2_000)
                return True
            except Exception:
                continue
    return False


def _safe_content(page: Any) -> str:
    try:
        return str(page.content() or "")
    except Exception:
        return ""


def _safe_title(page: Any) -> str:
    try:
        return str(page.title() or "")
    except Exception:
        return ""


def _safe_url(page: Any) -> str:
    try:
        return str(getattr(page, "url", "") or "")
    except Exception:
        return ""
