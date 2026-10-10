import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vtop_vitap_agent.llm import (
    DEFAULT_MODEL,
    MULTILINGUAL_VOICE_SYSTEM_PROMPT,
    VtopVoiceAgent,
    query_vtop_agent,
    query_vtop_agent_sync,
)


def test_system_prompt_rules():
    assert "VTOP" in MULTILINGUAL_VOICE_SYSTEM_PROMPT
    assert "Kokoro" in MULTILINGUAL_VOICE_SYSTEM_PROMPT
    assert "English" in MULTILINGUAL_VOICE_SYSTEM_PROMPT
    assert "Hindi" in MULTILINGUAL_VOICE_SYSTEM_PROMPT
    assert "Telugu" in MULTILINGUAL_VOICE_SYSTEM_PROMPT
    assert "Tamil" in MULTILINGUAL_VOICE_SYSTEM_PROMPT
    assert "get_student_timetable" in MULTILINGUAL_VOICE_SYSTEM_PROMPT
    assert "get_attendance_report" in MULTILINGUAL_VOICE_SYSTEM_PROMPT


def test_agent_init_defaults():
    agent = VtopVoiceAgent()
    assert agent.model_name == DEFAULT_MODEL
    assert agent.temperature == 0.1
    assert agent.agent is None
    assert agent.tools == []


@pytest.mark.asyncio
async def test_agent_initialize_mock():
    agent = VtopVoiceAgent()

    mock_client = MagicMock()
    mock_tool = MagicMock()
    mock_tool.name = "get_student_timetable"
    mock_client.get_tools = AsyncMock(return_value=[mock_tool])

    mock_compiled_agent = MagicMock()

    with patch("vtop_vitap_agent.llm.MultiServerMCPClient", return_value=mock_client):
        with patch("vtop_vitap_agent.llm.ChatOllama") as mock_ollama_cls:
            with patch("vtop_vitap_agent.llm.create_agent", return_value=mock_compiled_agent):
                await agent.initialize()

                assert len(agent.tools) == 1
                assert agent.tools[0].name == "get_student_timetable"
                assert agent.agent == mock_compiled_agent
                mock_ollama_cls.assert_called_once()


@pytest.mark.asyncio
async def test_agent_ask_mock():
    agent = VtopVoiceAgent()

    mock_compiled_agent = MagicMock()
    mock_response_msg = MagicMock()
    mock_response_msg.content = "Your attendance is 89 percent."
    mock_compiled_agent.ainvoke = AsyncMock(return_value={"messages": [mock_response_msg]})

    agent.agent = mock_compiled_agent

    reply = await agent.ask("Check attendance", preferred_language="english")
    assert reply == "Your attendance is 89 percent."

    mock_compiled_agent.ainvoke.assert_called_once()
    call_args = mock_compiled_agent.ainvoke.call_args[0][0]
    messages = call_args["messages"]
    assert len(messages) == 1
    assert "User preferred language: english" in messages[0]["content"]


@pytest.mark.asyncio
async def test_agent_context_manager():
    agent = VtopVoiceAgent()
    with patch.object(agent, "initialize", new_callable=AsyncMock) as mock_init:
        with patch.object(agent, "close", new_callable=AsyncMock) as mock_close:
            async with agent as a:
                assert a == agent
                mock_init.assert_called_once()
            mock_close.assert_called_once()


def test_query_vtop_agent_sync_mock():
    with patch("vtop_vitap_agent.llm.query_vtop_agent", new_callable=AsyncMock) as mock_q:
        mock_q.return_value = "Attendance is fine."
        reply = query_vtop_agent_sync("How is my attendance?", language="hindi")
        assert reply == "Attendance is fine."
