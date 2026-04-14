# MeshCore Simulator — ready-to-use image
#
# Build:
#   cmake -S . -B build-release -DCMAKE_BUILD_TYPE=Release
#   cmake --build build-release --target orchestrator -j$(nproc)
#   cd webapp && docker compose build
#
# Run:
#   docker compose up

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

# Pre-built orchestrator binary — strip debug symbols (~15% smaller)
COPY build-release/orchestrator/orchestrator /usr/local/bin/orchestrator
RUN strip --strip-all /usr/local/bin/orchestrator \
    && chmod +x /usr/local/bin/orchestrator \
    && apt-get purge -y --auto-remove binutils \
    && rm -rf /var/lib/apt/lists/*

# Webapp source (no __pycache__ — excluded by .dockerignore)
COPY webapp/server/ ./server/
COPY webapp/static/ ./static/
COPY topology_generator/ ./topology_generator/

# Non-root user for runtime
RUN useradd --system --no-create-home app \
    && mkdir -p data/simulations data/configs data/topologies data/sweeps data/interactive data/generators \
    && chown -R app:app /app
USER app

ENV ORCHESTRATOR_PATH=/usr/local/bin/orchestrator
ENV DATA_DIR=/app/data
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
