# MeshCore Simulator — ready-to-use image
#
# Build:
#   python3 tools/firmware.py build --build-dir build-release --build-type Release -j$(nproc)
#   cd webapp && docker compose build
#
# Run (local):
#   docker compose up
#
# Deploy to another machine:
#   docker save meshcore-sim:latest | gzip > meshcore-sim.tar.gz
#   scp meshcore-sim.tar.gz user@host:/path/
#   # On target:
#   docker load < meshcore-sim.tar.gz
#   docker run -d --name meshcore-sim \
#       -p 8000:8000 \
#       -v meshcore-data:/app/data \
#       --restart unless-stopped \
#       meshcore-sim:latest

FROM python:3.11-slim

# Runtime deps for the orchestrator binary (libcrypto, libstdc++, liblua5.4)
# binutils only needed during build for strip, removed afterwards
RUN apt-get update && apt-get install -y --no-install-recommends \
        libssl3 libstdc++6 liblua5.4-0 binutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies (cached layer — only busts when requirements.txt changes)
COPY webapp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-built orchestrator binary + firmware plugins — strip debug symbols
COPY build-release/orchestrator/orchestrator /usr/local/bin/orchestrator
COPY build-release/orchestrator/fw_*.so /usr/local/bin/
RUN strip --strip-all /usr/local/bin/orchestrator /usr/local/bin/fw_*.so \
    && chmod +x /usr/local/bin/orchestrator \
    && apt-get purge -y --auto-remove binutils \
    && rm -rf /var/lib/apt/lists/*

# Webapp source (no __pycache__ — excluded by .dockerignore)
COPY webapp/server/ ./server/
COPY webapp/static/ ./static/
COPY topology_generator/ ./topology_generator/
COPY tools/ ./tools/
COPY firmware.json /usr/local/bin/firmware.json

# Non-root user for runtime
RUN useradd --system --create-home --home-dir /home/app app \
    && mkdir -p data/simulations data/configs data/topologies data/sweeps data/interactive data/generators \
    && mkdir -p /home/app/.cache/srtm \
    && chown -R app:app /app /home/app
USER app

ENV ORCHESTRATOR_PATH=/usr/local/bin/orchestrator
ENV DATA_DIR=/app/data
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
