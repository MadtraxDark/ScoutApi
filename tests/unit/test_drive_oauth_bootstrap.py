"""OAuth renewal preserves local configuration and never prints credentials."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

BOOTSTRAP = (
    Path(__file__).resolve().parents[2] / "scripts/google_drive_oauth_bootstrap.py"
)
write_env_values = runpy.run_path(str(BOOTSTRAP))["_write_env_values"]


def test_renewal_preserves_other_settings_and_removes_duplicate_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = tmp_path / ".env"
    env.write_text(
        '# Config\nDATABASE_URL="unchanged"\n'
        "GOOGLE_DRIVE_REFRESH_TOKEN=old\n"
        'GOOGLE_DRIVE_ROOT_FOLDER_ID="existing-root"\n'
        "GOOGLE_DRIVE_REFRESH_TOKEN=stale-duplicate\n",
        encoding="utf-8",
    )
    write_env_values(env, {"GOOGLE_DRIVE_REFRESH_TOKEN": "test-only-renewal"})
    assert env.read_text(encoding="utf-8") == (
        '# Config\nDATABASE_URL="unchanged"\n'
        'GOOGLE_DRIVE_REFRESH_TOKEN="test-only-renewal"\n'
        'GOOGLE_DRIVE_ROOT_FOLDER_ID="existing-root"\n'
    )
    assert list(tmp_path.iterdir()) == [env]
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("value", ["token\ninjected=value", 'token"', "token$"])
def test_invalid_env_value_leaves_existing_file_intact(
    tmp_path: Path, value: str
) -> None:
    env = tmp_path / ".env"
    env.write_text("# unchanged\n", encoding="utf-8")
    with pytest.raises(ValueError):
        write_env_values(env, {"GOOGLE_DRIVE_REFRESH_TOKEN": value})
    assert env.read_text(encoding="utf-8") == "# unchanged\n"


def test_first_setup_appends_missing_keys(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    write_env_values(env, {"GOOGLE_DRIVE_REFRESH_TOKEN": "test-only-token"})
    assert (
        env.read_text(encoding="utf-8")
        == 'GOOGLE_DRIVE_REFRESH_TOKEN="test-only-token"\n'
    )


@pytest.mark.parametrize("timed_out", [False, True])
def test_consent_renewal_preserves_root_without_printing_token(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    timed_out: bool,
) -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient import discovery

    main = runpy.run_path(str(BOOTSTRAP))["main"]
    monkeypatch.setitem(main.__globals__, "_load_dotenv", lambda: None)
    monkeypatch.setitem(main.__globals__, "_port_in_use", lambda port: False)
    writer = MagicMock()
    monkeypatch.setitem(main.__globals__, "_write_env_values", writer)
    monkeypatch.setattr(sys, "argv", [str(BOOTSTRAP), "--write-env"])
    monkeypatch.setenv("GOOGLE_DRIVE_CLIENT_ID", "test-only-client")
    monkeypatch.setenv("GOOGLE_DRIVE_CLIENT_SECRET", "test-only-secret")
    monkeypatch.setenv("GOOGLE_DRIVE_ROOT_FOLDER_ID", "existing-root")
    flow = MagicMock()
    flow.run_local_server.return_value = (
        None if timed_out else MagicMock(refresh_token="test-only-renewal")
    )
    monkeypatch.setattr(InstalledAppFlow, "from_client_config", lambda *a, **k: flow)
    build = MagicMock()
    monkeypatch.setattr(discovery, "build", build)
    assert main() == (1 if timed_out else 0)
    output = capsys.readouterr()
    assert "test-only-renewal" not in output.out + output.err
    if timed_out:
        writer.assert_not_called()
        build.assert_not_called()
    else:
        build.return_value.files.return_value.get.assert_called_once_with(
            fileId="existing-root", fields="id"
        )
        build.return_value.files.return_value.create.assert_not_called()
        assert writer.call_args.args[1] == {
            "GOOGLE_DRIVE_REFRESH_TOKEN": "test-only-renewal",
            "GOOGLE_DRIVE_ROOT_FOLDER_ID": "existing-root",
        }
