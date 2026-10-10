from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from vtop_vitap_agent.voice_input import VoiceInputResult, VoiceInputService
from vtop_vitap_agent.voice_output import VoiceOutputResult, VoiceOutputService
from vtop_vitap_agent.workflow import (
    CallSessionResult,
    VtopVoiceWorkflow,
    handle_dummy_phone_call,
)


def test_workflow_init_defaults():
    wf = VtopVoiceWorkflow()
    assert wf.default_phone_number == "+91-90000-00001"
    assert isinstance(wf.input_service, VoiceInputService)
    assert isinstance(wf.output_service, VoiceOutputService)


def test_workflow_simulate_text_call():
    wf = VtopVoiceWorkflow()

    mock_out = VoiceOutputResult(
        text="Aapka timetable 9 baje shuru hota hai.",
        language="hindi",
        voice_name="hf_alpha",
        audio_data=np.zeros(24000, dtype=np.float32),
        sample_rate=24000,
        duration_seconds=1.0,
        audio_bytes=b"wav_bytes_hindi",
        success=True,
    )

    with patch.object(wf.input_service, "transfer_to_llm", return_value="Aapka timetable 9 baje shuru hota hai."):
        with patch.object(wf.output_service, "handle_dummy_call_response", return_value=mock_out):
            result = wf.simulate_text_call(
                user_text="Mera timetable kya hai?",
                preferred_language="hindi",
                dummy_phone_number="+91-90000-00001",
                caller_id="STUDENT_HINDI",
            )

            assert isinstance(result, CallSessionResult)
            assert result.success is True
            assert result.preferred_language == "hindi"
            assert result.dummy_phone_number == "+91-90000-00001"
            assert result.caller_id == "STUDENT_HINDI"
            assert result.llm_response == "Aapka timetable 9 baje shuru hota hai."
            assert result.voice_output.duration_seconds == 1.0


def test_workflow_handle_call_audio():
    wf = VtopVoiceWorkflow()
    dummy_audio = np.zeros(16000, dtype=np.float32)

    mock_input_res = VoiceInputResult(
        query_text="What are my marks?",
        detected_language="en",
        preferred_language="english",
        llm_response="You scored 45 out of 50 in CAT 1.",
        duration_seconds=0.4,
        success=True,
    )

    mock_output_res = VoiceOutputResult(
        text="You scored 45 out of 50 in CAT 1.",
        language="english",
        voice_name="af_heart",
        audio_data=np.zeros(24000, dtype=np.float32),
        sample_rate=24000,
        duration_seconds=1.5,
        audio_bytes=b"wav_bytes_eng",
        success=True,
    )

    with patch.object(wf.input_service, "handle_dummy_call", return_value=mock_input_res):
        with patch.object(wf.output_service, "handle_dummy_call_response", return_value=mock_output_res):
            result = wf.handle_call(
                audio_input=dummy_audio,
                preferred_language="english",
                dummy_phone_number="+91-90000-00001",
                caller_id="STUDENT_MARKS",
            )

            assert result.success is True
            assert result.user_query == "What are my marks?"
            assert result.llm_response == "You scored 45 out of 50 in CAT 1."
            assert result.voice_output.voice_name == "af_heart"
            assert result.total_duration_seconds > 0


def test_handle_dummy_phone_call_convenience():
    with patch("vtop_vitap_agent.workflow.VtopVoiceWorkflow.simulate_text_call") as mock_sim:
        mock_sim.return_value = MagicMock(success=True)
        res = handle_dummy_phone_call(
            audio_or_text="Exam dates?",
            language="english",
            dummy_number="+91-90000-00001",
            caller_id="STUDENT_EXAM",
        )
        assert res.success is True
        mock_sim.assert_called_once()
