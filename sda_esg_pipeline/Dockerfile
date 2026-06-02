# ─────────────────────────────────────────────────────────────────────────
# Sandboxed PDF-extraction / pipeline worker (SDAD §4.1).
#
# Runs as the unprivileged `nobody` user. Pair with the AppArmor profile in
# docker/apparmor-pdf-extractor.profile (loaded on the host, applied via
# `--security-opt apparmor=esg-pdf-extractor`) to restrict file/network access.
# ─────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# System deps for the extraction stack:
#   tesseract-ocr + vie   → OCR (§7)
#   ghostscript           → Camelot (lattice table detection)
#   libgl1 / libglib2.0   → OpenCV used by camelot-py[cv]
#   poppler-utils         → PDF rendering utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-vie \
        ghostscript \
        poppler-utils \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .
RUN pip install --no-deps -e .

# Drop privileges — extraction must never run as root (§4.1).
RUN chown -R nobody:nogroup /app
USER nobody

# Default: a Phase 2 worker consuming the phase2 queue.
CMD ["celery", "-A", "esg_pipeline.scheduler.celery_app:app", \
     "worker", "--loglevel=INFO", "-Q", "phase2,phase1", "--concurrency=2"]
