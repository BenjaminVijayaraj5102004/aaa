# VTOP VIT-AP Login and Session Management

This module provides authentication and session persistence for VTOP student accounts.

## Architecture

The module implements a two-layer separation:
1. **Service layer (`login.py`)**: Handles disk persistence, credential authentication, session restoration, TTL expiration, and resource cleanup.
2. **Consumers (`cli-bot`, `vtop_mcp_tool`)**: Orchestrate user interactions and API routes using the session store and client helpers.

## Features

- **Safe file-based session caching**: Stores sessions as JSON files in a configured directory.
- **Configurable TTL**: Automatically detects expired sessions and removes dead cache files (default: 20 minutes).
- **Path traversal prevention**: Sanitizes registration numbers to ensure session files stay inside the designated directory.
- **Resource cleanup**: Closes HTTP sessions on failure or context exit.
- **Context manager support**: `vtop_client_session` provides an async context manager that restores existing sessions or logs in, cleans up dead sessions if a session error occurs, and closes connections on exit.

## Configuration

Set these environment variables to adjust defaults:

| Variable | Description | Default |
|---|---|---|
| `VTOP_SESSION_DIR` | Directory path for session storage | `<repo_root>/.vtop_sessions` |
| `VTOP_SESSION_TIMEOUT_SECONDS` | Lifetime of a cached session in seconds | `1200` (20 minutes) |

## API

### `VtopSessionStore`

```python
from vtop_vitap_login import VtopSessionStore

store = VtopSessionStore(storage_dir="./custom_sessions", timeout_seconds=1200)

# Save a session
store.save("23BCE7152", session_data)

# Load a session (returns None if expired or missing)
session = store.load("23BCE7152")

# Check if active
if store.is_active("23BCE7152"):
    ttl = store.get_remaining_ttl("23BCE7152")

# List all active student sessions
active_students = store.list_active_sessions()

# Delete a session
store.delete("23BCE7152")

# Clear all cached sessions
store.clear_all()
```

### `authenticate_user`

```python
from vtop_vitap_login import authenticate_user

client = await authenticate_user("23BCE7152", "password")
try:
    data = await client.get_attendance("AP2026272")
finally:
    await client.close()
```

### `get_authenticated_client`

```python
from vtop_vitap_login import get_authenticated_client

# Reuses cached session if present, otherwise logs in with password
client = await get_authenticated_client("23BCE7152", password="password")
```

### `vtop_client_session`

Async context manager that handles reuse and socket cleanup:

```python
from vtop_vitap_login import vtop_client_session

async with vtop_client_session("23BCE7152", password="password") as client:
    timetable = await client.get_timetable("AP2026272")
```

## Running tests

Run the test suite with pytest:

```bash
uv run pytest tests/test_vtop_vitap_login.py -v
```
