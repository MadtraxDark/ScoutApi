"""SSRF-safe external image downloader."""

from __future__ import annotations

import hashlib
import io
import ipaddress
import logging
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from PIL import Image, UnidentifiedImageError

from scout_api.core.config import Settings, get_settings
from scout_api.core.performance import OperationCategory, timed
from scout_api.modules.crawler.core.exceptions import RequestError

logger = logging.getLogger(__name__)

_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "metadata.google.internal",
        "metadata.google",
        "metadata",
    }
)


@dataclass(frozen=True, slots=True)
class DownloadedImage:
    data: bytes
    content_type: str
    width: int
    height: int
    sha256: str
    extension: str
    source_url: str


def _is_blocked_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
        or (addr.version == 4 and addr in ipaddress.ip_network("169.254.0.0/16"))
        or (addr.version == 6 and addr in ipaddress.ip_network("fc00::/7"))
    )


def assert_url_safe_for_download(url: str) -> None:
    """Validate scheme/host and resolve DNS to reject private targets."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise RequestError(
            "URL de imagem deve ser http ou https", code="INVALID_REQUEST"
        )
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise RequestError("URL de imagem sem host", code="INVALID_REQUEST")
    if host in _BLOCKED_HOSTNAMES or host.endswith(".localhost"):
        raise RequestError("Host de imagem bloqueado (SSRF)", code="INVALID_REQUEST")
    if host.endswith(".internal") or host.endswith(".local"):
        raise RequestError("Host de imagem bloqueado (SSRF)", code="INVALID_REQUEST")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and _is_blocked_ip(literal):
        raise RequestError("IP de imagem bloqueado (SSRF)", code="INVALID_REQUEST")

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise RequestError(
            "Não foi possível resolver host da imagem",
            code="INVALID_REQUEST",
        ) from exc
    for info in infos:
        sockaddr = info[4]
        ip_str = sockaddr[0]
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if _is_blocked_ip(addr):
            raise RequestError(
                "Destino de imagem resolve para rede privada (SSRF)",
                code="INVALID_REQUEST",
            )


def _guess_extension(mime: str, fmt: str | None) -> str:
    mapping = {
        "JPEG": "jpg",
        "PNG": "png",
        "WEBP": "webp",
        "GIF": "gif",
        "AVIF": "avif",
        "BMP": "bmp",
        "TIFF": "tiff",
    }
    if fmt and fmt.upper() in mapping:
        return mapping[fmt.upper()]
    if "jpeg" in mime or "jpg" in mime:
        return "jpg"
    if "png" in mime:
        return "png"
    if "webp" in mime:
        return "webp"
    if "gif" in mime:
        return "gif"
    if "avif" in mime:
        return "avif"
    return "bin"


def validate_image_bytes(
    data: bytes,
    *,
    claimed_content_type: str | None,
    max_dimension: int,
) -> tuple[str, int, int, str]:
    """Return (mime, width, height, extension). Raises RequestError if invalid."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            width, height = img.size
            fmt = img.format
            if width <= 0 or height <= 0:
                raise RequestError(
                    "Imagem com dimensões inválidas", code="INVALID_REQUEST"
                )
            if width > max_dimension or height > max_dimension:
                raise RequestError(
                    f"Imagem excede dimensão máxima ({max_dimension}px)",
                    code="INVALID_REQUEST",
                )
            mime = Image.MIME.get(fmt or "", "") or (
                claimed_content_type or "application/octet-stream"
            ).split(";")[0].strip()
            if not mime.startswith("image/"):
                mime = f"image/{(fmt or 'jpeg').lower()}"
                if mime == "image/jpg":
                    mime = "image/jpeg"
            ext = _guess_extension(mime, fmt)
            return mime, width, height, ext
    except UnidentifiedImageError as exc:
        raise RequestError(
            "Conteúdo baixado não é uma imagem suportada",
            code="INVALID_REQUEST",
        ) from exc
    except OSError as exc:
        raise RequestError(
            "Falha ao validar bytes da imagem",
            code="INVALID_REQUEST",
        ) from exc


class ImageDownloader:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def download(self, url: str) -> DownloadedImage:
        assert_url_safe_for_download(url)
        timeout = self._settings.image_download_timeout_seconds
        max_bytes = self._settings.image_max_bytes
        max_redirects = self._settings.image_max_redirects

        with timed(
            "image_download",
            category=OperationCategory.HTTP_REQUEST,
            context={"url_host": urlparse(url).hostname or ""},
        ):
            with httpx.Client(
                follow_redirects=False,
                timeout=timeout,
                headers={"User-Agent": self._settings.scraper_user_agent},
            ) as client:
                current = url
                for _ in range(max_redirects + 1):
                    assert_url_safe_for_download(current)
                    response = client.get(current)
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise RequestError(
                                "Redirect sem Location",
                                code="INVALID_REQUEST",
                            )
                        current = str(httpx.URL(current).join(location))
                        continue
                    if response.status_code >= 400:
                        raise RequestError(
                            f"Download de imagem falhou (HTTP {response.status_code})",
                            code="UPSTREAM_ERROR",
                            retryable=response.status_code >= 500,
                        )
                    content_length = response.headers.get("content-length")
                    if content_length and int(content_length) > max_bytes:
                        raise RequestError(
                            "Imagem excede tamanho máximo permitido",
                            code="INVALID_REQUEST",
                        )
                    data = response.content
                    if len(data) > max_bytes:
                        raise RequestError(
                            "Imagem excede tamanho máximo permitido",
                            code="INVALID_REQUEST",
                        )
                    claimed = response.headers.get("content-type")
                    mime, width, height, ext = validate_image_bytes(
                        data,
                        claimed_content_type=claimed,
                        max_dimension=self._settings.image_max_dimension,
                    )
                    digest = hashlib.sha256(data).hexdigest()
                    return DownloadedImage(
                        data=data,
                        content_type=mime,
                        width=width,
                        height=height,
                        sha256=digest,
                        extension=ext,
                        source_url=current,
                    )
                raise RequestError(
                    "Muitos redirects ao baixar imagem",
                    code="INVALID_REQUEST",
                )
