"""Hosted entry fails closed and does not inherit unrestricted local access."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from roadinsight_ui.workspace import configuration, require_editor, unlock

ROOT = Path(__file__).resolve().parents[1]


def test_community_configuration_without_secret_is_read_only(tmp_path):
    env = {"ROADINSIGHT_MODE": "community", "ROADINSIGHT_DATA_DIR": str(tmp_path)}
    assert configuration(env) == ("community", tmp_path, "")
    assert not unlock("", "")
    with pytest.raises(PermissionError):
        require_editor("community", False)
    env["ROADINSIGHT_EDITOR_TOKEN"] = "short"
    with pytest.raises(ValueError, match="32"):
        configuration(env)
    env["ROADINSIGHT_EDITOR_TOKEN"] = "test-only-community-token-1234567890"
    assert unlock(configuration(env)[2], env["ROADINSIGHT_EDITOR_TOKEN"])
    require_editor("community", True)


@pytest.mark.parametrize("token", ["", "test-only-community-token-1234567890"])
def test_cloud_entry_always_starts_as_guest(monkeypatch, tmp_path, token):
    monkeypatch.setenv("ROADINSIGHT_MODE", "local")
    monkeypatch.setenv("ROADINSIGHT_DATA_DIR", str(tmp_path / "workspace"))
    monkeypatch.setenv("ROADINSIGHT_EDITOR_TOKEN", token)
    app = AppTest.from_file(str(ROOT / "streamlit_cloud.py")).run(timeout=30)
    assert not app.exception and not app.error
    # A guest boot must not open or create the shared private workspace.
    assert not (tmp_path / "workspace").exists()
    assert configuration()[0] == "community"


def test_cloud_entry_rejects_short_secret(monkeypatch, tmp_path):
    monkeypatch.setenv("ROADINSIGHT_MODE", "local")
    monkeypatch.setenv("ROADINSIGHT_DATA_DIR", str(tmp_path / "workspace"))
    monkeypatch.setenv("ROADINSIGHT_EDITOR_TOKEN", "short")
    app = AppTest.from_file(str(ROOT / "streamlit_cloud.py")).run(timeout=30)
    assert not app.exception
    assert app.error and "32" in app.error[0].value
    assert not (tmp_path / "workspace").exists()
