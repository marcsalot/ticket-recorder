# ── KTCK The Ticket – Stream Recorder ────────────────────────────────────────
# Build:   docker build -t ticket-recorder .
# Run:     docker compose up -d      (see docker-compose.yml)

FROM python:3.11-slim

# Install ffmpeg
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy application (overrides.json is bind-mounted at runtime via compose)
COPY recorder.py .

# Recordings are written here — mount a host directory or named volume
VOLUME ["/recordings"]

# -u = unbuffered stdout so logs appear immediately in `docker logs`
ENTRYPOINT ["python3", "-u", "recorder.py"]
CMD ["--output-dir", "/recordings"]
