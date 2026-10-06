from __future__ import annotations

import io
import os
import sys
from pathlib import Path

from PIL import Image


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> int:
    _load_dotenv()
    from scout_api.core.config import get_settings

    get_settings.cache_clear()
    from scout_api.modules.images.drive_client import GoogleDriveClient
    from scout_api.modules.images.optimizer import AvifOptimizer

    settings = get_settings()
    client = GoogleDriveClient(settings)
    if not client.is_configured():
        print(
            "GOOGLE_DRIVE_* incompleto — rode "
            "scripts/google_drive_oauth_bootstrap.py e preencha "
            "REFRESH_TOKEN + ROOT_FOLDER_ID no .env.",
            file=sys.stderr,
        )
        return 1

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (20, 180, 90)).save(buf, format="PNG")
    original = buf.getvalue()

    products = client.ensure_folder("products", parent_id=client.root_folder_id)
    smoke = client.ensure_folder("_smoke", parent_id=products)
    original_folder = client.ensure_folder("original", parent_id=smoke)
    optimized_folder = client.ensure_folder("optimized", parent_id=smoke)

    orig_id = client.upload_bytes(
        name="smoke.png",
        parent_id=original_folder,
        data=original,
        mime_type="image/png",
    )
    downloaded = client.download_bytes(orig_id)
    assert downloaded == original

    try:
        avif = AvifOptimizer(settings).convert(original)
    except Exception as exc:
        print(f"AVIF falhou (original ok): {exc}", file=sys.stderr)
        client.delete_file(orig_id)
        return 1

    opt_id = client.upload_bytes(
        name="smoke.avif",
        parent_id=optimized_folder,
        data=avif.data,
        mime_type="image/avif",
    )
    assert client.download_bytes(opt_id) == avif.data
    client.delete_file(opt_id)
    client.delete_file(orig_id)
    print("smoke_google_drive_images: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
