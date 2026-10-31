# Sarthi IAS Video Studio API (Render web service).
# Build from the repository root, which holds LOGO/, OUTRO/ and reference_dataset/:
#   docker build -f saarthi_video_engine/deploy/api.Dockerfile -t sarthi-studio-api .
FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STUDIO_DATA_DIR=/var/studio/data \
    STUDIO_SCRATCH_DIR=/var/studio/scratch

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-liberation fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY saarthi_video_engine/api/requirements.txt saarthi_video_engine/api/requirements.txt
RUN pip install -r saarthi_video_engine/api/requirements.txt

# Brand assets and the engine code that plan edits and render checks import.
COPY LOGO/ LOGO/
COPY OUTRO/ OUTRO/
COPY reference_dataset/shorts_dataset.json reference_dataset/shorts_dataset.json
COPY saarthi_video_engine/configs/ saarthi_video_engine/configs/
COPY saarthi_video_engine/schemas/ saarthi_video_engine/schemas/
COPY saarthi_video_engine/src/ saarthi_video_engine/src/
COPY saarthi_video_engine/run_phase4.py saarthi_video_engine/run_phase5.py saarthi_video_engine/
COPY saarthi_video_engine/outputs/dr_shivin_phase5/asset_cache/candidates.json \
     saarthi_video_engine/outputs/dr_shivin_phase5/asset_cache/inspire-action-yuri-megis.ogg \
     saarthi_video_engine/outputs/dr_shivin_phase5/asset_cache/
COPY saarthi_video_engine/api/ saarthi_video_engine/api/

RUN useradd --create-home --uid 10001 studio \
 && mkdir -p /var/studio \
 && chown -R studio:studio /var/studio
USER studio
WORKDIR /srv/saarthi_video_engine/api

EXPOSE 8787
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8787} --proxy-headers --forwarded-allow-ips='*' --no-server-header"]
