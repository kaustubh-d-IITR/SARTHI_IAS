# Sarthi IAS Video Studio worker (Render background worker).
# Runs transcription, visual analysis and the existing HyperFrames render from the job queue.
# Build from the repository root:
#   docker build -f saarthi_video_engine/deploy/worker.Dockerfile -t sarthi-studio-worker .
FROM node:22-bookworm-slim AS node

FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CUDA_VISIBLE_DEVICES="" \
    OPENBLAS_NUM_THREADS=1 \
    HF_HOME=/opt/hf \
    STUDIO_ENGINE_PYTHON=/usr/local/bin/python \
    STUDIO_DATA_DIR=/var/studio/data \
    STUDIO_SCRATCH_DIR=/var/studio/scratch

# ffmpeg for the engine; fonts-liberation is metric-compatible with Arial, which the title card names;
# the rest are the shared libraries headless Chrome needs.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates ffmpeg fonts-liberation fonts-dejavu-core fonts-inter \
      libasound2 libatk-bridge2.0-0 libatk1.0-0 libcairo2 libcups2 libdbus-1-3 libdrm2 libexpat1 \
      libgbm1 libglib2.0-0 libnspr4 libnss3 libpango-1.0-0 libx11-6 libxcb1 libxcomposite1 \
      libxdamage1 libxext6 libxfixes3 libxkbcommon0 libxrandr2 libxshmfence1 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && node --version && npx --version

WORKDIR /srv
COPY saarthi_video_engine/api/requirements.txt saarthi_video_engine/api/requirements.txt
COPY saarthi_video_engine/deploy/requirements-models.txt saarthi_video_engine/deploy/requirements-models.txt
RUN pip install -r saarthi_video_engine/api/requirements.txt \
 && pip install --index-url https://download.pytorch.org/whl/cpu torch==2.11.0 \
 && pip install -r saarthi_video_engine/deploy/requirements-models.txt

RUN useradd --create-home --uid 10001 studio \
 && mkdir -p /var/studio /opt/hf \
 && chown -R studio:studio /var/studio /opt/hf
USER studio

# Models and the pinned HyperFrames Chrome are fetched once here, as the runtime user,
# so a render never downloads them. Inter is fetched by HyperFrames on first render and cached.
COPY --chown=studio:studio saarthi_video_engine/deploy/fetch_models.py /tmp/fetch_models.py
RUN python /tmp/fetch_models.py
RUN npx --yes hyperframes@0.8.186 browser ensure \
 && npx --yes hyperframes@0.8.186 browser path

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
COPY saarthi_video_engine/assets/music_library/ saarthi_video_engine/assets/music_library/
COPY saarthi_video_engine/api/ saarthi_video_engine/api/

WORKDIR /srv/saarthi_video_engine/api
CMD ["python", "-m", "app.worker"]
