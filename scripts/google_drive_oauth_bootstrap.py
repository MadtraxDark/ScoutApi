#!/usr/bin/env python3
"""One-shot OAuth bootstrap for the dedicated Google Drive storage account.

Run locally (never in production containers as a user-facing flow):

  .\\.venv\\Scripts\\python.exe scripts/google_drive_oauth_bootstrap.py

Prerequisites:
  - Google Cloud project with **Google Drive API** enabled
  - OAuth client type **Desktop app** (Aplicativo para computador) — required
  - Env: GOOGLE_DRIVE_CLIENT_ID, GOOGLE_DRIVE_CLIENT_SECRET in ``.env``

Do **not** reuse the Web client used by Supabase Auth / PriceScout login.

Use --write-env to persist credentials locally without printing secrets.
Scope: https://www.googleapis.com/auth/drive.file
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import tempfile
from pathlib import Path

SCOPE = "https://www.googleapis.com/auth/drive.file"
FOLDER_MIME = "application/vnd.google-apps.folder"
# Fixed loopback port (register on Web clients if you must use one).
OAUTH_PORT = 8765
REDIRECT_URIS = (
    f"http://127.0.0.1:{OAUTH_PORT}/",
    f"http://localhost:{OAUTH_PORT}/",
)


def _write_env_values(env_path: Path, values: dict[str, str]) -> None:
    """Replace only selected keys atomically, without backups containing secrets."""
    for key, value in values.items():
        if any(char in value for char in ("\n", "\r", '"', "\\", "$")):
            raise ValueError(f"Valor inválido para {key}")
    content = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    lines = []
    remaining = dict(values)
    for line in content.splitlines():
        key = line.partition("=")[0].strip()
        if key in values:
            if key in remaining:
                lines.append(f'{key}="{remaining.pop(key)}"')
        else:
            lines.append(line)
    lines.extend(f'{key}="{value}"' for key, value in remaining.items())
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=env_path.parent, delete=False
        ) as handle:
            temporary = handle.name
            handle.write("\n".join(lines) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if env_path.exists():
            os.chmod(temporary, env_path.stat().st_mode)
        os.replace(temporary, env_path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _load_dotenv() -> None:
    """Load key=value pairs from repo ``.env`` into os.environ (no override)."""
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


def _mask_client_id(client_id: str) -> str:
    if len(client_id) < 24:
        return "(curto demais — confira o .env)"
    return f"{client_id[:16]}…{client_id[-12:]}"


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-env",
        action="store_true",
        help="Salva token e pasta no .env sem imprimir credenciais.",
    )
    args = parser.parse_args()
    _load_dotenv()
    client_id = os.environ.get("GOOGLE_DRIVE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_DRIVE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        print(
            "Defina GOOGLE_DRIVE_CLIENT_ID e GOOGLE_DRIVE_CLIENT_SECRET "
            "no ambiente ou no arquivo .env na raiz do projeto.",
            file=sys.stderr,
        )
        return 1

    if _port_in_use(OAUTH_PORT):
        print(
            f"Porta {OAUTH_PORT} já está em uso. Feche a aba/processo do "
            "bootstrap anterior e tente de novo "
            f"(ex.: Get-NetTCPConnection -LocalPort {OAUTH_PORT}).",
            file=sys.stderr,
        )
        return 1

    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from oauthlib.oauth2.rfc6749.errors import MismatchingStateError

    redirect_used = REDIRECT_URIS[0]
    print(
        "OAuth Drive (conta dedicada de storage)\n"
        f"  client_id: {_mask_client_id(client_id)}\n"
        f"  redirect_uri: {redirect_used}\n"
        "\n"
        "IMPORTANTE: use um cliente OAuth tipo Desktop (Aplicativo para\n"
        "computador), NÃO o cliente Web do login Supabase.\n"
        "Se o Google mostrar redirect_uri_mismatch, o client_id do .env\n"
        "ainda é o Web/Supabase — crie um Desktop novo e atualize o .env.\n"
        f"\nAbrindo consentimento em 127.0.0.1:{OAUTH_PORT} …\n"
        "Use só a janela que o script abrir; não recarregue URLs antigas.\n"
    )

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": list(REDIRECT_URIS),
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, scopes=[SCOPE])
    try:
        creds = flow.run_local_server(
            host="127.0.0.1",
            port=OAUTH_PORT,
            access_type="offline",
            prompt="consent",
            open_browser=True,
            timeout_seconds=300,
            authorization_prompt_message="",
        )
    except OSError as exc:
        print(
            f"Não foi possível abrir o servidor local na porta {OAUTH_PORT}: {exc}",
            file=sys.stderr,
        )
        return 1
    except MismatchingStateError:
        print(
            "Falha CSRF (state). Causas comuns:\n"
            "  - redirect_uri_mismatch na página do Google (corrija o client)\n"
            "  - duas abas/tentativas ao mesmo tempo\n"
            "  - callback antigo na porta 8765\n"
            "Feche abas de accounts.google.com, confirme o client Desktop no\n"
            ".env, aguarde 1 minuto e rode o script de novo uma única vez.",
            file=sys.stderr,
        )
        return 1

    if creds is None:
        print(
            "Consentimento não concluído em 300 segundos. Tente novamente.",
            file=sys.stderr,
        )
        return 1

    if not creds.refresh_token:
        print(
            "Nenhum refresh_token retornado. Revogue o acesso do app em "
            "https://myaccount.google.com/permissions e tente de novo "
            "(prompt=consent).",
            file=sys.stderr,
        )
        return 1

    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    def ensure_folder(name: str, parent_id: str | None = None) -> str:
        q = f"name = '{name}' and mimeType = '{FOLDER_MIME}' and trashed = false"
        if parent_id:
            q += f" and '{parent_id}' in parents"
        else:
            q += " and 'root' in parents"
        found = (
            service.files()
            .list(q=q, spaces="drive", fields="files(id)", pageSize=1)
            .execute()
            .get("files")
            or []
        )
        if found:
            return str(found[0]["id"])
        body: dict[str, object] = {"name": name, "mimeType": FOLDER_MIME}
        if parent_id:
            body["parents"] = [parent_id]
        created = service.files().create(body=body, fields="id").execute()
        return str(created["id"])

    products = os.environ.get("GOOGLE_DRIVE_ROOT_FOLDER_ID", "").strip()
    if products:
        # Renewal must retain the storage root used by existing catalog images.
        service.files().get(fileId=products, fields="id").execute()
    else:
        pricescout = ensure_folder("PriceScout")
        products = ensure_folder("products", parent_id=pricescout)

    if args.write_env:
        env_path = Path(__file__).resolve().parents[1] / ".env"
        _write_env_values(
            env_path,
            {
                "GOOGLE_DRIVE_REFRESH_TOKEN": creds.refresh_token,
                "GOOGLE_DRIVE_ROOT_FOLDER_ID": products,
            },
        )
        print("Autorização renovada e salva no .env; credenciais não exibidas.")
        print("Recrie api e image-optimizer para aplicar a configuração.")
        return 0

    print("# Cole no .env (nunca commitar valores reais):")
    print(f"GOOGLE_DRIVE_REFRESH_TOKEN={creds.refresh_token}")
    print(f"GOOGLE_DRIVE_ROOT_FOLDER_ID={products}")
    print("# Pasta criada: PriceScout/products")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
