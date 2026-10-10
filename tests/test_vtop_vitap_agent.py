import asyncio
import io
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from vtop_vitap_agent.llm import VtopVoiceAgent
from vtop_vitap_agent.voice_input import VoiceInputResult, VoiceInputService
from vtop_vitap_agent.voice_output import VoiceOutputResult, VoiceOutputService


class TestVoiceInputService:
    @pytest.fixture
    def voice_in(self):
        return VoiceInputService()

    def test_resolve_language_code(self, voice_in):
        assert voice_in.resolve_language_code("English") == "en"
        assert voice_in.resolve_language_code("hindi") == "hi"
        assert voice_in.resolve_language_code("Telugu") == "te"
        assert voice_in.resolve_language_code("tamil") == "ta"
        assert voice_in.resolve_language_code(None) is None

    def test_load_audio_bytes_raw_pcm(self, voice_in):
        # 16000 int16 samples (1 second) of silence
        raw_pcm = b"\x00\x00" * 16000
        audio_np = voice_in.load_audio_bytes(raw_pcm)
        assert isinstance(audio_np, np.ndarray)
        assert len(audio_np) == 16000
        assert audio_np.dtype == np.float32

    def test_transcribe_mocked(self, voice_in):
        mock_whisper = MagicMock()
        mock_whisper.transcribe.return_value = {
            "text": "What is my attendance?",
            "language": "en",
            "segments": [],
        }

        with patch.object(voice_in, "_whisper_model", mock_whisper):
            dummy_audio = np.zeros(16000, dtype=np.float32)
            res = voice_in.transcribe(dummy_audio, preferred_language="english")
            assert res["text"] == "What is my attendance?"
            assert res["language"] == "en"

    def test_transfer_to_llm_empty_input(self, voice_in):
        reply = voice_in.transfer_to_llm("")
        assert "No voice input detected" in reply

    def test_transfer_to_llm_mocked(self, voice_in):
        mock_llm = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = "Your attendance is 85 percent."
        mock_llm.invoke.return_value = mock_resp

        with patch.object(voice_in, "_llm", mock_llm):
            reply = voice_in.transfer_to_llm("attendance", user_language="english")
            assert reply == "Your attendance is 85 percent."

    def test_handle_dummy_call(self, voice_in):
        mock_whisper = MagicMock()
        mock_whisper.transcribe.return_value = {"text": "Hello", "language": "en"}
        mock_llm = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = "Hello student, how can I help you?"
        mock_llm.invoke.return_value = mock_resp

        with patch.object(voice_in, "_whisper_model", mock_whisper), patch.object(voice_in, "_llm", mock_llm):
            dummy_audio = np.zeros(16000, dtype=np.float32)
            res = voice_in.handle_dummy_call(
                dummy_phone_number="+91-90000-00001",
                audio_input=dummy_audio,
                preferred_language="english",
                caller_id="STUDENT123",
            )
            assert isinstance(res, VoiceInputResult)
            assert res.query_text == "Hello"
            assert res.llm_response == "Hello student, how can I help you?"
            assert res.metadata["dummy_number"] == "+91-90000-00001"


class TestVoiceOutputService:
    @pytest.fixture
    def voice_out(self):
        return VoiceOutputService()

    def test_resolve_language(self, voice_out):
        assert voice_out.resolve_language("English") == "en"
        assert voice_out.resolve_language("hindi") == "hi"
        assert voice_out.resolve_language("telugu") == "te"
        assert voice_out.resolve_language("Tamil") == "ta"
        assert voice_out.resolve_language("") == "en"

    def test_synthesize_empty_text(self, voice_out):
        res = voice_out.synthesize("")
        assert not res.success
        assert len(res.audio_data) == 0
        assert res.audio_bytes == b""

    def test_synthesize_mocked_pipeline(self, voice_out):
        mock_pipeline = MagicMock()
        # Mock generator yields tuple (_, _, segment)
        mock_pipeline.return_value = [("g", "p", np.zeros(12000, dtype=np.float32))]

        with patch.object(voice_out, "resolve_voice_and_pipeline", return_value=(mock_pipeline, "a", "af_heart")):
            res = voice_out.synthesize("Welcome to VTOP", language="english")
            assert res.success is True
            assert res.duration_seconds > 0
            assert len(res.audio_bytes) > 0
            assert res.voice_name == "af_heart"

    def test_send_voice_message_to_user_from_dict(self, voice_out):
        with patch.object(voice_out, "synthesize") as mock_synth:
            mock_synth.return_value = VoiceOutputResult(
                text="Test",
                language="en",
                voice_name="af_heart",
                audio_data=np.zeros(100),
                success=True,
            )
            res = voice_out.send_voice_message_to_user({"response": "Test"}, play_audio=False)
            assert res.text == "Test"


class TestVtopVoiceAgent:
    def test_init_defaults(self):
        agent = VtopVoiceAgent()
        assert agent.model_name == "llama3.1:8b"
        assert "VIT-AP VTOP Voice Assistant" in agent.system_prompt

    def test_ask_mocked(self):
        async def _test():
            agent = VtopVoiceAgent()
            mock_langchain_agent = AsyncMock()
            mock_msg = MagicMock()
            mock_msg.content = "Your next class is in room CB-G01."
            mock_langchain_agent.ainvoke.return_value = {"messages": [mock_msg]}

            agent.agent = mock_langchain_agent

            reply = await agent.ask("When is my next class?")
            assert reply == "Your next class is in room CB-G01."

        asyncio.run(_test())
