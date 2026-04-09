# MeshCore Simulator — ready-to-use image
# Requires pre-built orchestrator binary: cmake -S . -B build && cmake --build build --target orchestrator
FROM python:3.11-slim

# Runtime deps for the orchestrator binary (libcrypto3, libstdc++6)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libssl3 libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies (cached layer)
COPY webapp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-built orchestrator binary
COPY build/orchestrator/orchestrator /usr/local/bin/orchestrator
RUN chmod +x /usr/local/bin/orchestrator

# Webapp code
COPY webapp/server/ ./server/
COPY webapp/static/ ./static/

# Data directories
RUN mkdir -p data/simulations data/configs data/topologies data/sweeps

ENV ORCHESTRATOR_PATH=/usr/local/bin/orchestrator
ENV DATA_DIR=/app/data

EXPOSE 8000
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
