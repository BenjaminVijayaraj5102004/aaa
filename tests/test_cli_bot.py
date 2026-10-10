import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from vtop_vitap_login import VtopLoginError, VtopSessionError, VtopSessionStore

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load cli-bot/login.py app
spec = importlib.util.spec_from_file_location("cli_bot_login", PROJECT_ROOT / "cli-bot" / "login.py")
assert spec and spec.loader
cli_bot_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli_bot_mod)
app = cli_bot_mod.app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def test_store(tmp_path):
    store = VtopSessionStore(storage_dir=tmp_path, timeout_seconds=120)
    return store


class TestCliBotApi:
    def test_health_check(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "vtop-cli-bot"

    def test_session_status(self, client, test_store):
        with patch.object(cli_bot_mod, "session_store", test_store):
            test_store.save("23BCE0001", {"cookie": "c", "csrf_token": "t"})
            response = client.get("/v1/status")
            assert response.status_code == 200
            data = response.json()
            assert data["has_active_session"] is True
            assert "23BCE0001" in data["active_users"]
            assert data["total_cached_sessions"] == 1

    def test_check_user_session_active(self, client, test_store):
        with patch.object(cli_bot_mod, "session_store", test_store):
            test_store.save("23BCE7152", {"cookie": "c", "csrf_token": "t"})
            response = client.get("/v1/session/23BCE7152")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "active"
            assert data["registration_number"] == "23BCE7152"

    def test_check_user_session_inactive(self, client, test_store):
        with patch.object(cli_bot_mod, "session_store", test_store):
            response = client.get("/v1/session/UNKNOWN_STUDENT")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "inactive"

    def test_login_missing_reg_no(self, client):
        response = client.post("/v1/login", json={"password": "pwd"})
        assert response.status_code == 400
        assert "UserName (registration number) is required" in response.json()["detail"]

    def test_login_success(self, client):
        mock_vtop_client = MagicMock()
        mock_vtop_client.password = "SecretPass"
        mock_vtop_client.session = MagicMock()
        mock_vtop_client.session.model_dump.return_value = {
            "registration_number": "23BCE7152",
            "cookie": "cookie_val",
            "csrf_token": "token_val",
            "user_agent": "Mozilla",
        }
        mock_vtop_client.close = AsyncMock()

        with patch.object(cli_bot_mod, "get_authenticated_client", new_callable=AsyncMock) as mock_get_client:
            mock_get_client.return_value = mock_vtop_client
            response = client.post("/v1/login", json={"UserName": "23BCE7152", "password": "SecretPass"})
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert data["registration_number"] == "23BCE7152"
            mock_vtop_client.close.assert_awaited_once()

    def test_login_session_error_handling(self, client):
        with patch.object(cli_bot_mod, "get_authenticated_client", new_callable=AsyncMock) as mock_get_client:
            mock_get_client.side_effect = VtopSessionError("Session expired", status_code=401)
            response = client.post("/v1/login", json={"username": "23BCE7152"})
            assert response.status_code == 401
            assert "Session expired" in response.json()["detail"]

    def test_login_credentials_error_handling(self, client):
        with patch.object(cli_bot_mod, "get_authenticated_client", new_callable=AsyncMock) as mock_get_client:
            mock_get_client.side_effect = VtopLoginError("Invalid password", status_code=401)
            response = client.post("/v1/login", json={"UserName": "23BCE7152", "password": "bad"})
            assert response.status_code == 401
            assert "VTOP login failed" in response.json()["detail"]

