# Dockerfile
# The recipe for running Lifeguard-Agent anywhere with Docker: Render, a Nebius VM, or a laptop.
# It starts from a small Linux with Python, installs our packages, copies only the code the
# running app needs, and starts the web server.
#
#   Build:  docker build -t lifeguard-agent .
#   Run:    docker run -p 8000:8000 --env-file .env lifeguard-agent      then open http://localhost:8000
#
# Secrets are never copied into the image (see .dockerignore): they come from the host at run time.

FROM python:3.13-slim

# No .pyc files inside the container, and log lines show up at once instead of waiting in a buffer
# (otherwise errors could be missing from Render's log when something goes wrong).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Packages first, code second: Docker reuses this step until requirements.txt changes,
# so a change to the code alone rebuilds in seconds instead of reinstalling everything.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Only what the running app needs. Scripts, tests and results stay out of the image.
COPY app/ app/
COPY static/ static/

# Run as a normal user, not root: if someone ever broke into the app, they wouldn't own the container.
RUN useradd --create-home lifeguard
USER lifeguard

# Render tells the app which port to use in $PORT; elsewhere it's 8000.
EXPOSE 8000

# Docker asks this every 30 s; "docker ps" then shows healthy or unhealthy (useful on the VM).
# Python instead of curl, because the slim image has no curl.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://localhost:{os.environ.get(\"PORT\", \"8000\")}/health', timeout=4)"

# One process on purpose: the sessions live in this process's memory, so a second worker would
# have its own and visitors would lose their server half the time.
# "exec" makes uvicorn the main process, so it gets the stop signal and shuts down cleanly.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
