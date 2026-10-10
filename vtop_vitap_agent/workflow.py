import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import numpy as np

from vtop_vitap_agent.voice_input import VoiceInputResult, VoiceInputService
from vtop_vitap_agent.voice_output import VoiceOutputResult, VoiceOutputService

logger = logging.getLogger("vtop_voice_workflow")


@dataclass
class CallSessionResult:
    """Structured result of a simulated telephone voice session."""

    session_id: str
    dummy_phone_number: str
    caller_id: Optional[str]
    preferred_language: str
    detected_language: str
    user_query: str
    llm_response: str
    voice_output: VoiceOutputResult
    total_duration_seconds: float
    success: bool
    metadata: Dict[str, Any] = field(default_factory=dict)


class VtopVoiceWorkflow:
    """
    VTOP Voice Assistant End-to-End Workflow Orchestrator.

    Workflow implementation:
    1. Incoming call on dummy number.
    2. Speech recognition in preferred natural language (English, Hindi, Telugu, Tamil).
    3. Query processing with LLM (ChatOllama + VTOP MCP tools).
    4. Voice synthesis and playback via Kokoro TTS.
    """

    def __init__(
        self,
        voice_input_service: Optional[VoiceInputService] = None,
        voice_output_service: Optional[VoiceOutputService] = None,
        default_phone_number: str = "+91-90000-00001",
    ):
        self.input_service = voice_input_service or VoiceInputService()
        self.output_service = voice_output_service or VoiceOutputService()
        self.default_phone_number = default_phone_number

    def handle_call(
        self,
        audio_input: Union[np.ndarray, bytes, str, Path],
        preferred_language: Optional[str] = "english",
        dummy_phone_number: Optional[str] = None,
        caller_id: Optional[str] = None,
        play_audio: bool = False,
        output_file: Optional[Union[str, Path]] = None,
    ) -> CallSessionResult:
        """Processes an incoming voice call through the full pipeline."""
        start_time = time.time()
        session_id = str(uuid.uuid4())
        phone = dummy_phone_number or self.default_phone_number
        caller = caller_id or "STUDENT_CALLER"
        lang = preferred_language or "english"

        logger.info(
            f"[Workflow: {session_id[:8]}] Call initiated to {phone} by {caller} in {lang}"
        )

        input_result: VoiceInputResult = self.input_service.handle_dummy_call(
            dummy_phone_number=phone,
            audio_input=audio_input,
            preferred_language=lang,
            caller_id=caller,
        )

        output_result: VoiceOutputResult = self.output_service.handle_dummy_call_response(
            llm_response=input_result.llm_response,
            dummy_phone_number=phone,
            caller_id=caller,
            language=lang,
            play_audio=play_audio,
            output_file=output_file,
        )

        total_duration = time.time() - start_time

        return CallSessionResult(
            session_id=session_id,
            dummy_phone_number=phone,
            caller_id=caller,
            preferred_language=lang,
            detected_language=input_result.detected_language,
            user_query=input_result.query_text,
            llm_response=input_result.llm_response,
            voice_output=output_result,
            total_duration_seconds=total_duration,
            success=input_result.success and output_result.success,
            metadata={
                "input_duration": input_result.duration_seconds,
                "output_duration": output_result.duration_seconds,
                "output_wav_bytes": len(output_result.audio_bytes or b""),
            },
        )

    def simulate_text_call(
        self,
        user_text: str,
        preferred_language: Optional[str] = "english",
        dummy_phone_number: Optional[str] = None,
        caller_id: Optional[str] = None,
        play_audio: bool = False,
        output_file: Optional[Union[str, Path]] = None,
    ) -> CallSessionResult:
        """Simulates a call when user input is already transcribed text."""
        start_time = time.time()
        session_id = str(uuid.uuid4())
        phone = dummy_phone_number or self.default_phone_number
        caller = caller_id or "STUDENT_CALLER"
        lang = preferred_language or "english"

        llm_reply = self.input_service.transfer_to_llm(
            query_text=user_text,
            user_language=lang,
            context_metadata={"caller_id": caller, "dummy_number": phone},
        )

        output_result: VoiceOutputResult = self.output_service.handle_dummy_call_response(
            llm_response=llm_reply,
            dummy_phone_number=phone,
            caller_id=caller,
            language=lang,
            play_audio=play_audio,
            output_file=output_file,
        )

        total_duration = time.time() - start_time

        return CallSessionResult(
            session_id=session_id,
            dummy_phone_number=phone,
            caller_id=caller,
            preferred_language=lang,
            detected_language=lang,
            user_query=user_text,
            llm_response=llm_reply,
            voice_output=output_result,
            total_duration_seconds=total_duration,
            success=bool(llm_reply) and output_result.success,
            metadata={
                "output_duration": output_result.duration_seconds,
                "output_wav_bytes": len(output_result.audio_bytes or b""),
            },
        )


def handle_dummy_phone_call(
    audio_or_text: Union[np.ndarray, bytes, str, Path],
    language: str = "english",
    dummy_number: str = "+91-90000-00001",
    caller_id: str = "STUDENT_CALLER",
    play_audio: bool = False,
    output_file: Optional[Union[str, Path]] = None,
) -> CallSessionResult:
    """Convenience helper to execute the full dummy phone call workflow."""
    wf = VtopVoiceWorkflow(default_phone_number=dummy_number)
    if isinstance(audio_or_text, str) and not Path(audio_or_text).exists():
        return wf.simulate_text_call(
            user_text=audio_or_text,
            preferred_language=language,
            dummy_phone_number=dummy_number,
            caller_id=caller_id,
            play_audio=play_audio,
            output_file=output_file,
        )
    return wf.handle_call(
        audio_input=audio_or_text,
        preferred_language=language,
        dummy_phone_number=dummy_number,
        caller_id=caller_id,
        play_audio=play_audio,
        output_file=output_file,
    )
