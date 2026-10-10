import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from vtop_vitap_agent.voice_output import (
    DEFAULT_SAMPLE_RATE,
    LANGUAGE_KOKORO_MAP,
    SUPPORTED_LANGUAGES,
    VoiceOutputResult,
    VoiceOutputService,
    fetch_from_llm_module,
    send_voice_output,
)


def test_supported_languages_mapping():
    assert VoiceOutputService.resolve_language("english") == "en"
    assert VoiceOutputService.resolve_language("EN") == "en"
    assert VoiceOutputService.resolve_language("hindi") == "hi"
    assert VoiceOutputService.resolve_language("HINDHI") == "hi"
    assert VoiceOutputService.resolve_language("telugu") == "te"
    assert VoiceOutputService.resolve_language("tamil") == "ta"
    assert VoiceOutputService.resolve_language(None) == "en"
    assert VoiceOutputService.resolve_language("unknown") == "en"


def test_language_kokoro_map_coverage():
    for lang in ["en", "hi", "te", "ta", "en-gb"]:
        assert lang in LANGUAGE_KOKORO_MAP
        assert "lang_code" in LANGUAGE_KOKORO_MAP[lang]
        assert "default_voice" in LANGUAGE_KOKORO_MAP[lang]


def test_synthesize_empty_string():
    service = VoiceOutputService()
    result = service.synthesize("", language="english")
    assert not result.success
    assert result.text == ""
    assert len(result.audio_data) == 0
    assert result.duration_seconds == 0.0
    assert result.audio_bytes == b""


def test_synthesize_mock_pipeline():
    service = VoiceOutputService()
    mock_pipeline = MagicMock()
    # Mock generator returning one audio chunk of shape (24000,) => 1 sec
    mock_chunk = np.ones(24000, dtype=np.float32) * 0.1
    mock_pipeline.return_value = [("g", "p", mock_chunk)]

    with patch.object(service, "get_pipeline", return_value=mock_pipeline):
        result = service.synthesize("Hello student", language="english")
        assert result.success
        assert result.text == "Hello student"
        assert result.language == "english"
        assert result.voice_name == "af_heart"
        assert len(result.audio_data) == 24000
        assert pytest.approx(result.duration_seconds, 0.01) == 1.0
        assert result.audio_bytes is not None
        assert len(result.audio_bytes) > 0


def test_synthesize_custom_voice():
    service = VoiceOutputService()
    mock_pipeline = MagicMock()
    mock_chunk = np.zeros(12000, dtype=np.float32)
    mock_pipeline.return_value = [("g", "p", mock_chunk)]

    with patch.object(service, "get_pipeline", return_value=mock_pipeline):
        result = service.synthesize("Test voice", language="english", voice="am_adam")
        assert result.voice_name == "am_adam"
        assert len(result.audio_data) == 12000


def test_play_stream_headless_fallback():
    service = VoiceOutputService()
    mock_pipeline = MagicMock()
    mock_chunk = np.zeros(12000, dtype=np.float32)
    mock_pipeline.return_value = [("g", "p", mock_chunk)]

    # Mock PyAudio to simulate headless environment where open() fails
    mock_pyaudio_inst = MagicMock()
    mock_pyaudio_inst.open.side_effect = RuntimeError("No audio output device")

    with patch("pyaudio.PyAudio", return_value=mock_pyaudio_inst):
        with patch.object(service, "get_pipeline", return_value=mock_pipeline):
            result = service.play_stream("Testing stream fallback", language="english")
            assert result.success
            assert result.metadata.get("streamed") is True
            assert len(result.audio_data) == 12000


def test_save_to_file(tmp_path: Path):
    service = VoiceOutputService()
    mock_pipeline = MagicMock()
    mock_chunk = np.zeros(24000, dtype=np.float32)
    mock_pipeline.return_value = [("g", "p", mock_chunk)]

    out_file = tmp_path / "test_output.wav"
    with patch.object(service, "get_pipeline", return_value=mock_pipeline):
        saved_path = service.save_to_file("Saved message", output_path=out_file)
        assert saved_path == out_file
        assert out_file.exists()
        assert out_file.stat().st_size > 0


def test_send_voice_message_to_user_types():
    service = VoiceOutputService()
    mock_res = VoiceOutputResult(
        text="Sample",
        language="english",
        voice_name="af_heart",
        audio_data=np.zeros(24000, dtype=np.float32),
        sample_rate=24000,
        duration_seconds=1.0,
        audio_bytes=b"dummy_wav",
        success=True,
    )

    with patch.object(service, "synthesize", return_value=mock_res):
        # 1. Plain string
        r1 = service.send_voice_message_to_user("Hello", play_audio=False)
        assert r1.success

        # 2. Dictionary input
        r2 = service.send_voice_message_to_user({"content": "Hello dict"}, play_audio=False)
        assert r2.success

        # 3. Object with .content attribute (e.g. LangChain message)
        mock_msg = MagicMock()
        mock_msg.content = "Hello langchain"
        r3 = service.send_voice_message_to_user(mock_msg, play_audio=False)
        assert r3.success


def test_handle_dummy_call_response():
    service = VoiceOutputService()
    mock_res = VoiceOutputResult(
        text="Hello student",
        language="telugu",
        voice_name="hf_alpha",
        audio_data=np.zeros(24000, dtype=np.float32),
        sample_rate=24000,
        duration_seconds=1.0,
        audio_bytes=b"wav_data",
        success=True,
    )

    with patch.object(service, "send_voice_message_to_user", return_value=mock_res) as mock_send:
        result = service.handle_dummy_call_response(
            llm_response="Namaskaram",
            dummy_phone_number="+91-90000-00001",
            caller_id="23BEC7152",
            language="telugu",
            play_audio=False,
        )
        assert result.success
        assert mock_send.called
        kwargs = mock_send.call_args.kwargs
        assert kwargs["metadata"]["dummy_number"] == "+91-90000-00001"
        assert kwargs["metadata"]["caller_id"] == "23BEC7152"
        assert kwargs["metadata"]["channel"] == "telephony_dummy_service"


def test_fetch_from_llm_module_mock():
    with patch("vtop_vitap_agent.llm.query_vtop_agent_sync", return_value="Mocked response") as mock_q:
        res = fetch_from_llm_module("What is my CGPA?", language="english")
        assert res == "Mocked response"
        mock_q.assert_called_once_with("What is my CGPA?", language="english")
