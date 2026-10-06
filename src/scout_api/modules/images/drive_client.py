"""Google Drive API client for the dedicated PriceScout storage account."""

from __future__ import annotations

import io
import json
import logging
import threading
from typing import Any, Protocol

import google_auth_httplib2
import googleapiclient.http
import httplib2
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

from scout_api.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
FOLDER_MIME = "application/vnd.google-apps.folder"


class DriveStorage(Protocol):
    @property
    def root_folder_id(self) -> str: ...

    def ensure_folder(self, name: str, *, parent_id: str) -> str: ...

    def upload_bytes(
        self,
        *,
        name: str,
        parent_id: str,
        data: bytes,
        mime_type: str,
    ) -> str: ...

    def download_bytes(self, file_id: str) -> bytes: ...

    def delete_file(self, file_id: str) -> None: ...


class DriveNotConfiguredError(RuntimeError):
    """Raised when Google Drive OAuth settings are incomplete."""


class DriveClientError(RuntimeError):
    """Sanitized storage failure with a stable, provider-neutral category."""

    def __init__(
        self,
        message: str,
        *,
        availability: str = "storage_error",
        code: str = "storage_error",
        retryable: bool = False,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.availability = availability
        self.code = code
        self.retryable = retryable
        self.http_status = http_status


def _drive_http_error(exc: HttpError) -> DriveClientError:
    status = getattr(getattr(exc, "resp", None), "status", None)
    reason = ""
    try:
        payload = json.loads(exc.content.decode("utf-8", errors="replace"))
        errors = payload.get("error", {}).get("errors", [])
        reason = " ".join(str(item.get("reason", "")) for item in errors).casefold()
    except (AttributeError, TypeError, ValueError):
        pass
    if status == 404:
        return DriveClientError(
            "Storage file not found",
            availability="not_found",
            code="storage_not_found",
            http_status=status,
        )
    if status == 403 and any(
        term in reason for term in ("quota", "ratelimit", "userlimit", "dailylimit")
    ):
        return DriveClientError(
            "Storage quota temporarily unavailable",
            availability="temporarily_unavailable",
            code="storage_unavailable",
            retryable=True,
            http_status=status,
        )
    if status == 403:
        return DriveClientError(
            "Storage access denied",
            availability="permission_denied",
            code="storage_permission_denied",
            http_status=status,
        )
    if status == 401:
        return DriveClientError(
            "Storage credentials rejected",
            code="storage_credentials_invalid",
            http_status=status,
        )
    if status == 429 or (isinstance(status, int) and status >= 500):
        return DriveClientError(
            "Storage temporarily unavailable",
            availability="temporarily_unavailable",
            code="storage_unavailable",
            retryable=True,
            http_status=status,
        )
    return DriveClientError("Storage request failed", http_status=status)


def _drive_transport_error(exc: BaseException) -> DriveClientError:
    if isinstance(exc, GoogleAuthError) and any(
        reason in str(exc).casefold()
        for reason in ("invalid_grant", "invalid_client", "unauthorized_client")
    ):
        return DriveClientError(
            "Storage credentials are invalid", code="storage_credentials_invalid"
        )
    return DriveClientError(
        "Storage temporarily unavailable",
        availability="temporarily_unavailable",
        code="storage_unavailable",
        retryable=True,
    )


class GoogleDriveClient:
    """Drive v3 client using offline OAuth refresh token (backend-only).

    Thread-safe for concurrent AVIF workers: httplib2.Http is not thread-safe,
    so each API request gets its own AuthorizedHttp via requestBuilder
    (see google-api-python-client thread safety docs).
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        service: Any | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._service = service
        self._service_lock = threading.Lock()

    @property
    def root_folder_id(self) -> str:
        folder_id = (self._settings.google_drive_root_folder_id or "").strip()
        if not folder_id:
            raise DriveNotConfiguredError("GOOGLE_DRIVE_ROOT_FOLDER_ID não configurado")
        return folder_id

    def is_configured(self) -> bool:
        s = self._settings
        return bool(
            (s.google_drive_client_id or "").strip()
            and (s.google_drive_client_secret or "").strip()
            and (s.google_drive_refresh_token or "").strip()
            and (s.google_drive_root_folder_id or "").strip()
        )

    def _credentials(self) -> Credentials:
        s = self._settings
        client_id = (s.google_drive_client_id or "").strip()
        client_secret = (s.google_drive_client_secret or "").strip()
        refresh_token = (s.google_drive_refresh_token or "").strip()
        if not (client_id and client_secret and refresh_token):
            raise DriveNotConfiguredError(
                "Credenciais Google Drive incompletas "
                "(CLIENT_ID / CLIENT_SECRET / REFRESH_TOKEN)"
            )
        creds = Credentials(  # type: ignore[no-untyped-call]
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=[DRIVE_SCOPE],
        )
        if not creds.valid:
            creds.refresh(Request())  # type: ignore[no-untyped-call]
        return creds

    def _drive(self) -> Any:
        if self._service is not None:
            return self._service
        with self._service_lock:
            if self._service is not None:
                return self._service
            creds = self._credentials()

            def build_request(
                http: object, *args: object, **kwargs: object
            ) -> googleapiclient.http.HttpRequest:
                # Fresh httplib2.Http per request — required under ThreadPoolExecutor.
                new_http = google_auth_httplib2.AuthorizedHttp(
                    creds, http=httplib2.Http()
                )
                return googleapiclient.http.HttpRequest(new_http, *args, **kwargs)

            authorized_http = google_auth_httplib2.AuthorizedHttp(
                creds, http=httplib2.Http()
            )
            self._service = build(
                "drive",
                "v3",
                http=authorized_http,
                requestBuilder=build_request,
                cache_discovery=False,
            )
            return self._service

    def ensure_folder(self, name: str, *, parent_id: str) -> str:
        """Find or create a folder under parent (drive.file scoped)."""
        safe_name = name.replace("'", "\\'")
        query = (
            f"name = '{safe_name}' and '{parent_id}' in parents "
            f"and mimeType = '{FOLDER_MIME}' and trashed = false"
        )
        try:
            existing = (
                self._drive()
                .files()
                .list(q=query, spaces="drive", fields="files(id,name)", pageSize=1)
                .execute()
            )
            files = existing.get("files") or []
            if files:
                return str(files[0]["id"])
            meta = {
                "name": name,
                "mimeType": FOLDER_MIME,
                "parents": [parent_id],
            }
            created = self._drive().files().create(body=meta, fields="id").execute()
            return str(created["id"])
        except HttpError as exc:
            raise _drive_http_error(exc) from exc
        except (OSError, httplib2.HttpLib2Error, GoogleAuthError, TimeoutError) as exc:
            raise _drive_transport_error(exc) from exc

    def upload_bytes(
        self,
        *,
        name: str,
        parent_id: str,
        data: bytes,
        mime_type: str,
    ) -> str:
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime_type, resumable=False)
        body = {"name": name, "parents": [parent_id]}
        try:
            # UUID filenames are stable across retries, including a crash after upload.
            safe_name = name.replace("\\", "\\\\").replace("'", "\\'")
            result = (
                self._drive()
                .files()
                .list(
                    q=(
                        f"name = '{safe_name}' and '{parent_id}' in parents "
                        "and trashed = false"
                    ),
                    spaces="drive",
                    fields="files(id)",
                    pageSize=1,
                )
                .execute()
            )
            if result.get("files"):
                return str(result["files"][0]["id"])
            created = (
                self._drive()
                .files()
                .create(body=body, media_body=media, fields="id")
                .execute()
            )
            return str(created["id"])
        except HttpError as exc:
            raise _drive_http_error(exc) from exc
        except (OSError, httplib2.HttpLib2Error, GoogleAuthError, TimeoutError) as exc:
            raise _drive_transport_error(exc) from exc

    def download_bytes(self, file_id: str) -> bytes:
        try:
            request = self._drive().files().get_media(fileId=file_id)
            buffer = io.BytesIO()
            downloader = MediaIoBaseDownload(buffer, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            return buffer.getvalue()
        except HttpError as exc:
            raise _drive_http_error(exc) from exc
        except (OSError, httplib2.HttpLib2Error, GoogleAuthError, TimeoutError) as exc:
            raise _drive_transport_error(exc) from exc

    def delete_file(self, file_id: str) -> None:
        """Idempotent delete: missing file is success."""
        try:
            self._drive().files().delete(fileId=file_id).execute()
        except HttpError as exc:
            status = getattr(getattr(exc, "resp", None), "status", None)
            if status == 404:
                logger.info("Drive file already absent: %s", file_id[:8])
                return
            raise _drive_http_error(exc) from exc
        except (OSError, httplib2.HttpLib2Error, GoogleAuthError, TimeoutError) as exc:
            raise _drive_transport_error(exc) from exc


class InMemoryDriveStorage:
    """Test double that stores bytes in process memory."""

    root_folder_id: str = "root"

    def __init__(self) -> None:
        self.files: dict[str, tuple[str, bytes, str]] = {}
        self.folders: dict[tuple[str, str], str] = {}
        self._seq = 0

    def _next_id(self) -> str:
        self._seq += 1
        return f"mem-{self._seq}"

    def ensure_folder(self, name: str, *, parent_id: str) -> str:
        key = (parent_id, name)
        if key not in self.folders:
            self.folders[key] = self._next_id()
        return self.folders[key]

    def upload_bytes(
        self,
        *,
        name: str,
        parent_id: str,
        data: bytes,
        mime_type: str,
    ) -> str:
        file_id = self._next_id()
        self.files[file_id] = (name, data, mime_type)
        return file_id

    def download_bytes(self, file_id: str) -> bytes:
        if file_id not in self.files:
            raise DriveClientError(
                "Storage file not found",
                availability="not_found",
                code="storage_not_found",
                http_status=404,
            )
        return self.files[file_id][1]

    def delete_file(self, file_id: str) -> None:
        self.files.pop(file_id, None)


_development_drive_storage = InMemoryDriveStorage()


def get_drive_storage(settings: Settings | None = None) -> DriveStorage:
    """Resolve the configured Drive backend or shared process-local dev storage."""
    cfg = settings or get_settings()
    drive = GoogleDriveClient(cfg)
    if drive.is_configured():
        return drive
    return _development_drive_storage
