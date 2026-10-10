FROM python:3.13-slim-bookworm

# Install uv package manager
RUN pip install --no-cache-dir uv

# Set working directory
WORKDIR /app

# Configure Python and uv environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    OLLAMA_BASE_URL="http://host.docker.internal:11434"

# Install system dependencies required for git repos, audio encoding/decoding, and Kokoro speech phonemization
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ca-certificates \
    git \
    ffmpeg \
    libsndfile1 \
    espeak-ng \
    portaudio19-dev \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency manifests first to leverage Docker layer caching
COPY pyproject.toml uv.lock ./

# Install Python dependencies without installing project code yet
RUN uv sync --frozen --no-install-project --no-dev

# Copy application source directories and files
COPY README.md ./
COPY main.py ./
COPY vtop_vitap_agent/ ./vtop_vitap_agent/
COPY vtop_mcp_tool/ ./vtop_mcp_tool/
COPY vtop_vitap_login/ ./vtop_vitap_login/
COPY cli-bot/ ./cli-bot/
COPY nginx/ ./nginx/

# Install the project packages
RUN uv sync --frozen --no-dev

# Expose port for optional FastAPI/web endpoints
EXPOSE 8000

# Default command runs the Uvicorn FastAPI server on port 8000
CMD ["python", "main.py", "server", "--host", "0.0.0.0", "--port", "8000"]
