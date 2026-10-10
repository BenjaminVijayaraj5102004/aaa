import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from vitap_vtop_client.client import VtopClient
from vitap_vtop_client.exceptions import VtopLoginError, VtopSessionError
from vitap_vtop_client.login.model.restorable_session_model import RestorableSession

from vtop_vitap_login import (
    DEFAULT_SESSION_DIR,
    DEFAULT_SESSION_TIMEOUT_SECONDS,
    VtopLoginOtpRequiredError,
    VtopSessionError,
    VtopSessionStore,
    authenticate_user,
    get_authenticated_client,
    session_store,
    vtop_client_session,
)


@pytest.fixture
def temp_store(tmp_path: Path):
    return VtopSessionStore(storage_dir=tmp_path, timeout_seconds=60)


@pytest.fixture
def sample_session_dict():
    return {
        "registration_number": "23BCE7152",
        "cookie": "mock_cookie_abc123",
        "csrf_token": "mock_csrf_xyz789",
        "user_agent": "Mozilla/5.0 TestAgent",
    }


@pytest.fixture
def sample_restorable_session(sample_session_dict):
    return RestorableSession(**sample_session_dict)


class TestVtopSessionStore:
    def test_init_creates_directory(self, tmp_path: Path):
        target_dir = tmp_path / "custom_sessions"
        assert not target_dir.exists()
        store = VtopSessionStore(storage_dir=target_dir)
        assert target_dir.exists()
        assert store.timeout_seconds == DEFAULT_SESSION_TIMEOUT_SECONDS

    def test_save_and_load_restorable_session(self, temp_store, sample_restorable_session):
        reg_no = "23BCE7152"
        temp_store.save(reg_no, sample_restorable_session)

        loaded = temp_store.load(reg_no)
        assert loaded is not None
        assert loaded["registration_number"] == reg_no
        assert loaded["cookie"] == "mock_cookie_abc123"
        assert loaded["csrf_token"] == "mock_csrf_xyz789"
        assert loaded["user_agent"] == "Mozilla/5.0 TestAgent"

    def test_save_and_load_dict(self, temp_store, sample_session_dict):
        reg_no = "23bce7152"  # lowercase input
        temp_store.save(reg_no, sample_session_dict)

        loaded = temp_store.load("23BCE7152")
        assert loaded is not None
        assert loaded["cookie"] == "mock_cookie_abc123"

    def test_save_unsupported_type_raises_type_error(self, temp_store):
        with pytest.raises(TypeError, match="Unsupported session type"):
            temp_store.save("23BCE7152", "not_a_valid_session_type")  # type: ignore

    def test_load_nonexistent_returns_none(self, temp_store):
        assert temp_store.load("NONEXISTENT") is None

    def test_load_expired_session_purges_file(self, tmp_path, sample_session_dict):
        short_store = VtopSessionStore(storage_dir=tmp_path, timeout_seconds=1)
        reg_no = "23BCE7152"
        short_store.save(reg_no, sample_session_dict)

        user_file = short_store._get_user_file(reg_no)
        assert user_file.exists()

        # Artificially set timestamp to 10 seconds ago
        content = json.loads(user_file.read_text(encoding="utf-8"))
        content["timestamp"] = time.time() - 10
        user_file.write_text(json.dumps(content), encoding="utf-8")

        loaded = short_store.load(reg_no)
        assert loaded is None
        assert not user_file.exists()

    def test_load_corrupted_file_purges_and_returns_none(self, temp_store):
        reg_no = "23BCE7152"
        user_file = temp_store._get_user_file(reg_no)
        user_file.write_text("invalid_json{broken", encoding="utf-8")

        loaded = temp_store.load(reg_no)
        assert loaded is None
        assert not user_file.exists()

    def test_delete_existing_and_nonexistent(self, temp_store, sample_session_dict):
        reg_no = "23BCE7152"
        temp_store.save(reg_no, sample_session_dict)
        user_file = temp_store._get_user_file(reg_no)
        assert user_file.exists()

        temp_store.delete(reg_no)
        assert not user_file.exists()

        # Deleting non-existent should be a safe no-op
        temp_store.delete("NONEXISTENT")

    def test_is_active_and_get_remaining_ttl(self, temp_store, sample_session_dict):
        reg_no = "23BCE7152"
        assert not temp_store.is_active(reg_no)
        assert temp_store.get_remaining_ttl(reg_no) is None

        temp_store.save(reg_no, sample_session_dict)
        assert temp_store.is_active(reg_no)

        ttl = temp_store.get_remaining_ttl(reg_no)
        assert ttl is not None
        assert 0 < ttl <= 60

    def test_list_active_sessions_and_clear_all(self, temp_store, sample_session_dict):
        temp_store.save("23BCE7152", sample_session_dict)
        temp_store.save("23BCE7153", sample_session_dict)

        active = temp_store.list_active_sessions()
        assert sorted(active) == ["23BCE7152", "23BCE7153"]

        cleared_count = temp_store.clear_all()
        assert cleared_count == 2
        assert temp_store.list_active_sessions() == []

    def test_sanitization_and_validation(self, temp_store):
        with pytest.raises(ValueError, match="Registration number cannot be empty"):
            temp_store._get_user_file("")

        with pytest.raises(ValueError, match="Registration number cannot be empty"):
            temp_store._get_user_file("   ")

        with pytest.raises(ValueError, match="Invalid registration number format"):
            temp_store._get_user_file("???///")

        # Path traversal characters should be sanitized out safely
        safe_path = temp_store._get_user_file("../../23BCE7152")
        assert safe_path.name == "23BCE7152.json"
        assert safe_path.parent == temp_store.storage_dir


class TestAuthenticationFunctions:
    def test_authenticate_user_success(self, temp_store, sample_restorable_session):
        async def _test():
            with patch("vtop_vitap_login.login.VtopClient") as MockClient:
                mock_instance = MagicMock()
                mock_instance.login = AsyncMock()
                mock_instance.session = sample_restorable_session
                mock_instance.close = AsyncMock()
                MockClient.return_value = mock_instance

                client = await authenticate_user("23BCE7152", "SecretPass123", store=temp_store)

                assert client == mock_instance
                mock_instance.login.assert_awaited_once()
                assert temp_store.is_active("23BCE7152")

        asyncio.run(_test())

    def test_authenticate_user_failure_closes_client(self, temp_store):
        async def _test():
            with patch("vtop_vitap_login.login.VtopClient") as MockClient:
                mock_instance = MagicMock()
                mock_instance.login = AsyncMock(side_effect=VtopLoginError("Invalid credentials", status_code=401))
                mock_instance.close = AsyncMock()
                MockClient.return_value = mock_instance

                with pytest.raises(VtopLoginError):
                    await authenticate_user("23BCE7152", "WrongPassword", store=temp_store)

                mock_instance.close.assert_awaited_once()
                assert not temp_store.is_active("23BCE7152")

        asyncio.run(_test())

    def test_authenticate_user_missing_credentials(self, temp_store):
        async def _test():
            with pytest.raises(ValueError, match="Registration number is required"):
                await authenticate_user("", "pwd", store=temp_store)

            with pytest.raises(ValueError, match="Password is required"):
                await authenticate_user("23BCE7152", "", store=temp_store)

        asyncio.run(_test())

    def test_get_authenticated_client_with_explicit_session_data(self, temp_store, sample_session_dict):
        async def _test():
            with patch("vtop_vitap_login.login.VtopClient.restore") as mock_restore:
                mock_client = MagicMock()
                mock_restore.return_value = mock_client

                client = await get_authenticated_client(
                    registration_number="23BCE7152",
                    session_data=sample_session_dict,
                    store=temp_store,
                )

                assert client == mock_client
                mock_restore.assert_called_once_with(
                    registration_number=sample_session_dict["registration_number"],
                    cookie=sample_session_dict["cookie"],
                    csrf_token=sample_session_dict["csrf_token"],
                    user_agent=sample_session_dict["user_agent"],
                )

        asyncio.run(_test())

    def test_get_authenticated_client_restores_from_cache(self, temp_store, sample_session_dict):
        async def _test():
            temp_store.save("23BCE7152", sample_session_dict)

            with patch("vtop_vitap_login.login.VtopClient.restore") as mock_restore:
                mock_client = MagicMock()
                mock_restore.return_value = mock_client

                client = await get_authenticated_client(
                    registration_number="23BCE7152",
                    password=None,
                    store=temp_store,
                )

                assert client == mock_client
                mock_restore.assert_called_once()

        asyncio.run(_test())

    def test_get_authenticated_client_cache_miss_with_password(self, temp_store, sample_restorable_session):
        async def _test():
            with patch("vtop_vitap_login.login.VtopClient") as MockClient:
                mock_instance = MagicMock()
                mock_instance.login = AsyncMock()
                mock_instance.session = sample_restorable_session
                mock_instance.close = AsyncMock()
                MockClient.return_value = mock_instance

                client = await get_authenticated_client(
                    registration_number="23BCE7152",
                    password="TestPassword",
                    store=temp_store,
                )

                assert client == mock_instance
                mock_instance.login.assert_awaited_once()
                assert temp_store.is_active("23BCE7152")

        asyncio.run(_test())

    def test_get_authenticated_client_cache_miss_without_password_raises_session_error(self, temp_store):
        async def _test():
            with pytest.raises(VtopSessionError, match="No active session found"):
                await get_authenticated_client(
                    registration_number="23BCE7152",
                    password=None,
                    store=temp_store,
                )

        asyncio.run(_test())

    def test_get_authenticated_client_empty_reg_no_raises(self, temp_store):
        async def _test():
            with pytest.raises(ValueError, match="Registration number is required"):
                await get_authenticated_client(registration_number="  ")

        asyncio.run(_test())


class TestVtopClientSessionContextManager:
    def test_context_manager_normal_lifecycle(self, temp_store, sample_session_dict):
        async def _test():
            temp_store.save("23BCE7152", sample_session_dict)
            mock_client = MagicMock()
            mock_client.close = AsyncMock()

            with patch("vtop_vitap_login.login.VtopClient.restore", return_value=mock_client):
                async with vtop_client_session("23BCE7152", store=temp_store) as client:
                    assert client == mock_client

                mock_client.close.assert_awaited_once()

        asyncio.run(_test())

    def test_context_manager_session_error_purges_cache_and_closes(self, temp_store, sample_session_dict):
        async def _test():
            reg_no = "23BCE7152"
            temp_store.save(reg_no, sample_session_dict)
            assert temp_store.is_active(reg_no)

            mock_client = MagicMock()
            mock_client.close = AsyncMock()

            with patch("vtop_vitap_login.login.VtopClient.restore", return_value=mock_client):
                with pytest.raises(VtopSessionError, match="Session dead"):
                    async with vtop_client_session(reg_no, store=temp_store):
                        raise VtopSessionError("Session dead", status_code=401)

                assert not temp_store.is_active(reg_no)
                mock_client.close.assert_awaited_once()

        asyncio.run(_test())


class TestVtopStoreEdgeCases:
    def test_load_and_delete_with_invalid_reg_no(self, temp_store):
        assert temp_store.load("") is None
        # delete should gracefully return
        temp_store.delete("")

    def test_get_remaining_ttl_edge_cases(self, temp_store, sample_session_dict):
        assert temp_store.get_remaining_ttl("") is None
        assert temp_store.get_remaining_ttl("NONEXISTENT") is None

        # Corrupted JSON returns None
        user_file = temp_store._get_user_file("23BCE7152")
        user_file.write_text("corrupted", encoding="utf-8")
        assert temp_store.get_remaining_ttl("23BCE7152") is None

        # Expired returns None
        payload = {"timestamp": time.time() - 200, "session": sample_session_dict}
        user_file.write_text(json.dumps(payload), encoding="utf-8")
        assert temp_store.get_remaining_ttl("23BCE7152") is None

    def test_delete_handles_oserror(self, temp_store, sample_session_dict):
        temp_store.save("23BCE7152", sample_session_dict)
        user_file = temp_store._get_user_file("23BCE7152")

        with patch.object(Path, "unlink", side_effect=OSError("Permission denied")):
            # Should not raise exception
            temp_store.delete("23BCE7152")
        assert user_file.exists()

    def test_list_and_clear_when_directory_missing(self, tmp_path):
        non_existent_dir = tmp_path / "not_there"
        store = VtopSessionStore(storage_dir=non_existent_dir)
        # Delete directory
        non_existent_dir.rmdir()
        assert not non_existent_dir.exists()

        assert store.list_active_sessions() == []
        assert store.clear_all() == 0

    def test_clear_all_handles_oserror(self, temp_store, sample_session_dict):
        temp_store.save("23BCE7152", sample_session_dict)
        with patch.object(Path, "unlink", side_effect=OSError("Lock error")):
            cleared = temp_store.clear_all()
            assert cleared == 0

    def test_authenticate_user_without_session_attribute(self, temp_store):
        async def _test():
            with patch("vtop_vitap_login.login.VtopClient") as MockClient:
                mock_instance = MagicMock()
                mock_instance.login = AsyncMock()
                mock_instance.session = None
                mock_instance.close = AsyncMock()
                MockClient.return_value = mock_instance

                client = await authenticate_user("23BCE7152", "SecretPass123", store=temp_store)
                assert client == mock_instance
                assert not temp_store.is_active("23BCE7152")

        asyncio.run(_test())
