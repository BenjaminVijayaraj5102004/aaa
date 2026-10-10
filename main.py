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


def get_application():
    """Dynamically loads and returns the FastAPI application instance from cli-bot/login.py."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "cli_bot_login", PROJECT_ROOT / "cli-bot" / "login.py"
    )
    if not spec or not spec.loader:
        raise RuntimeError(f"Failed to load cli-bot/login.py from {PROJECT_ROOT / 'cli-bot' / 'login.py'}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.app


# ASGI application instance for standard Uvicorn commands (e.g. `uvicorn main:app`)
app = get_application()


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
        fastapi_app = get_application()
        route_count = len(fastapi_app.routes)
        print(f"[OK] cli-bot: FastAPI service initialized successfully ({route_count} routes registered)")
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

    # 5. Check nginx configuration
    try:
        nginx_conf = PROJECT_ROOT / "nginx" / "nginx.conf"
        if nginx_conf.exists():
            content = nginx_conf.read_text(encoding="utf-8")
            if "proxy_pass" in content and "8000" in content:
                print(f"[OK] nginx: Reverse proxy configuration verified at {nginx_conf.relative_to(PROJECT_ROOT)}")
            else:
                print(f"[WARN] nginx: {nginx_conf.name} present but missing backend proxy directives")
        else:
            print("[WARN] nginx: nginx/nginx.conf not found")
    except Exception as e:
        print(f"[FAIL] nginx: {e}")
        status_all_ok = False

    # 6. Check GitHub Actions workflow
    try:
        ci_workflow = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
        if ci_workflow.exists():
            print(f"[OK] github_actions: CI workflow verified at {ci_workflow.relative_to(PROJECT_ROOT)}")
        else:
            print("[WARN] github_actions: .github/workflows/ci.yml not found")
    except Exception as e:
        print(f"[FAIL] github_actions: {e}")
        status_all_ok = False

    print("=======================================")
    if status_all_ok:
        print("Result: All project components are healthy.\n")
        return 0
    else:
        print("Result: One or more components encountered issues.\n")
        return 1


def start_uvicorn(
    host: str = "0.0.0.0",
    port: int = 8000,
    reload: bool = False,
    workers: int = 1,
    log_level: str = "info",
    app_target: object = None,
) -> None:
    """
    Starts the Uvicorn ASGI server with configurable runtime parameters.

    Args:
        host: Interface binding address (default '0.0.0.0').
        port: Listening port number (default 8000).
        reload: Enable auto-reload on code change (default False).
        workers: Number of worker processes (default 1).
        log_level: Logging verbosity level (default 'info').
        app_target: FastAPI application instance or ASGI import string.
    """
    import uvicorn

    target = app_target or get_application()
    logger.info(
        f"Starting Uvicorn server on {host}:{port} (workers={workers}, reload={reload}, log_level={log_level})"
    )
    uvicorn.run(
        target,
        host=host,
        port=port,
        reload=reload,
        workers=workers,
        log_level=log_level,
    )


def run_server(
    host: str = "0.0.0.0",
    port: int = 8000,
    reload: bool = False,
    workers: int = 1,
    log_level: str = "info",
) -> None:
    """Runs the FastAPI cli-bot server using the Uvicorn startup method."""
    start_uvicorn(
        host=host,
        port=port,
        reload=reload,
        workers=workers,
        log_level=log_level,
    )


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
    server_parser = subparsers.add_parser(
        "server", help="Start the FastAPI CLI-bot authentication server with Uvicorn"
    )
    server_parser.add_argument(
        "--host",
        default=os.getenv("HOST", "0.0.0.0"),
        help="Host address (default: 0.0.0.0 or $HOST)",
    )
    server_parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("PORT", "8000")),
        help="Port (default: 8000 or $PORT)",
    )
    server_parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development",
    )
    server_parser.add_argument(
        "--workers",
        type=int,
        default=int(os.getenv("WORKERS", "1")),
        help="Number of worker processes (default: 1)",
    )
    server_parser.add_argument(
        "--log-level",
        default=os.getenv("LOG_LEVEL", "info"),
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        help="Uvicorn log verbosity level (default: info)",
    )

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
        run_server(
            host=args.host,
            port=args.port,
            reload=args.reload,
            workers=args.workers,
            log_level=args.log_level,
        )
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
