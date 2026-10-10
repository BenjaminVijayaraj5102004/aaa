import asyncio
from datetime import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vitap_vtop_client.timetable.model.timetable_model import Course
from vtop_mcp_tool import vtop_mcp
from vtop_mcp_tool.vtop_mcp import (
    _load_credentials,
    calculate_free_slots_for_day,
    get_academic_calendar,
    get_attendance_report,
    get_available_semesters,
    get_digital_assignments,
    get_exam_schedule,
    get_free_slots,
    get_grade_history,
    get_session_status,
    get_student_marks,
    get_student_profile,
    get_student_timetable,
)


class TestVtopMcpToolHelpers:
    def test_calculate_free_slots_empty_day(self):
        # Empty courses should return a full day free slot
        slots = calculate_free_slots_for_day([])
        assert len(slots) == 1
        assert slots[0].start_time == "08:00 AM"
        assert slots[0].end_time == "07:30 PM"
        assert slots[0].duration_minutes == 690

    def test_calculate_free_slots_with_classes(self):
        # Create mock course from 09:00 AM to 10:00 AM
        course = MagicMock()
        course.time = "09:00 AM - 10:00 AM"
        course.course_name = "Data Structures"

        slots = calculate_free_slots_for_day([course])
        assert len(slots) >= 1
        # Slot before 9:00 AM (08:00 AM to 09:00 AM)
        assert slots[0].start_time == "08:00 AM"
        assert slots[0].end_time == "09:00 AM"
        assert slots[0].duration_minutes == 60

    def test_load_credentials_from_env(self, monkeypatch):
        monkeypatch.setenv("VTOP_USERNAME", "23BCE7152")
        monkeypatch.setenv("VTOP_PASSWORD", "SuperPass")
        with patch.object(vtop_mcp, "_sync_sessions_and_get_active_username", return_value=None):
            u, p = _load_credentials()
            assert u == "23BCE7152"
            assert p == "SuperPass"


class TestVtopMcpEndpoints:
    def test_get_session_status(self):
        async def _test():
            with patch.object(vtop_mcp, "_load_credentials", return_value=("23BCE7152", "pass")):
                with patch.object(vtop_mcp.session_store, "load", return_value={"cookie": "c", "csrf_token": "t"}):
                    res = await get_session_status()
                    assert res["has_active_session"] is True
                    assert res["registration_number"] == "23BCE7152"

        asyncio.run(_test())


    def test_get_student_timetable(self):
        async def _test():
            mock_client = MagicMock()
            mock_client.close = AsyncMock()
            mock_timetable = MagicMock()
            mock_timetable.model_dump.return_value = {"Monday": []}
            mock_client.get_timetable = AsyncMock(return_value=mock_timetable)

            with patch.object(vtop_mcp, "_get_authenticated_client", AsyncMock(return_value=mock_client)):
                with patch.object(vtop_mcp, "_resolve_semester_id", AsyncMock(return_value="AP2026272")):
                    res = await get_student_timetable()
                    assert "Monday" in res
                    mock_client.close.assert_awaited_once()

        asyncio.run(_test())

    def test_get_attendance_report(self):
        async def _test():
            mock_client = MagicMock()
            mock_client.close = AsyncMock()
            mock_item = MagicMock()
            mock_item.model_dump.return_value = {"course_code": "CSE1001", "percentage": 95}
            mock_client.get_attendance = AsyncMock(return_value=[mock_item])

            with patch.object(vtop_mcp, "_get_authenticated_client", AsyncMock(return_value=mock_client)):
                with patch.object(vtop_mcp, "_resolve_semester_id", AsyncMock(return_value="AP2026272")):
                    res = await get_attendance_report()
                    assert len(res) == 1
                    assert res[0]["percentage"] == 95
                    mock_client.close.assert_awaited_once()

        asyncio.run(_test())

    def test_get_student_profile(self):
        async def _test():
            mock_client = MagicMock()
            mock_client.close = AsyncMock()
            mock_profile = MagicMock()
            mock_profile.model_dump.return_value = {"name": "Test Student", "cgpa": 9.1}
            mock_client.get_profile = AsyncMock(return_value=mock_profile)

            with patch.object(vtop_mcp, "_get_authenticated_client", AsyncMock(return_value=mock_client)):
                res = await get_student_profile()
                assert res["cgpa"] == 9.1
                mock_client.close.assert_awaited_once()

        asyncio.run(_test())

    def test_get_available_semesters(self):
        async def _test():
            mock_client = MagicMock()
            mock_client.close = AsyncMock()
            mock_sem = MagicMock()
            mock_sem.model_dump.return_value = {"semester_id": "AP2026272", "name": "Winter 2026"}
            mock_sem_data = MagicMock()
            mock_sem_data.semesters = [mock_sem]
            mock_client.get_semesters = AsyncMock(return_value=mock_sem_data)

            with patch.object(vtop_mcp, "_get_authenticated_client", AsyncMock(return_value=mock_client)):
                res = await get_available_semesters()
                assert len(res) == 1
                assert res[0]["semester_id"] == "AP2026272"
                mock_client.close.assert_awaited_once()

        asyncio.run(_test())
