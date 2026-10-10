import json
import logging
import os
import shutil
import sys
import time as time_mod
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

from dotenv import load_dotenv

# Ensure workspace root is in sys.path for direct script execution and MCP dev
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Load .env variables from project root
load_dotenv(PROJECT_ROOT / ".env")
load_dotenv()

from mcp.server.fastmcp import FastMCP
from vitap_vtop_client.academic_calendar.model.calendar_model import AcademicCalendarModel
from vitap_vtop_client.client import VtopClient
from vitap_vtop_client.timetable.model.timetable_model import Course, TimetableModel

from vtop_mcp_tool.model.vtop_tool_model import DayScheduleSummary, FreeSlot
from vtop_vitap_login import (
    VtopSessionError,
    authenticate_user,
    get_authenticated_client,
    session_store,
)

logger = logging.getLogger("vtop_mcp")
mcp = FastMCP("VTOP-Assistant-MCP")


def _sync_sessions_and_get_active_username() -> Optional[str]:
    """
    Checks session storage directories (including .vtop_sessions and cli-bot/.vtop_sessions).
    Syncs sessions from cli-bot/.vtop_sessions to session_store.storage_dir if needed.
    Returns the registration number of the most recently active session, if any.
    """
    primary_dir = session_store.storage_dir
    primary_dir.mkdir(parents=True, exist_ok=True)

    cli_bot_dir = PROJECT_ROOT / "cli-bot" / ".vtop_sessions"
    if cli_bot_dir.exists():
        for f in cli_bot_dir.glob("*.json"):
            dest = primary_dir / f.name
            try:
                if not dest.exists() or f.stat().st_mtime > dest.stat().st_mtime:
                    shutil.copy2(f, dest)
            except Exception:
                pass

    session_files = list(primary_dir.glob("*.json"))
    if not session_files:
        return None

    active_sessions: List[Tuple[float, str]] = []
    cached_sessions: List[Tuple[float, str]] = []

    for sf in session_files:
        reg_no = sf.stem.upper()
        try:
            data = json.loads(sf.read_text(encoding="utf-8"))
            saved_time = data.get("timestamp", sf.stat().st_mtime)
            cached_sessions.append((saved_time, reg_no))

            elapsed = time_mod.time() - saved_time
            if elapsed <= session_store.timeout_seconds and "session" in data:
                active_sessions.append((saved_time, reg_no))
        except Exception:
            continue

    if active_sessions:
        active_sessions.sort(key=lambda x: x[0], reverse=True)
        return active_sessions[0][1]

    if cached_sessions:
        cached_sessions.sort(key=lambda x: x[0], reverse=True)
        return cached_sessions[0][1]

    return None


def _load_credentials() -> tuple[Optional[str], Optional[str]]:
    """Loads VTOP username and password from active sessions, env, .env, or credentials files."""
    # 1. Environment variables
    u = (
        os.getenv("VTOP_USERNAME")
        or os.getenv("VTOP_REGISTRATION_NUMBER")
        or os.getenv("VTOP_USER")
    )
    p = os.getenv("VTOP_PASSWORD") or os.getenv("VTOP_PWD")

    if u:
        u = u.strip().strip("'\"")
    if p:
        p = p.strip().strip("'\"")

    # 2. Check credentials files if username or password is missing
    cred_paths = [
        PROJECT_ROOT / ".vtop_credentials.json",
        PROJECT_ROOT / "cli-bot" / ".vtop_credentials.json",
    ]
    for cp in cred_paths:
        if cp.exists():
            try:
                data = json.loads(cp.read_text(encoding="utf-8"))
                if not u:
                    raw_u = (
                        data.get("username")
                        or data.get("registration_number")
                        or data.get("reg_no")
                    )
                    if raw_u:
                        u = str(raw_u).strip().strip("'\"")
                if not p:
                    raw_p = data.get("password") or data.get("pwd")
                    if raw_p:
                        p = str(raw_p).strip().strip("'\"")
            except Exception:
                pass

    # 3. Check for active/cached sessions from cli-bot or session_store
    active_user = _sync_sessions_and_get_active_username()
    if active_user:
        # If no username found yet, or if current username has no valid active session
        # but active_user does, prioritize the active session from cli-bot
        if not u:
            u = active_user
        else:
            current_is_active = session_store.load(u) is not None
            if not current_is_active and session_store.load(active_user) is not None:
                u = active_user

    # 4. Check legacy .vtop_session.json in root if u still not found
    if not u:
        legacy_session = PROJECT_ROOT / ".vtop_session.json"
        if legacy_session.exists():
            try:
                data = json.loads(legacy_session.read_text(encoding="utf-8"))
                if data.get("registration_number"):
                    u = str(data["registration_number"]).strip().strip("'\"")
            except Exception:
                pass

    return (u.strip() if u else None, p.strip() if p else None)


def _parse_date(date_str: str) -> datetime:
    """Parses various date formats like '23/09/26', '23/09/2026', '2026-09-23', 'today'."""
    raw = date_str.strip().lower()
    if raw == "today":
        return datetime.today()
    if raw == "tomorrow":
        return datetime.today() + timedelta(days=1)

    for fmt in (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d/%m/%y",
        "%d-%m-%Y",
        "%d-%m-%y",
        "%d %b %Y",
        "%d %B %Y",
    ):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            pass
    raise ValueError(f"Could not parse date: {date_str}. Please use format YYYY-MM-DD or DD/MM/YYYY.")


def _parse_time_str(t_str: str) -> time:
    """Parses times like '09:00 AM', '14:30', or '02:00 PM'."""
    t_str = t_str.strip()
    for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(t_str, fmt).time()
        except ValueError:
            pass
    raise ValueError(f"Unknown time format: {t_str}")


def calculate_free_slots_for_day(
    day_courses: List[Course],
    day_start_str: str = "08:00 AM",
    day_end_str: str = "07:30 PM",
) -> List[FreeSlot]:
    """Calculates gaps between scheduled classes."""
    day_start = _parse_time_str(day_start_str)
    day_end = _parse_time_str(day_end_str)

    busy_intervals = []
    for c in day_courses:
        if "-" in c.time:
            parts = c.time.split("-")
            try:
                start = _parse_time_str(parts[0])
                end = _parse_time_str(parts[1])
                busy_intervals.append((start, end))
            except Exception:
                continue

    busy_intervals.sort(key=lambda x: x[0])

    free_slots: List[FreeSlot] = []
    current_time = day_start

    for b_start, b_end in busy_intervals:
        if b_start > current_time:
            duration = int(
                (
                    datetime.combine(datetime.today(), b_start)
                    - datetime.combine(datetime.today(), current_time)
                ).total_seconds()
                / 60
            )
            if duration >= 15:
                slot_type = (
                    "Lunch Window"
                    if current_time <= time(13, 0) and b_start >= time(13, 0)
                    else "Break"
                )
                free_slots.append(
                    FreeSlot(
                        start_time=current_time.strftime("%I:%M %p"),
                        end_time=b_start.strftime("%I:%M %p"),
                        duration_minutes=duration,
                        slot_type=slot_type,
                    )
                )
        current_time = max(current_time, b_end)

    if current_time < day_end:
        duration = int(
            (
                datetime.combine(datetime.today(), day_end)
                - datetime.combine(datetime.today(), current_time)
            ).total_seconds()
            / 60
        )
        if duration >= 15:
            free_slots.append(
                FreeSlot(
                    start_time=current_time.strftime("%I:%M %p"),
                    end_time=day_end.strftime("%I:%M %p"),
                    duration_minutes=duration,
                    slot_type="Post-Class / Evening Free",
                )
            )

    return free_slots


async def _get_authenticated_client(
    username: Optional[str] = None, password: Optional[str] = None
) -> VtopClient:
    u = username
    p = password
    if not u or not p:
        env_u, env_p = _load_credentials()
        u = u or env_u
        p = p or env_p

    if not u:
        raise ValueError(
            "VTOP username not found. Please log in first via cli-bot (/v1/login), "
            "call the login_vtop tool, or set VTOP_USERNAME & VTOP_PASSWORD in .env or .vtop_credentials.json"
        )

    try:
        return await get_authenticated_client(registration_number=u, password=p)
    except VtopSessionError as e:
        if p:
            session_store.delete(u)
            return await authenticate_user(registration_number=u, password=p)
        raise VtopSessionError(
            f"Active VTOP session expired for {u} and no password was provided. "
            "Please log in again via cli-bot (/v1/login), call login_vtop, or set VTOP_PASSWORD in .env / .vtop_credentials.json.",
            status_code=401,
        ) from e


async def _resolve_semester_id(client: VtopClient, semester_id: Optional[str] = None) -> str:
    """Uses provided semester_id or automatically fetches the active semester from VTOP."""
    if semester_id and semester_id.strip():
        return semester_id.strip()
    sem_data = await client.get_semesters()
    if not sem_data.semesters:
        raise ValueError("No semesters found for this account.")
    return sem_data.semesters[0].id


@mcp.tool()
async def login_vtop(
    registration_number: str,
    password: str,
) -> dict:
    """Authenticates student credentials against VTOP and caches the session for subsequent tool calls."""
    clean_id = registration_number.strip().upper()
    client = await authenticate_user(registration_number=clean_id, password=password)
    try:
        return {
            "status": "success",
            "message": f"Successfully authenticated session for {clean_id}",
            "registration_number": clean_id,
        }
    finally:
        await client.close()


@mcp.tool()
async def get_session_status() -> dict:
    """Checks whether an active, valid VTOP session is currently loaded and returns session status."""
    u, p = _load_credentials()
    if not u:
        return {
            "has_active_session": False,
            "message": "No username or active session found. Please log in via cli-bot or login_vtop.",
        }
    cached = session_store.load(u)
    if cached:
        return {
            "has_active_session": True,
            "registration_number": u,
            "message": f"Active session found for {u}",
        }
    return {
        "has_active_session": False,
        "registration_number": u,
        "message": f"Session for {u} is missing or expired. Password available: {bool(p)}",
    }


@mcp.tool()
async def get_student_timetable(
    semester_id: Optional[str] = None,
) -> dict:
    """Fetches the full weekly timetable (Monday - Sunday) for a semester.
    If semester_id is omitted, automatically uses the active semester.
    """
    client = await _get_authenticated_client()
    try:
        sem_id = await _resolve_semester_id(client, semester_id)
        res = await client.get_timetable(sem_sub_id=sem_id)
        return res.model_dump()
    finally:
        await client.close()


@mcp.tool()
async def get_free_slots(
    weekday: str,
    semester_id: Optional[str] = None,
) -> list:
    """Calculates all free time slots and breaks between scheduled classes for a specific day (e.g. 'Monday', 'Tuesday', etc.)."""
    client = await _get_authenticated_client()
    try:
        sem_id = await _resolve_semester_id(client, semester_id)
        timetable = await client.get_timetable(sem_sub_id=sem_id)
        day_key = weekday.strip().capitalize()
        courses = getattr(timetable, day_key, [])
        free_slots = calculate_free_slots_for_day(courses)
        return [slot.model_dump() for slot in free_slots]
    finally:
        await client.close()


@mcp.tool()
async def get_attendance_report(
    semester_id: Optional[str] = None,
) -> list:
    """Fetches current attendance percentages, attended/total classes, and margin.
    If semester_id is omitted, automatically uses the active semester.
    """
    client = await _get_authenticated_client()
    try:
        sem_id = await _resolve_semester_id(client, semester_id)
        res = await client.get_attendance(sem_sub_id=sem_id)
        return [item.model_dump() for item in res]
    finally:
        await client.close()


@mcp.tool()
async def get_exam_schedule(
    semester_id: Optional[str] = None,
) -> dict:
    """Fetches midterm/final exam schedule with venues and timings.
    If semester_id is omitted, automatically uses the active semester.
    """
    client = await _get_authenticated_client()
    try:
        sem_id = await _resolve_semester_id(client, semester_id)
        res = await client.get_exam_schedule(sem_sub_id=sem_id)
        return res.model_dump()
    finally:
        await client.close()


@mcp.tool()
async def get_available_semesters() -> list:
    """Fetches all valid semester IDs and semester names available for this student account."""
    client = await _get_authenticated_client()
    try:
        sem_data = await client.get_semesters()
        return [sem.model_dump() for sem in sem_data.semesters]
    finally:
        await client.close()


@mcp.tool()
async def get_student_profile() -> dict:
    """Fetches the student profile information, CGPA, branch, and program details."""
    client = await _get_authenticated_client()
    try:
        res = await client.get_profile()
        return res.model_dump()
    finally:
        await client.close()


@mcp.tool()
async def get_student_marks(
    semester_id: Optional[str] = None,
) -> dict:
    """Fetches marks and assessment scores for courses in a semester.
    If semester_id is omitted, automatically uses the active semester.
    """
    client = await _get_authenticated_client()
    try:
        sem_id = await _resolve_semester_id(client, semester_id)
        res = await client.get_marks(sem_sub_id=sem_id)
        return res.model_dump()
    finally:
        await client.close()


@mcp.tool()
async def get_grade_history() -> dict:
    """Fetches the student's complete grade history, credits earned, and GPA across semesters."""
    client = await _get_authenticated_client()
    try:
        res = await client.get_grade_history()
        return res.model_dump()
    finally:
        await client.close()


@mcp.tool()
async def get_digital_assignments(
    semester_id: Optional[str] = None,
) -> list:
    """Fetches digital assignments and submission statuses for courses in a semester.
    If semester_id is omitted, automatically uses the active semester.
    """
    client = await _get_authenticated_client()
    try:
        sem_id = await _resolve_semester_id(client, semester_id)
        res = await client.get_digital_assignments(sem_sub_id=sem_id)
        return [item.model_dump() for item in res]
    finally:
        await client.close()


@mcp.tool()
async def get_academic_calendar(
    semester_id: Optional[str] = None,
) -> dict:
    """Fetches the academic calendar events, holidays, and instructional days for a semester.
    If semester_id is omitted, automatically uses the active semester.
    """
    client = await _get_authenticated_client()
    try:
        sem_id = await _resolve_semester_id(client, semester_id)
        res = await client.get_academic_calendar(sem_sub_id=sem_id)
        return res.model_dump()
    finally:
        await client.close()


if __name__ == "__main__":
    mcp.run()
