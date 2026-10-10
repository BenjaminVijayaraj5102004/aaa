import io
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union

import numpy as np
import pyaudio
import soundfile as sf
import whisper
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

logger = logging.getLogger("vtop_voice_input")

# Supported natural languages mapped to ISO 639-1 language codes for Whisper
SUPPORTED_LANGUAGES: Dict[str, str] = {
    "english": "en",
    "en": "en",
    "hindi": "hi",
    "hi": "hi",
    "telugu": "te",
    "te": "te",
    "tamil": "ta",
    "ta": "ta",
}

DEFAULT_SAMPLE_RATE = 16000
DEFAULT_CHUNK_SIZE = 1024
DEFAULT_RECORD_SECONDS = 5.0


@dataclass
class VoiceInputResult:
    """Structured result returned by the voice input and LLM processing pipeline."""

    query_text: str
    detected_language: str
    preferred_language: Optional[str]
    llm_response: str
    duration_seconds: float
    success: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


class VoiceInputService:
    """
    Voice Input Service for VTOP Assistant.
    
    Handles:
    1. Capturing audio from live microphone, raw audio bytes, or audio files.
    2. Transcribing user queries in their preferred natural language (English, Hindi, Telugu, Tamil).
    3. Transferring the transcribed query to the LLM (ChatOllama) for processing.
    4. Preparing structured responses ready for voice output synthesis (Kokoro TTS).
    """

    def __init__(
        self,
        whisper_model_name: str = "turbo",
        device: str = "cpu",
        ollama_model: str = "llama3.1:8b",
        temperature: float = 0.7,
        system_prompt: Optional[str] = None,
    ):
        self.device = device
        self.whisper_model_name = whisper_model_name
        self.ollama_model_name = ollama_model
        self.temperature = temperature

        self._whisper_model: Optional[whisper.Whisper] = None
        self._llm: Optional[ChatOllama] = None

        self.system_prompt = system_prompt or (
            "You are JEV, the AI voice assistant for VIT-AP students (VTOP). "
            "You receive voice queries from students calling the portal service in their "
            "preferred language (English, Hindi, Telugu, or Tamil). "
            "Provide helpful, concise, conversational, and direct answers suitable "
            "for spoken voice response. Respond in the same natural language the user speaks "
            "unless they explicitly request otherwise."
        )

    @property
    def whisper_model(self) -> whisper.Whisper:
        """Lazily load the Whisper model to keep startup fast."""
        if self._whisper_model is None:
            logger.info(f"Loading Whisper model '{self.whisper_model_name}' on {self.device}...")
            self._whisper_model = whisper.load_model(self.whisper_model_name, device=self.device)
        return self._whisper_model

    @property
    def llm(self) -> ChatOllama:
        """Lazily initialize the ChatOllama client."""
        if self._llm is None:
            logger.info(f"Connecting to Ollama with model '{self.ollama_model_name}'...")
            self._llm = ChatOllama(
                model=self.ollama_model_name,
                temperature=self.temperature,
            )
        return self._llm

    @staticmethod
    def resolve_language_code(language: Optional[str]) -> Optional[str]:
        """Maps user language names ('english', 'hindi', 'telugu', 'tamil') to Whisper codes."""
        if not language:
            return None
        lang_clean = language.strip().lower()
        return SUPPORTED_LANGUAGES.get(lang_clean, lang_clean)

    def record_microphone(
        self,
        record_seconds: float = DEFAULT_RECORD_SECONDS,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> np.ndarray:
        """Captures audio directly from the default input device (microphone)."""
        p = pyaudio.PyAudio()
        stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=sample_rate,
            input=True,
            frames_per_buffer=chunk_size,
        )

        logger.info(f"[Microphone] Recording for {record_seconds}s...")
        frames = []
        num_frames = int(sample_rate / chunk_size * record_seconds)
        for _ in range(num_frames):
            data = stream.read(chunk_size)
            frames.append(data)

        stream.stop_stream()
        stream.close()
        p.terminate()
        logger.info("[Microphone] Recording complete.")

        raw_bytes = b"".join(frames)
        audio_np = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        return audio_np

    def load_audio_file(self, file_path: Union[str, Path]) -> np.ndarray:
        """Loads and normalizes an audio file (e.g. .wav, .mp3) to 16kHz float32."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {path}")

        logger.info(f"Loading audio file from {path}")
        return whisper.load_audio(str(path))

    def load_audio_bytes(
        self,
        audio_bytes: bytes,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
    ) -> np.ndarray:
        """Converts raw audio bytes (PCM int16 or encoded format like WAV) into float32 array."""
        try:
            # First try reading as an encoded container format (WAV, MP3, OGG) via soundfile
            data, sr = sf.read(io.BytesIO(audio_bytes), dtype="float32")
            if data.ndim > 1:
                data = data.mean(axis=1)  # Convert multi-channel to mono
            return data
        except Exception:
            # Fall back to raw 16-bit PCM buffer
            audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            return audio_np

    def transcribe(
        self,
        audio: np.ndarray,
        preferred_language: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Transcribes speech using Whisper with optional preferred language constraint.
        Supported languages: English ('en'), Hindi ('hi'), Telugu ('te'), Tamil ('ta').
        """
        lang_code = self.resolve_language_code(preferred_language)
        padded_audio = whisper.pad_or_trim(audio)

        transcribe_args = {
            "fp16": False,
            "task": "transcribe",
        }
        if lang_code:
            transcribe_args["language"] = lang_code

        logger.info(f"Transcribing audio with preferred language: {preferred_language or 'auto-detect'} (code: {lang_code})...")
        result = self.whisper_model.transcribe(padded_audio, **transcribe_args)
        
        detected_lang = result.get("language", lang_code or "unknown")
        text = result.get("text", "").strip()
        logger.info(f"Transcription: '{text}' [Detected language: {detected_lang}]")

        return {
            "text": text,
            "language": detected_lang,
            "segments": result.get("segments", []),
        }

    def transfer_to_llm(
        self,
        query_text: str,
        user_language: Optional[str] = None,
        context_metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Transfers the transcribed query to the LLM (ChatOllama) and generates a spoken response.
        Ensures the response matches the user's preferred natural language.
        """
        if not query_text:
            return "No voice input detected. Please speak again."

        lang_instruction = ""
        if user_language:
            lang_instruction = f" The caller spoke in {user_language}. Reply naturally and conversationally in {user_language}."

        prompt_system = self.system_prompt + lang_instruction

        messages = [
            SystemMessage(content=prompt_system),
            HumanMessage(content=query_text),
        ]

        logger.info(f"Sending transcribed query to LLM ({self.ollama_model_name})...")
        response = self.llm.invoke(messages)
        return str(response.content).strip()

    def process_voice_pipeline(
        self,
        audio_input: Union[np.ndarray, bytes, str, Path],
        preferred_language: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        on_llm_response: Optional[Callable[[str], None]] = None,
    ) -> VoiceInputResult:
        """
        Full end-to-end voice processing:
        Audio Input -> Multi-language Whisper Transcription -> LLM Query Processing -> Output.
        """
        start_time = time.time()
        meta = metadata.copy() if metadata else {}

        # 1. Resolve audio input into numpy float32 array
        if isinstance(audio_input, np.ndarray):
            audio = audio_input
        elif isinstance(audio_input, (str, Path)):
            audio = self.load_audio_file(audio_input)
            meta["source_file"] = str(audio_input)
        elif isinstance(audio_input, bytes):
            audio = self.load_audio_bytes(audio_input)
            meta["source_type"] = "bytes"
        else:
            raise ValueError(f"Unsupported audio input type: {type(audio_input)}")

        # 2. Transcribe using Whisper
        transcription_result = self.transcribe(audio, preferred_language=preferred_language)
        query_text = transcription_result["text"]
        detected_lang = transcription_result["language"]

        # 3. Transfer to LLM
        if query_text:
            llm_reply = self.transfer_to_llm(
                query_text=query_text,
                user_language=preferred_language or detected_lang,
                context_metadata=meta,
            )
        else:
            llm_reply = "No speech detected. Please try speaking again."

        # 4. Optional hook for voice output (Kokoro)
        if on_llm_response and llm_reply:
            try:
                on_llm_response(llm_reply)
            except Exception as e:
                logger.warning(f"Voice output callback failed: {e}")

        duration = time.time() - start_time

        return VoiceInputResult(
            query_text=query_text,
            detected_language=detected_lang,
            preferred_language=preferred_language,
            llm_response=llm_reply,
            duration_seconds=duration,
            success=bool(query_text),
            metadata=meta,
        )

    def handle_dummy_call(
        self,
        dummy_phone_number: str,
        audio_input: Union[np.ndarray, bytes, str, Path],
        preferred_language: Optional[str] = None,
        caller_id: Optional[str] = None,
    ) -> VoiceInputResult:
        """
        Simulates an incoming call on the dummy phone number service.
        Processes caller speech in their preferred language (English, Hindi, Telugu, Tamil),
        transfers to the LLM, and formats for voice output.
        """
        logger.info(
            f"[Dummy Phone Call] Incoming call to {dummy_phone_number} "
            f"(Caller: {caller_id or 'Unknown'}, Preferred Language: {preferred_language or 'Auto'})"
        )
        call_metadata = {
            "dummy_number": dummy_phone_number,
            "caller_id": caller_id,
            "channel": "telephony_dummy_service",
            "timestamp": time.time(),
        }
        return self.process_voice_pipeline(
            audio_input=audio_input,
            preferred_language=preferred_language,
            metadata=call_metadata,
        )


# Backward-compatible alias for the Windsurf framework workflow
WindsurfVoiceInputService = VoiceInputService


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    print("\n=== VTOP Voice Input & LLM Pipeline ===")
    print(f"Supported Languages: {', '.join(SUPPORTED_LANGUAGES.keys())}")
    
    service = VoiceInputService()

    # Demonstration of a dummy number telephone call workflow
    DUMMY_NUMBER = "+91-90000-00001"
    print(f"\nSimulating incoming call to dummy number: {DUMMY_NUMBER}")
    
    # Try recording 5 seconds of microphone audio
    try:
        audio = service.record_microphone(record_seconds=4.0)
        result = service.handle_dummy_call(
            dummy_phone_number=DUMMY_NUMBER,
            audio_input=audio,
            preferred_language="english",
            caller_id="STUDENT_TEST_USER",
        )
        print("\n--- Pipeline Result ---")
        print(f"User Query      : {result.query_text}")
        print(f"Detected Lang   : {result.detected_language}")
        print(f"LLM Response    : {result.llm_response}")
        print(f"Total Time      : {result.duration_seconds:.2f}s")
    except Exception as err:
        print(f"Interactive microphone test skipped or failed: {err}")
