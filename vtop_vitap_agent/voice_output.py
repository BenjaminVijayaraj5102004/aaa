import io
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import numpy as np
import pyaudio
import soundfile as sf
import torch
from kokoro import KModel, KPipeline

logger = logging.getLogger("vtop_voice_output")

# Supported natural languages mapped to ISO codes
SUPPORTED_LANGUAGES: Dict[str, str] = {
    "english": "en",
    "en": "en",
    "en-us": "en",
    "en-gb": "en-gb",
    "hindi": "hi",
    "hindhi": "hi",
    "hi": "hi",
    "telugu": "te",
    "te": "te",
    "tamil": "ta",
    "ta": "ta",
}

# Kokoro configuration per language code
# Kokoro-82M supports 'a' (US English), 'b' (GB English), and 'h' (Hindi).
# Telugu and Tamil utilize Indic phonemization (EspeakG2P) with Kokoro's Hindi vocoder pipeline.
LANGUAGE_KOKORO_MAP: Dict[str, Dict[str, str]] = {
    "en": {"lang_code": "a", "default_voice": "af_heart"},
    "en-gb": {"lang_code": "b", "default_voice": "bf_alice"},
    "hi": {"lang_code": "h", "default_voice": "hf_alpha"},
    "te": {"lang_code": "h", "default_voice": "hf_alpha"},
    "ta": {"lang_code": "h", "default_voice": "hf_alpha"},
}

DEFAULT_SAMPLE_RATE = 24000


@dataclass
class VoiceOutputResult:
    """Structured result returned by the voice output synthesis pipeline."""

    text: str
    language: str
    voice_name: str
    audio_data: np.ndarray
    sample_rate: int = DEFAULT_SAMPLE_RATE
    duration_seconds: float = 0.0
    audio_bytes: Optional[bytes] = None
    success: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


class VoiceOutputService:
    """
    Voice Output Service for VTOP Assistant using Kokoro TTS.

    Workflow:
    1. Receives output message from the LLM (e.g. from llm.py, ChatOllama, or agent response).
    2. Resolves the user's preferred natural language (English, Hindi, Telugu, Tamil).
    3. Synthesizes voice audio using Kokoro KPipeline.
    4. Delivers the voice output back to the user via speaker streaming, audio bytes (for API/calls),
       or audio file.
    """

    def __init__(
        self,
        repo_id: str = "hexgrad/Kokoro-82M",
        device: str = "cpu",
        default_language: str = "english",
    ):
        self.repo_id = repo_id
        self.device = device
        self.default_language = default_language

        # Shared KModel instance across pipelines to optimize memory
        self._shared_model: Optional[KModel] = None
        # Cached pipelines keyed by Kokoro lang_code ('a', 'h', 'b', etc.)
        self._pipelines: Dict[str, KPipeline] = {}

    @property
    def shared_model(self) -> KModel:
        """Lazily loads and caches the shared Kokoro neural model."""
        if self._shared_model is None:
            logger.info(f"Loading Kokoro model '{self.repo_id}' on {self.device}...")
            self._shared_model = KModel(repo_id=self.repo_id).to(self.device).eval()
        return self._shared_model

    def get_pipeline(self, kokoro_lang_code: str) -> KPipeline:
        """Retrieves or initializes a cached KPipeline for the requested Kokoro lang_code."""
        if kokoro_lang_code not in self._pipelines:
            logger.info(
                f"Initializing Kokoro KPipeline for lang_code='{kokoro_lang_code}' on {self.device}..."
            )
            pipeline = KPipeline(
                lang_code=kokoro_lang_code,
                repo_id=self.repo_id,
                model=self.shared_model,
                device=self.device,
            )
            self._pipelines[kokoro_lang_code] = pipeline
        return self._pipelines[kokoro_lang_code]

    @staticmethod
    def resolve_language(language: Optional[str]) -> str:
        """Resolves natural language input to normalized key ('en', 'hi', 'te', 'ta')."""
        if not language:
            return "en"
        cleaned = language.strip().lower()
        return SUPPORTED_LANGUAGES.get(cleaned, "en")

    def resolve_voice_and_pipeline(
        self, language: Optional[str] = None, custom_voice: Optional[str] = None
    ) -> Tuple[KPipeline, str, str]:
        """
        Determines the pipeline, Kokoro language code, and voice name.
        Returns: (pipeline, kokoro_lang_code, voice_name)
        """
        lang_key = self.resolve_language(language or self.default_language)
        config = LANGUAGE_KOKORO_MAP.get(lang_key, LANGUAGE_KOKORO_MAP["en"])

        kokoro_lang_code = config["lang_code"]
        voice_name = custom_voice or config["default_voice"]
        pipeline = self.get_pipeline(kokoro_lang_code)

        return pipeline, kokoro_lang_code, voice_name

    def synthesize(
        self,
        text: str,
        language: Optional[str] = "english",
        voice: Optional[str] = None,
        speed: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> VoiceOutputResult:
        """
        Synthesizes text into a complete audio waveform and in-memory WAV bytes.
        """
        clean_text = str(text or "").strip()
        meta = metadata.copy() if metadata else {}

        if not clean_text:
            empty_arr = np.array([], dtype=np.float32)
            return VoiceOutputResult(
                text="",
                language=language or self.default_language,
                voice_name=voice or "none",
                audio_data=empty_arr,
                sample_rate=DEFAULT_SAMPLE_RATE,
                duration_seconds=0.0,
                audio_bytes=b"",
                success=False,
                metadata=meta,
            )

        start_time = time.time()
        pipeline, kokoro_lang_code, voice_name = self.resolve_voice_and_pipeline(
            language=language, custom_voice=voice
        )

        logger.info(
            f"Synthesizing voice output in '{language}' (Kokoro lang: '{kokoro_lang_code}', voice: '{voice_name}')..."
        )

        segments: List[np.ndarray] = []
        generator = pipeline(clean_text, voice=voice_name, speed=speed)

        for _, _, audio in generator:
            if audio is None:
                continue
            if hasattr(audio, "detach"):
                audio_np = audio.detach().cpu().numpy()
            else:
                audio_np = np.asarray(audio)
            segments.append(audio_np.astype(np.float32))

        if segments:
            full_audio = np.concatenate(segments)
        else:
            full_audio = np.array([], dtype=np.float32)

        # Encode full audio to in-memory WAV bytes
        wav_buffer = io.BytesIO()
        if len(full_audio) > 0:
            sf.write(wav_buffer, full_audio, DEFAULT_SAMPLE_RATE, format="WAV")
        wav_bytes = wav_buffer.getvalue()

        duration = len(full_audio) / DEFAULT_SAMPLE_RATE if len(full_audio) > 0 else 0.0
        elapsed_time = time.time() - start_time
        meta["synthesis_latency_seconds"] = round(elapsed_time, 3)

        logger.info(
            f"Synthesized {duration:.2f}s of audio for '{clean_text[:40]}...' in {elapsed_time:.2f}s"
        )

        return VoiceOutputResult(
            text=clean_text,
            language=language or self.default_language,
            voice_name=voice_name,
            audio_data=full_audio,
            sample_rate=DEFAULT_SAMPLE_RATE,
            duration_seconds=duration,
            audio_bytes=wav_bytes,
            success=True,
            metadata=meta,
        )

    def play_stream(
        self,
        text: str,
        language: Optional[str] = "english",
        voice: Optional[str] = None,
        speed: float = 1.0,
    ) -> VoiceOutputResult:
        """
        Streams synthesized audio in real time directly to the speaker using PyAudio.
        Falls back gracefully if no audio output device is present.
        """
        clean_text = str(text or "").strip()
        if not clean_text:
            return self.synthesize("", language=language, voice=voice)

        pipeline, kokoro_lang_code, voice_name = self.resolve_voice_and_pipeline(
            language=language, custom_voice=voice
        )

        p = pyaudio.PyAudio()
        stream = None
        try:
            stream = p.open(
                format=pyaudio.paFloat32,
                channels=1,
                rate=DEFAULT_SAMPLE_RATE,
                output=True,
            )
        except Exception as e:
            logger.warning(
                f"Audio output device could not be opened (e.g. headless environment): {e}. Proceeding without live playback."
            )

        logger.info(
            f"[Speaking] Streaming voice output for: '{clean_text[:40]}...' (voice: '{voice_name}')"
        )
        generator = pipeline(clean_text, voice=voice_name, speed=speed)

        segments: List[np.ndarray] = []
        for _, _, audio in generator:
            if audio is None:
                continue
            if hasattr(audio, "detach"):
                audio_np = audio.detach().cpu().numpy()
            else:
                audio_np = np.asarray(audio)

            float32_audio = audio_np.astype(np.float32)
            segments.append(float32_audio)

            if stream is not None:
                try:
                    stream.write(float32_audio.tobytes())
                except Exception as stream_err:
                    logger.warning(f"Error writing to PyAudio stream: {stream_err}")

        if stream is not None:
            stream.stop_stream()
            stream.close()
        p.terminate()

        if segments:
            full_audio = np.concatenate(segments)
        else:
            full_audio = np.array([], dtype=np.float32)

        wav_buffer = io.BytesIO()
        if len(full_audio) > 0:
            sf.write(wav_buffer, full_audio, DEFAULT_SAMPLE_RATE, format="WAV")
        wav_bytes = wav_buffer.getvalue()

        duration = len(full_audio) / DEFAULT_SAMPLE_RATE if len(full_audio) > 0 else 0.0

        return VoiceOutputResult(
            text=clean_text,
            language=language or self.default_language,
            voice_name=voice_name,
            audio_data=full_audio,
            sample_rate=DEFAULT_SAMPLE_RATE,
            duration_seconds=duration,
            audio_bytes=wav_bytes,
            success=True,
            metadata={"streamed": True},
        )

    def save_to_file(
        self,
        text: str,
        output_path: Union[str, Path],
        language: Optional[str] = "english",
        voice: Optional[str] = None,
        speed: float = 1.0,
    ) -> Path:
        """Synthesizes text and saves it directly to an audio file (e.g. .wav)."""
        result = self.synthesize(text, language=language, voice=voice, speed=speed)
        dest = Path(output_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(dest), result.audio_data, result.sample_rate)
        logger.info(f"Saved audio output to {dest}")
        return dest

    def send_voice_message_to_user(
        self,
        message: Any,
        language: Optional[str] = "english",
        voice: Optional[str] = None,
        play_audio: bool = True,
        output_file: Optional[Union[str, Path]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> VoiceOutputResult:
        """
        Receives output from llm.py (or any LLM response object),
        synthesizes the voice message, and delivers it to the user.
        """
        # Extract text from various response types (LangChain message, dict, str)
        if hasattr(message, "content"):
            text = str(message.content)
        elif isinstance(message, dict):
            text = str(message.get("text") or message.get("response") or message.get("content") or "")
        else:
            text = str(message)

        text = text.strip()
        meta = metadata.copy() if metadata else {}

        if play_audio:
            result = self.play_stream(text=text, language=language, voice=voice)
            result.metadata.update(meta)
        else:
            result = self.synthesize(text=text, language=language, voice=voice, metadata=meta)

        if output_file and result.success and len(result.audio_data) > 0:
            dest = Path(output_file)
            dest.parent.mkdir(parents=True, exist_ok=True)
            sf.write(str(dest), result.audio_data, result.sample_rate)
            result.metadata["saved_file"] = str(dest)

        return result

    def handle_dummy_call_response(
        self,
        llm_response: Any,
        dummy_phone_number: str,
        caller_id: Optional[str] = None,
        language: Optional[str] = "english",
        play_audio: bool = False,
        output_file: Optional[Union[str, Path]] = None,
    ) -> VoiceOutputResult:
        """
        Completes the dummy number call workflow:
        Takes the LLM processed query output and sends the voice message back
        to the user in their preferred natural language (English, Hindi, Telugu, Tamil).
        """
        logger.info(
            f"[Dummy Phone Call Voice Output] Sending voice message to caller {caller_id or 'Unknown'} "
            f"via dummy number {dummy_phone_number} in language '{language}'"
        )
        call_meta = {
            "dummy_number": dummy_phone_number,
            "caller_id": caller_id,
            "channel": "telephony_dummy_service",
            "timestamp": time.time(),
        }
        return self.send_voice_message_to_user(
            message=llm_response,
            language=language,
            play_audio=play_audio,
            output_file=output_file,
            metadata=call_meta,
        )


def fetch_from_llm_module(user_query: str, language: Optional[str] = None, **kwargs) -> Optional[str]:
    """
    Attempts to retrieve an output message from llm.py if the module exists.
    Supports query_vtop_agent_sync, query_vtop_agent, VtopVoiceAgent, and other conventions.
    """
    try:
        from vtop_vitap_agent import llm  # type: ignore

        # 1. Check for query_vtop_agent_sync or query_vtop_agent
        if hasattr(llm, "query_vtop_agent_sync"):
            logger.info("Invoking query_vtop_agent_sync from vtop_vitap_agent.llm...")
            return llm.query_vtop_agent_sync(user_query, language=language)

        if hasattr(llm, "query_vtop_agent"):
            import asyncio
            logger.info("Invoking query_vtop_agent from vtop_vitap_agent.llm...")
            return asyncio.run(llm.query_vtop_agent(user_query, language=language))

        # 2. Check other common LLM helper functions
        for func_name in ("generate_response", "process_query", "get_llm_response", "query_llm", "ask_llm"):
            func = getattr(llm, func_name, None)
            if callable(func):
                logger.info(f"Invoking {func_name} from vtop_vitap_agent.llm...")
                res = func(user_query, language=language, **kwargs)
                if hasattr(res, "content"):
                    return str(res.content)
                return str(res)
    except Exception as e:
        logger.debug(f"llm.py invocation error or not accessible: {e}")
    return None


# Global default service instance
default_voice_output_service = VoiceOutputService()


def send_voice_output(
    message: Any,
    language: Optional[str] = "english",
    voice: Optional[str] = None,
    play_audio: bool = True,
    output_file: Optional[Union[str, Path]] = None,
) -> VoiceOutputResult:
    """Convenience helper to send a voice message to the user."""
    return default_voice_output_service.send_voice_message_to_user(
        message=message,
        language=language,
        voice=voice,
        play_audio=play_audio,
        output_file=output_file,
    )


# Backward-compatible alias for the Windsurf framework workflow
WindsurfVoiceOutputService = VoiceOutputService


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    print("\n=== VTOP Voice Output (Kokoro TTS) Workflow ===")
    print(f"Supported Languages: {', '.join(SUPPORTED_LANGUAGES.keys())}")

    service = VoiceOutputService()

    # Demonstration: Simulating message received from llm.py for an incoming call on the dummy number
    DUMMY_NUMBER = "+91-90000-00001"
    CALLER_ID = "STUDENT_23BEC7152"

    test_cases = [
        ("english", "Hello! Your current attendance is 87 percent, and your next class starts at 2 PM."),
        ("hindi", "नमस्ते! आपकी उपस्थिति 87 प्रतिशत है, और आपकी अगली कक्षा दोपहर 2 बजे है।"),
        ("telugu", "నమస్కారం! మీ ప్రస్తుత హాజరు శాతం 87 శాతం ఉంది."),
        ("tamil", "வணக்கம்! உங்கள் தற்போதைய வருகை 87 சதவீதம்."),
    ]

    for lang, sample_llm_output in test_cases:
        print(f"\n--- Testing Language: {lang} ---")
        print(f"Incoming LLM Message: {sample_llm_output}")

        result = service.handle_dummy_call_response(
            llm_response=sample_llm_output,
            dummy_phone_number=DUMMY_NUMBER,
            caller_id=CALLER_ID,
            language=lang,
            play_audio=False,  # Set to True when running interactively with speakers
        )

        print(f"Voice Synthesized   : {result.voice_name}")
        print(f"Duration            : {result.duration_seconds:.2f}s")
        print(f"WAV Bytes Generated : {len(result.audio_bytes or b'')} bytes")
        print(f"Success             : {result.success}")
