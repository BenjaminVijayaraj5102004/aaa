import logging
from typing import Optional
from pydantic import BaseModel, Field
from fastapi import FastAPI, HTTPException
from vtop_vitap_login import (
    get_authenticated_client,
    session_store,
    VtopLoginError,
    VtopSessionError,
)

logger = logging.getLogger("cli_bot")
app = FastAPI(title="VTOP CLI Bot Service", version="0.1.0")


class UserInfo(BaseModel):
    UserName: Optional[str] = Field(
        default=None,
        description="VTOP Student Registration Number (e.g. 23BEC7152)",
    )
    username: Optional[str] = Field(
        default=None,
        description="Alternative alias for registration number",
    )
    password: Optional[str] = Field(
        default=None,
        description="VTOP Password",
    )

    def get_registration_number(self) -> Optional[str]:
        return self.UserName or self.username


@app.get("/")
@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "service": "vtop-cli-bot",
        "session_dir": str(session_store.storage_dir),
    }


@app.get("/v1/status")
async def get_session_status():
    """Returns active session status across the storage directory."""
    active_users = session_store.list_active_sessions()
    files = list(session_store.storage_dir.glob("*.json"))

    return {
        "has_active_session": len(active_users) > 0,
        "active_users": active_users,
        "total_cached_sessions": len(files),
    }


@app.get("/v1/session/{registration_number}")
async def check_user_session(registration_number: str):
    """Checks whether an active session exists for a specific student."""
    reg_no = registration_number.strip().upper()
    cached = session_store.load(reg_no)
    if cached:
        return {
            "status": "active",
            "registration_number": reg_no,
            "message": f"Active session found for {reg_no}",
        }
    return {
        "status": "inactive",
        "registration_number": reg_no,
        "message": f"No active session for {reg_no}. Please log in with password.",
    }


@app.post("/v1/login")
async def get_or_create_session(user: UserInfo):
    reg_no = user.get_registration_number()
    if not reg_no or not reg_no.strip():
        raise HTTPException(
            status_code=400,
            detail="UserName (registration number) is required.",
        )

    try:
        client = await get_authenticated_client(
            registration_number=reg_no,
            password=user.password,
        )
        try:
            return {
                "status": "success",
                "registration_number": reg_no.strip().upper(),
                "session": client.session.model_dump() if client.session else None,
                "is_restored": client.password is None,
            }
        finally:
            await client.close()

    except VtopSessionError as e:
        raise HTTPException(
            status_code=401,
            detail=str(e) or "Session expired or missing. Please provide password to re-authenticate.",
        )
    except VtopLoginError as e:
        raise HTTPException(
            status_code=401,
            detail=f"VTOP login failed: {e}",
        )
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e),
        )
    except Exception as e:
        logger.exception("Unexpected error in /v1/login")
        raise HTTPException(
            status_code=500,
            detail=f"An unexpected error occurred during login: {e}",
        )
