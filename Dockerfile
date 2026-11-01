# ChequeVerify AI – single container: React build served by the Flask API.
# Build:  docker build -t chequeverify .
# Run:    docker run -p 8000:8000 -e CV_ADMIN_PASSWORD=Change-me-123 -v cv-data:/data chequeverify
# Everything that must survive restarts lives in /data (database, uploads, specimens, models, reports).

FROM node:20-alpine AS ui
WORKDIR /ui
COPY Frontend/package.json Frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY Frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    CV_INSTANCE_DIR=/data/instance CV_MODELS_DIR=/data/models CV_REPORTS_DIR=/data/reports \
    CV_OCR_ENGINE=tesseract CV_PROXY_HOPS=0 CV_CORS_ORIGINS="" PORT=8000
# glibc malloc: fixed thresholds and two arenas stop freed image buffers from piling up in a multi-threaded process.
# Measured with scripts/deploy_simulation.py: eight simultaneous 12-megapixel uploads peak at about 250 MB with these
# settings and at 370-580 MB without them (a Render Starter instance has 512 MB), at about 10 % more processing time.
ENV MALLOC_ARENA_MAX=2 MALLOC_MMAP_THRESHOLD_=131072 MALLOC_TRIM_THRESHOLD_=131072
# The image is meant to be safe in a 512 MB container (Render Starter) without further settings. Measured with
# scripts/deploy_simulation.py: two 25-megapixel scans in flight peaked at 540 MB, which is over 512 MB; 12 megapixels
# (a full 600-dpi cheque is 10.7 MP) with two images in flight stays near 270 MB. A host with more memory can raise
# CV_MAX_IMAGE_PIXELS (the application's own default outside Docker is 25 MP); further requests wait for a free slot.
ENV CV_MAX_IMAGE_PIXELS=12000000 CV_MAX_CONCURRENT_IMAGES=2
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 app \
 && mkdir -p /data && chown app /data
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY reports/ reports/
COPY --from=ui /ui/dist Frontend/dist
# The entrypoint starts as root to prepare /data (hosted disks are mounted root-owned),
# then drops to the unprivileged "app" user before starting the server.
WORKDIR /app/backend
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/api/health')" || exit 1
CMD ["python", "docker_entrypoint.py"]
