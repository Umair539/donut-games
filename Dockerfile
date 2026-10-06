FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# dependencies first, so rebuilds are fast when only the code changes
COPY requirements.txt .
RUN pip install -r requirements.txt

# The web pages aren't included, Cloudflare Pages serves them (infra/google/cloudflare.tf)
COPY Server ./Server

# /data is where rooms are saved on shutdown, mount a volume there to keep games through a
# deploy. A new volume copies this folder's owner, so the app user can write to it.
RUN useradd --create-home app && mkdir /data && chown app:app /data
USER app

# Rooms live in memory, so run exactly one process (the default).
ENV HOST=0.0.0.0 PORT=8000 SNAPSHOT_PATH=/data/rooms.json
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz')"

CMD ["python", "-m", "Server.core.app"]
