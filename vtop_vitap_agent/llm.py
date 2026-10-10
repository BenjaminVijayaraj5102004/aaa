import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from langchain_ollama import ChatOllama
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain.agents import create_agent

logger = logging.getLogger("vtop_voice_agent")
logging.basicConfig(level=logging.INFO)

# Default configuration
DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
DEFAULT_OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
DEFAULT_MCP_SCRIPT = PROJECT_ROOT / "vtop_mcp_tool" / "vtop_mcp.py"

MULTILINGUAL_VOICE_SYSTEM_PROMPT = """You are the official VIT-AP VTOP Voice Assistant.
You assist university students over an interactive voice phone call service.

Key Operating Rules:
1. Language Preference:
   - Identify the student's preferred language from their query (English, Hindi, Telugu, or Tamil).
   - Always respond in the SAME language the student used:
     * English: Natural, conversational Indian English.
     * Hindi: Clear spoken Hindi or common student Hinglish (e.g., "Aapka attendance 82 percent hai").
     * Telugu: Polite, natural spoken Telugu.
     * Tamil: Polite, natural spoken Tamil.
   - If the user explicitly asks you to reply in another language (e.g., "tell me in Telugu"), follow that instruction.

2. Spoken Voice Format:
   - Your response is converted directly to speech using Kokoro Text-To-Speech and played to the caller.
   - NEVER use markdown tables, asterisks, hash headers, bullet points, brackets, or code blocks.
   - Speak in concise, friendly, full conversational sentences suitable for a phone call.
   - Read course codes, classroom venues, and timings naturally (e.g., "Room CB-G01 from 9 AM to 10 AM").

3. Tool Usage & Accuracy:
   - Always call the provided VTOP tools to retrieve accurate student data:
     * get_student_timetable: for today's or weekly schedule.
     * get_free_slots: for free time slots, breaks, or lunch window.
     * get_attendance_report: for attendance percentages, total attended, and margin.
     * get_exam_schedule: for mid-term or end-term exam dates and halls.
     * get_student_marks: for CAT, FAT, or internal assignment marks.
     * get_student_profile: for CGPA, branch, student name, and registration number.
     * get_digital_assignments: for pending or submitted assignments.
     * get_academic_calendar: for holidays and instructional working days.
     * get_grade_history: for GPA and historical semester grades.
   - If authentication fails or credentials are required, politely inform the student that their VTOP session has expired and ask them to log in.
   - Never fabricate or guess academic records. Always rely strictly on tool outputs.
"""


class VtopVoiceAgent:
    """VTOP Voice Assistant combining Ollama, LangChain, MCP tools, and Kokoro TTS."""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_OLLAMA_URL,
        mcp_script_path: Optional[Path] = None,
        temperature: float = 0.1,
        system_prompt: Optional[str] = None,
    ):
        self.model_name = model_name
        self.base_url = base_url
        self.mcp_script_path = (mcp_script_path or DEFAULT_MCP_SCRIPT).resolve()
        self.temperature = temperature
        self.system_prompt = system_prompt or MULTILINGUAL_VOICE_SYSTEM_PROMPT

        self.mcp_client: Optional[MultiServerMCPClient] = None
        self.agent: Any = None
        self.tools: List[Any] = []
        self._tts_pipeline: Any = None
        self._pyaudio: Any = None

    async def initialize(self) -> None:
        """Connects to the MCP server via stdio and initializes the LangChain agent."""
        if self.agent is not None:
            return

        logger.info(f"Connecting to VTOP MCP server at: {self.mcp_script_path}")
        self.mcp_client = MultiServerMCPClient(
            connections={
                "vtop": {
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [str(self.mcp_script_path)],
                    "cwd": str(PROJECT_ROOT),
                }
            }
        )

        logger.info("Loading MCP tools...")
        self.tools = await self.mcp_client.get_tools()
        logger.info(f"Loaded {len(self.tools)} tools from VTOP MCP server.")

        logger.info(f"Initializing ChatOllama model '{self.model_name}'...")
        llm = ChatOllama(
            model=self.model_name,
            base_url=self.base_url,
            temperature=self.temperature,
        )

        logger.info("Building LangChain agent...")
        self.agent = create_agent(
            model=llm,
            tools=self.tools,
            system_prompt=self.system_prompt,
        )
        logger.info("VTOP Voice Agent initialized successfully.")

    async def ask(
        self,
        query: str,
        chat_history: Optional[List[Dict[str, str]]] = None,
        preferred_language: Optional[str] = None,
    ) -> str:
        """Processes a student query through the agent and returns a spoken-friendly answer."""
        if self.agent is None:
            await self.initialize()

        messages: List[Dict[str, str]] = []

        if chat_history:
            for item in chat_history:
                messages.append(item)

        user_prompt = query
        if preferred_language:
            user_prompt = f"[User preferred language: {preferred_language}]\n{query}"

        messages.append({"role": "user", "content": user_prompt})

        logger.info(f"Invoking agent with query: {query}")
        result = await self.agent.ainvoke({"messages": messages})

        # Retrieve the final assistant message
        final_message = result["messages"][-1]
        response_text = getattr(final_message, "content", "").strip()
        return response_text

    def _get_tts_pipeline(self, lang_code: str = "a") -> Any:
        """Lazy loader for Kokoro TTS pipeline."""
        if self._tts_pipeline is None:
            try:
                from kokoro import KPipeline
                self._tts_pipeline = KPipeline(lang_code=lang_code, device="cpu")
            except Exception as e:
                logger.warning(f"Could not initialize Kokoro TTS: {e}")
                return None
        return self._tts_pipeline

    def speak(
        self,
        text: str,
        voice: str = "af_heart",
        lang_code: str = "a",
        play_audio: bool = True,
    ) -> List[Any]:
        """Synthesizes text to speech using Kokoro TTS and optionally plays through speakers."""
        pipeline = self._get_tts_pipeline(lang_code=lang_code)
        if not pipeline:
            logger.warning("TTS pipeline not available. Skipping audio generation.")
            return []

        import numpy as np

        audio_chunks = []
        output_stream = None

        if play_audio:
            try:
                import pyaudio
                if self._pyaudio is None:
                    self._pyaudio = pyaudio.PyAudio()
                output_stream = self._pyaudio.open(
                    format=pyaudio.paFloat32,
                    channels=1,
                    rate=24000,
                    output=True,
                )
            except Exception as e:
                logger.warning(f"Audio output stream failed to open: {e}. Outputting silently.")
                output_stream = None

        try:
            generator = pipeline(text, voice=voice)
            for _, _, audio_segment in generator:
                if hasattr(audio_segment, "detach"):
                    audio_data = audio_segment.detach().cpu().numpy()
                else:
                    audio_data = np.asarray(audio_segment)

                audio_chunks.append(audio_data)

                if output_stream is not None:
                    output_stream.write(audio_data.astype(np.float32).tobytes())
        finally:
            if output_stream is not None:
                output_stream.stop_stream()
                output_stream.close()

        return audio_chunks

    async def ask_and_speak(
        self,
        query: str,
        preferred_language: Optional[str] = None,
        voice: str = "af_heart",
        play_audio: bool = True,
    ) -> Tuple[str, List[Any]]:
        """Processes query with LLM and speaks out the response."""
        answer = await self.ask(query, preferred_language=preferred_language)
        audio_chunks = self.speak(answer, voice=voice, play_audio=play_audio)
        return answer, audio_chunks

    async def close(self) -> None:
        """Cleans up audio resources."""
        if self._pyaudio is not None:
            try:
                self._pyaudio.terminate()
            except Exception:
                pass
            self._pyaudio = None

    async def __aenter__(self) -> "VtopVoiceAgent":
        await self.initialize()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()


async def query_vtop_agent(query: str, language: Optional[str] = None) -> str:
    """Convenience function to run a single query against the agent."""
    agent = VtopVoiceAgent()
    try:
        await agent.initialize()
        return await agent.ask(query, preferred_language=language)
    finally:
        await agent.close()


def query_vtop_agent_sync(query: str, language: Optional[str] = None) -> str:
    """Synchronous helper for querying the agent."""
    return asyncio.run(query_vtop_agent(query, language=language))


async def _interactive_cli():
    """CLI loop for testing natural language queries in English, Hindi, Telugu, or Tamil."""
    print("=" * 60)
    print("  VTOP Multilingual Voice Assistant (Ollama + MCP Tools)")
    print("  Supported Languages: English, Hindi, Telugu, Tamil")
    print("=" * 60)

    agent = VtopVoiceAgent()
    await agent.initialize()

    print("\nAssistant ready. Type your query (or 'exit' to quit):\n")

    try:
        while True:
            try:
                user_input = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not user_input or user_input.lower() in ("exit", "quit", "q"):
                print("Exiting...")
                break

            print("\nProcessing with Ollama and VTOP MCP tools...")
            try:
                response = await agent.ask(user_input)
                print(f"\nAI: {response}\n")
            except Exception as e:
                print(f"\nError: {e}\n")
    finally:
        await agent.close()


if __name__ == "__main__":
    asyncio.run(_interactive_cli())
