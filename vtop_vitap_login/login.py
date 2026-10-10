import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

from vitap_vtop_client.client import VtopClient
from vitap_vtop_client.exceptions import (
    VtopLoginError,
    VtopLoginOtpRequiredError,
    VtopSessionError,
)
from vitap_vtop_client.login.model.restorable_session_model import RestorableSession

logger = logging.getLogger("vtop_service")

# Environment configurable session directory and timeout defaults
SESSION_DIR_ENV = os.environ.get("VTOP_SESSION_DIR")
DEFAULT_SESSION_DIR = (
    Path(SESSION_DIR_ENV).resolve()
    if SESSION_DIR_ENV
    else Path(__file__).resolve().parent.parent / ".vtop_sessions"
)

TIMEOUT_ENV = os.environ.get("VTOP_SESSION_TIMEOUT_SECONDS")
DEFAULT_SESSION_TIMEOUT_SECONDS = (
    int(TIMEOUT_ENV) if TIMEOUT_ENV and TIMEOUT_ENV.isdigit() else 20 * 60
)


class VtopSessionStore:
    """Manages disk-based persistence and lifetime tracking of VTOP student sessions."""

    def __init__(
        self,
        storage_dir: Union[str, Path] = DEFAULT_SESSION_DIR,
        timeout_seconds: int = DEFAULT_SESSION_TIMEOUT_SECONDS,
    ):
        self.storage_dir = Path(storage_dir)
        self.timeout_seconds = timeout_seconds
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def _get_user_file(self, registration_number: str) -> Path:
        if not registration_number or not str(registration_number).strip():
            raise ValueError("Registration number cannot be empty.")
        clean_id = registration_number.strip().upper()
        safe_id = "".join(c for c in clean_id if c.isalnum() or c in ("-", "_"))
        if not safe_id:
            raise ValueError(f"Invalid registration number format: {registration_number!r}")
        return self.storage_dir / f"{safe_id}.json"

    def save(
        self,
        registration_number: str,
        session: Union[RestorableSession, Dict[str, Any]],
    ) -> None:
        """Stores a serialized session with a creation timestamp."""
        file_path = self._get_user_file(registration_number)
        if hasattr(session, "model_dump"):
            session_data = session.model_dump()
        elif isinstance(session, dict):
            session_data = dict(session)
        else:
            raise TypeError(
                f"Unsupported session type: {type(session).__name__}. Expected RestorableSession or dict."
            )

        payload = {
            "timestamp": time.time(),
            "registration_number": registration_number.strip().upper(),
            "session": session_data,
        }
        file_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.info("Saved session for %s to %s", registration_number, file_path)

    def load(self, registration_number: str) -> Optional[Dict[str, Any]]:
        """
        Loads session if it exists and has not exceeded the timeout duration.
        Returns the session dict for VtopClient.restore, or None if expired/missing.
        """
        try:
            file_path = self._get_user_file(registration_number)
        except ValueError:
            return None

        if not file_path.exists():
            return None

        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            saved_time = data.get("timestamp", 0)
            elapsed = time.time() - saved_time

            if elapsed > self.timeout_seconds:
                logger.info(
                    "Session for %s expired (%.1f min > %.1f min limit).",
                    registration_number,
                    elapsed / 60,
                    self.timeout_seconds / 60,
                )
                self.delete(registration_number)
                return None

            return data.get("session")
        except Exception as e:
            logger.warning("Failed to read session file for %s: %s", registration_number, e)
            self.delete(registration_number)
            return None

    def delete(self, registration_number: str) -> None:
        """Removes the stored session file."""
        try:
            file_path = self._get_user_file(registration_number)
        except ValueError:
            return

        if file_path.exists():
            try:
                file_path.unlink()
                logger.info("Deleted expired or invalid session for %s", registration_number)
            except OSError as e:
                logger.error("Error deleting session file %s: %s", file_path, e)

    def is_active(self, registration_number: str) -> bool:
        """Returns True if a valid unexpired session is present for this student."""
        return self.load(registration_number) is not None

    def get_remaining_ttl(self, registration_number: str) -> Optional[float]:
        """Returns the number of seconds remaining before the session expires, or None."""
        try:
            file_path = self._get_user_file(registration_number)
        except ValueError:
            return None

        if not file_path.exists():
            return None

        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            saved_time = data.get("timestamp", 0)
            remaining = self.timeout_seconds - (time.time() - saved_time)
            return max(0.0, remaining) if remaining > 0 else None
        except Exception:
            return None

    def list_active_sessions(self) -> List[str]:
        """Returns a list of student registration numbers currently possessing active sessions."""
        if not self.storage_dir.exists():
            return []

        active = []
        for file in self.storage_dir.glob("*.json"):
            reg_no = file.stem.upper()
            if self.load(reg_no) is not None:
                active.append(reg_no)
        return active

    def clear_all(self) -> int:
        """Deletes all session files in storage_dir and returns the number of files cleared."""
        if not self.storage_dir.exists():
            return 0

        count = 0
        for file in self.storage_dir.glob("*.json"):
            try:
                file.unlink()
                count += 1
            except OSError:
                pass
        return count


# Global default session store instance
session_store = VtopSessionStore()


async def authenticate_user(
    registration_number: str,
    password: str,
    store: Optional[VtopSessionStore] = None,
) -> VtopClient:
    """
    Performs a fresh login with credentials, caches the session on success, and returns client.
    Closes the client connection on failure.
    """
    if not registration_number or not str(registration_number).strip():
        raise ValueError("Registration number is required to authenticate.")
    if not password or not str(password).strip():
        raise ValueError("Password is required to authenticate.")

    reg_no = str(registration_number).strip().upper()
    target_store = store or session_store
    client = VtopClient(registration_number=reg_no, password=password)
    try:
        await client.login()
        if client.session:
            target_store.save(reg_no, client.session)
        return client
    except Exception:
        await client.close()
        raise


async def get_authenticated_client(
    registration_number: str,
    password: Optional[str] = None,
    session_data: Optional[Dict[str, Any]] = None,
    store: Optional[VtopSessionStore] = None,
) -> VtopClient:
    """
    Provides an authenticated VtopClient.

    1. If `session_data` is explicitly provided, restores directly from it.
    2. Otherwise, checks local session storage for a valid (<20m) session.
    3. If session is missing or expired, logs in with `password`.
    """
    target_store = store or session_store
    if not registration_number or not str(registration_number).strip():
        raise ValueError("Registration number is required to authenticate or restore a VTOP session.")
    reg_no = str(registration_number).strip().upper()

    # 1. Direct session injection (e.g. from API header / token)
    if session_data:
        return VtopClient.restore(
            registration_number=session_data["registration_number"],
            cookie=session_data["cookie"],
            csrf_token=session_data["csrf_token"],
            user_agent=session_data.get("user_agent"),
        )

    # 2. Check cached session on disk
    cached = target_store.load(reg_no)
    if cached:
        logger.info("Reusing active session for %s", reg_no)
        return VtopClient.restore(
            registration_number=cached["registration_number"],
            cookie=cached["cookie"],
            csrf_token=cached["csrf_token"],
            user_agent=cached.get("user_agent"),
        )

    # 3. Fresh login
    if not password:
        raise VtopSessionError(
            f"No active session found for {reg_no} and no password provided for login.",
            status_code=401,
        )

    logger.info("Initiating fresh login for %s", reg_no)
    return await authenticate_user(reg_no, password, target_store)


@asynccontextmanager
async def vtop_client_session(
    registration_number: str,
    password: Optional[str] = None,
    session_data: Optional[Dict[str, Any]] = None,
    store: Optional[VtopSessionStore] = None,
) -> AsyncGenerator[VtopClient, None]:
    """
    Async Context Manager for API endpoints and tasks.
    Automatically handles session reuse, login, dead-session invalidation, and socket cleanup.
    """
    target_store = store or session_store
    client = await get_authenticated_client(
        registration_number=registration_number,
        password=password,
        session_data=session_data,
        store=target_store,
    )
    try:
        yield client
    except VtopSessionError:
        # If server rejected restored session as dead, clear cache
        target_store.delete(registration_number)
        raise
    finally:
        await client.close()
