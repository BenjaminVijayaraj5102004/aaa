import argparse
import sys
from pathlib import Path

# Ensure repo root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from vtop_vitap_agent.workflow import VtopVoiceWorkflow, handle_dummy_phone_call


def run_demo(language: str = "english", user_query: str = "What classes do I have today?"):
    """Demonstrates incoming call workflow to the dummy number."""
    dummy_number = "+91-90000-00001"
    caller_id = "STUDENT_DEMO"

    print("=" * 65)
    print("  VTOP Multilingual Voice Assistant (JEV Bot)")
    print(f"  Dummy Number : {dummy_number}")
    print(f"  Caller       : {caller_id}")
    print(f"  Language     : {language}")
    print(f"  Query        : {user_query}")
    print("=" * 65)

    result = handle_dummy_phone_call(
        audio_or_text=user_query,
        language=language,
        dummy_number=dummy_number,
        caller_id=caller_id,
        play_audio=False,
    )

    print("\n--- Pipeline Result ---")
    print(f"Session ID    : {result.session_id}")
    print(f"Detected Lang : {result.detected_language}")
    print(f"User Query    : {result.user_query}")
    print(f"LLM Response  : {result.llm_response}")
    print(f"TTS Voice     : {result.voice_output.voice_name}")
    print(f"TTS Duration  : {result.voice_output.duration_seconds:.2f}s")
    print(f"WAV Bytes     : {len(result.voice_output.audio_bytes or b'')} bytes")
    print(f"Success       : {result.success}")
    print(f"Total Time    : {result.total_duration_seconds:.2f}s\n")


def main():
    parser = argparse.ArgumentParser(description="VTOP Voice Assistant (JEV Bot)")
    parser.add_argument(
        "--lang",
        type=str,
        default="english",
        choices=["english", "hindi", "telugu", "tamil"],
        help="Preferred caller language",
    )
    parser.add_argument(
        "--query",
        type=str,
        default="What is my attendance status?",
        help="Student query text to simulate incoming speech",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Run interactive CLI chat mode",
    )
    args = parser.parse_args()

    if args.interactive:
        from vtop_vitap_agent.llm import _interactive_cli
        import asyncio
        asyncio.run(_interactive_cli())
    else:
        run_demo(language=args.lang, user_query=args.query)


if __name__ == "__main__":
    main()
