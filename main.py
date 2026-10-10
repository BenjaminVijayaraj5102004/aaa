import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

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
        print("[OK] vtop_vitap_agent: voice_input, voice_output, and llm modules imported successfully")
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


def run_dummy_call(
    number: str = "+91-90000-00001",
    language: str = "english",
    query_text: str | None = None,
    audio_path: str | None = None,
) -> None:
    """Executes the dummy number call simulation workflow described in workflow.md."""
    from vtop_vitap_agent.voice_input import VoiceInputService
    from vtop_vitap_agent.voice_output import VoiceOutputService

    print(f"\n[Call Inbound] Dummy Number: {number} | Preferred Language: {language}")
    in_service = VoiceInputService()
    out_service = VoiceOutputService()

    if audio_path:
        print(f"Loading caller audio from file: {audio_path}")
        result = in_service.handle_dummy_call(
            dummy_phone_number=number,
            audio_input=audio_path,
            preferred_language=language,
        )
    elif query_text:
        print(f"Simulating spoken query: '{query_text}'")
        llm_reply = in_service.transfer_to_llm(query_text=query_text, user_language=language)
        print(f"\n[AI Response]: {llm_reply}\n")
        out_res = out_service.synthesize(text=llm_reply, language=language)
        print(f"[Kokoro TTS]: Synthesized {out_res.duration_seconds:.2f}s of audio in {language}.")
        return
    else:
        print("Recording 4 seconds of microphone input...")
        audio = in_service.record_microphone(record_seconds=4.0)
        result = in_service.handle_dummy_call(
            dummy_phone_number=number,
            audio_input=audio,
            preferred_language=language,
        )

    print(f"\nCaller Query: {result.query_text}")
    print(f"AI Response : {result.llm_response}")
    out_res = out_service.synthesize(text=result.llm_response, language=language)
    print(f"[Kokoro TTS]: Synthesized {out_res.duration_seconds:.2f}s of voice output.\n")


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

    # call
    call_parser = subparsers.add_parser("call", help="Simulate a telephony voice call on the dummy number service")
    call_parser.add_argument("--number", default="+91-90000-00001", help="Dummy phone number")
    call_parser.add_argument("--lang", default="english", help="User preferred language (english, hindi, telugu, tamil)")
    call_parser.add_argument("--text", default=None, help="Text query to simulate without microphone")
    call_parser.add_argument("--audio", default=None, help="Audio file path to process")

    args = parser.parse_args()

    if args.command == "doctor":
        sys.exit(run_doctor())
    elif args.command == "server":
        run_server(host=args.host, port=args.port)
    elif args.command == "mcp":
        run_mcp()
    elif args.command == "call":
        run_dummy_call(
            number=args.number,
            language=args.lang,
            query_text=args.text,
            audio_path=args.audio,
        )
    else:
        print("Hello from jev-bot-for-vtop!")
        print("Run 'python main.py doctor' to verify all components.")
        print("Run 'python main.py --help' to see all options.")


if __name__ == "__main__":
    main()
