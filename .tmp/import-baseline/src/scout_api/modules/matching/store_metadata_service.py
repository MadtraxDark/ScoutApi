import io
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from PIL import Image

from scout_api.modules.crawler.stores import STORE_CONFIGS
from scout_api.modules.matching.models import StoreMetadata
from scout_api.modules.matching.store_metadata_repository import StoreMetadataRepository


class StoreNotFoundError(Exception):
    pass


class InvalidStoreLogoError(ValueError):
    pass


MAX_STORE_LOGO_BYTES = 2 * 1024 * 1024
STORE_LOGO_MIMES = {
    "PNG": "image/png",
    "WEBP": "image/webp",
    "AVIF": "image/avif",
    "JPEG": "image/jpeg",
}
STORE_LOGO_EXTENSIONS = {
    "png": "image/png",
    "webp": "image/webp",
    "avif": "image/avif",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
}


@dataclass(frozen=True, slots=True)
class StoreLogoUpload:
    data: bytes
    mime_type: str
    is_svg: bool


def validate_store_logo_upload(
    data: bytes, filename: str, mime_type: str
) -> StoreLogoUpload:
    if not data or len(data) > MAX_STORE_LOGO_BYTES:
        raise InvalidStoreLogoError("A logo deve ter no máximo 2 MB.")
    extension = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if extension == "svg" and mime_type.lower() in {
        "image/svg+xml",
        "application/svg+xml",
    }:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidStoreLogoError(
                "O conteúdo informado não é um SVG válido."
            ) from exc
        validate_store_logo(text)
        return StoreLogoUpload(data, "image/svg+xml", True)
    if STORE_LOGO_EXTENSIONS.get(extension) != mime_type.lower():
        raise InvalidStoreLogoError(
            "A extensão do arquivo não corresponde ao formato informado."
        )
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
            detected = STORE_LOGO_MIMES.get(image.format or "")
            if (
                image.width < 1
                or image.height < 1
                or image.width * image.height > 40_000_000
            ):
                raise InvalidStoreLogoError(
                    "As dimensões da logo excedem o limite permitido."
                )
    except Exception as exc:
        raise InvalidStoreLogoError("O arquivo não contém uma imagem válida.") from exc
    if detected is None or detected != mime_type.lower():
        raise InvalidStoreLogoError(
            "O formato real do arquivo não corresponde ao MIME informado."
        )
    if extension not in {"png", "webp", "avif", "jpg", "jpeg"}:
        raise InvalidStoreLogoError("Formato de logo não permitido.")
    return StoreLogoUpload(data, detected, False)


def validate_store_logo(value: str | None) -> None:
    if value is None:
        return
    if len(value.encode("utf-8")) > MAX_STORE_LOGO_BYTES:
        raise InvalidStoreLogoError("A logo deve ter no máximo 2 MB.")
    if re.search(r"<!DOCTYPE|<!ENTITY", value, re.IGNORECASE):
        raise InvalidStoreLogoError("O SVG não pode declarar entidades ou DOCTYPE.")
    try:
        root = ET.fromstring(value)
    except ET.ParseError as exc:
        raise InvalidStoreLogoError(
            "O conteúdo informado não é um SVG válido."
        ) from exc
    if root.tag.split("}")[-1].lower() != "svg":
        raise InvalidStoreLogoError("O conteúdo informado não é um SVG válido.")
    forbidden_tags = {"script", "foreignobject", "iframe", "object", "embed", "style"}
    for element in root.iter():
        if element.tag.split("}")[-1].lower() in forbidden_tags:
            raise InvalidStoreLogoError("O SVG contém conteúdo ativo.")
        for attribute, content in element.attrib.items():
            name = attribute.split("}")[-1].lower()
            if name.startswith("on"):
                raise InvalidStoreLogoError("O SVG contém um handler de evento.")
            if name in {"href", "src"} and not content.strip().startswith("#"):
                raise InvalidStoreLogoError(
                    "O SVG não pode referenciar recursos externos."
                )
            if name == "style" and re.search(
                r"url\s*\(|@import|expression\s*\(", content, re.I
            ):
                raise InvalidStoreLogoError("O SVG contém CSS ativo ou externo.")


class StoreMetadataService:
    def __init__(self, repository: StoreMetadataRepository) -> None:
        self.repository = repository

    def list_metadata(self) -> dict[str, StoreMetadata]:
        return self.repository.get_all()

    def update(
        self, key: str, display_name: str, logo_svg: str | None
    ) -> StoreMetadata:
        if key not in STORE_CONFIGS:
            raise StoreNotFoundError(key)
        name = display_name.strip()
        if not name:
            raise ValueError("O nome de exibição é obrigatório.")
        validate_store_logo(logo_svg)
        return self.repository.save(key, name, logo_svg)
