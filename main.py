import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# Ensure repo root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from vtop_vitap_agent.workflow import VtopVoiceWorkflow, handle_dummy_phone_call

logger = logging.getLogger("jev_bot")


def run_doctor() -> int:
    """Checks the health and availability of all project modules and dependencies."""
    print("\n=== JEV VTOP Bot Diagnostic Check ===")
    status_all_ok = True

    # 1. Check vtop_vitap_login
    try:
        from vtop_vitap_login import session_store
        active_users = session_store.list_active_sessions()
        print(f"[OK] vtop_vitap_login: Active sessions found for {active_users if active_users else 'None (login required)'}")
    except Exception as e:
        print(f"[FAIL] vtop_vitap_login: {e}")
        status_all_ok = False

    # 2. Check cli-bot
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("cli_bot_login", PROJECT_ROOT / "cli-bot" / "login.py")
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            print("[OK] cli-bot: FastAPI service initialized successfully")
        else:
            print("[FAIL] cli-bot: could not load module spec")
            status_all_ok = False
    except Exception as e:
        print(f"[FAIL] cli-bot: {e}")
        status_all_ok = False

    # 3. Check vtop_mcp_tool
    try:
        from vtop_mcp_tool.vtop_mcp import mcp
        print(f"[OK] vtop_mcp_tool: MCP server '{mcp.name}' loaded successfully")
    except Exception as e:
        print(f"[FAIL] vtop_mcp_tool: {e}")
        status_all_ok = False

    # 4. Check vtop_vitap_agent modules
    try:
        from vtop_vitap_agent.voice_input import VoiceInputService
        from vtop_vitap_agent.voice_output import VoiceOutputService
        from vtop_vitap_agent.llm import VtopVoiceAgent
        from vtop_vitap_agent.workflow import VtopVoiceWorkflow
        print("[OK] vtop_vitap_agent: voice_input, voice_output, llm, and workflow loaded successfully")
    except Exception as e:
        print(f"[FAIL] vtop_vitap_agent: {e}")
        status_all_ok = False

    print("=======================================")
    if status_all_ok:
        print("Result: All project components are healthy.\n")
        return 0
    else:
        print("Result: One or more components encountered issues.\n")
        return 1


def run_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Runs the FastAPI cli-bot server."""
    import uvicorn
    import importlib.util

    spec = importlib.util.spec_from_file_location("cli_bot_login", PROJECT_ROOT / "cli-bot" / "login.py")
    if not spec or not spec.loader:
        raise RuntimeError("Failed to load cli-bot/login.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    uvicorn.run(mod.app, host=host, port=port)


def run_mcp() -> None:
    """Runs the FastMCP server via stdio transport."""
    from vtop_mcp_tool.vtop_mcp import mcp
    mcp.run(transport="stdio")


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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="JEV Bot for VTOP: Multi-lingual Voice Assistant & Services",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # doctor
    subparsers.add_parser("doctor", help="Run health and configuration diagnostics")

    # server
    server_parser = subparsers.add_parser("server", help="Start the FastAPI CLI-bot authentication server")
    server_parser.add_argument("--host", default="127.0.0.1", help="Host address (default: 127.0.0.1)")
    server_parser.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")

    # mcp
    subparsers.add_parser("mcp", help="Start the VTOP MCP server via stdio")

    # demo / call
    call_parser = subparsers.add_parser("call", help="Simulate a telephony voice call on the dummy number service")
    call_parser.add_argument("--number", default="+91-90000-00001", help="Dummy phone number")
    call_parser.add_argument("--lang", default="english", choices=["english", "hindi", "telugu", "tamil"], help="Caller language")
    call_parser.add_argument("--query", default="What is my attendance status?", help="Query text to simulate")

    # root options for backwards compatibility
    parser.add_argument("--lang", type=str, default="english", choices=["english", "hindi", "telugu", "tamil"], help="Caller language")
    parser.add_argument("--query", type=str, default="What classes do I have today?", help="Query text")
    parser.add_argument("--interactive", action="store_true", help="Interactive CLI chat mode")

    args = parser.parse_args()

    if args.command == "doctor":
        sys.exit(run_doctor())
    elif args.command == "server":
        run_server(host=args.host, port=args.port)
    elif args.command == "mcp":
        run_mcp()
    elif args.command == "call":
        run_demo(language=args.lang, user_query=args.query)
    elif args.interactive:
        from vtop_vitap_agent.llm import _interactive_cli
        asyncio.run(_interactive_cli())
    else:
        run_demo(language=args.lang, user_query=args.query)


if __name__ == "__main__":
    main()
