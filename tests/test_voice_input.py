from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from vtop_vitap_agent.voice_input import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_RECORD_SECONDS,
    DEFAULT_SAMPLE_RATE,
    SUPPORTED_LANGUAGES,
    VoiceInputResult,
    VoiceInputService,
)


def test_language_code_resolution():
    assert VoiceInputService.resolve_language_code("english") == "en"
    assert VoiceInputService.resolve_language_code("EN") == "en"
    assert VoiceInputService.resolve_language_code("hindi") == "hi"
    assert VoiceInputService.resolve_language_code("telugu") == "te"
    assert VoiceInputService.resolve_language_code("tamil") == "ta"
    assert VoiceInputService.resolve_language_code(None) is None
    assert VoiceInputService.resolve_language_code("fr") == "fr"


def test_load_audio_bytes_pcm():
    service = VoiceInputService()
    # 1 second of 16kHz 16-bit PCM zeroes
    raw_bytes = bytes(16000 * 2)
    audio_np = service.load_audio_bytes(raw_bytes)
    assert isinstance(audio_np, np.ndarray)
    assert audio_np.dtype == np.float32
    assert len(audio_np) == 16000
    assert np.all(audio_np == 0.0)


def test_load_audio_file_nonexistent():
    service = VoiceInputService()
    with pytest.raises(FileNotFoundError):
        service.load_audio_file("non_existent_file.wav")


def test_transcribe_mock():
    service = VoiceInputService()
    mock_whisper = MagicMock()
    mock_whisper.transcribe.return_value = {
        "text": "What is my timetable today?",
        "language": "en",
        "segments": [{"id": 0, "text": "What is my timetable today?"}],
    }

    dummy_audio = np.zeros(16000, dtype=np.float32)

    with patch.object(service, "_whisper_model", mock_whisper):
        res = service.transcribe(dummy_audio, preferred_language="english")
        assert res["text"] == "What is my timetable today?"
        assert res["language"] == "en"
        assert len(res["segments"]) == 1
        mock_whisper.transcribe.assert_called_once()
        _, kwargs = mock_whisper.transcribe.call_args
        assert kwargs["language"] == "en"
        assert kwargs["fp16"] is False


def test_transfer_to_llm_empty():
    service = VoiceInputService()
    reply = service.transfer_to_llm("")
    assert "No voice input detected" in reply


def test_transfer_to_llm_mcp_delegation():
    service = VoiceInputService(use_mcp=True)
    with patch(
        "vtop_vitap_agent.llm.query_vtop_agent_sync",
        return_value="Your attendance is 88 percent.",
    ) as mock_agent:
        reply = service.transfer_to_llm("Attendance status?", user_language="english")
        assert reply == "Your attendance is 88 percent."
        mock_agent.assert_called_once_with("Attendance status?", language="english")


def test_transfer_to_llm_fallback_chat_ollama():
    service = VoiceInputService(use_mcp=False)
    mock_llm = MagicMock()
    mock_reply = MagicMock()
    mock_reply.content = "Direct Ollama answer"
    mock_llm.invoke.return_value = mock_reply

    with patch.object(service, "_llm", mock_llm):
        reply = service.transfer_to_llm("Tell me my schedule", user_language="hindi")
        assert reply == "Direct Ollama answer"
        assert mock_llm.invoke.called


def test_process_voice_pipeline():
    service = VoiceInputService()
    dummy_audio = np.zeros(16000, dtype=np.float32)

    with patch.object(
        service,
        "transcribe",
        return_value={"text": "When is my exam?", "language": "en", "segments": []},
    ):
        with patch.object(service, "transfer_to_llm", return_value="Your exam starts on Monday."):
            cb_mock = MagicMock()
            result = service.process_voice_pipeline(
                audio_input=dummy_audio,
                preferred_language="english",
                on_llm_response=cb_mock,
            )

            assert result.success
            assert result.query_text == "When is my exam?"
            assert result.detected_language == "en"
            assert result.llm_response == "Your exam starts on Monday."
            assert cb_mock.called
            cb_mock.assert_called_once_with("Your exam starts on Monday.")


def test_handle_dummy_call():
    service = VoiceInputService()
    dummy_audio = np.zeros(16000, dtype=np.float32)

    with patch.object(service, "process_voice_pipeline") as mock_pipeline:
        mock_res = VoiceInputResult(
            query_text="Hi",
            detected_language="en",
            preferred_language="english",
            llm_response="Hello",
            duration_seconds=0.5,
            success=True,
        )
        mock_pipeline.return_value = mock_res

        res = service.handle_dummy_call(
            dummy_phone_number="+91-90000-00001",
            audio_input=dummy_audio,
            preferred_language="english",
            caller_id="STUDENT_TEST",
        )

        assert res.success
        assert mock_pipeline.called
        kwargs = mock_pipeline.call_args.kwargs
        assert kwargs["metadata"]["dummy_number"] == "+91-90000-00001"
        assert kwargs["metadata"]["caller_id"] == "STUDENT_TEST"
        assert kwargs["metadata"]["channel"] == "telephony_dummy_service"
