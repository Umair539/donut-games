FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# dependencies first, so rebuilds are fast when only the code changes
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY Server ./Server
COPY web ./web

RUN useradd --create-home app
USER app

# Rooms live in memory, so run exactly one process (the default).
ENV HOST=0.0.0.0 PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz')"

CMD ["python", "-m", "Server.core.app"]
