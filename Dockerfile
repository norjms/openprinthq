# The screens are the same on every platform, so they are built once, natively.
FROM --platform=$BUILDPLATFORM node:22-alpine AS ui
# The shared look (openprinthq-ui) is ui/ in this repository, beside the frontend, as in the source tree.
COPY ui/ /src/ui/
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json* frontend/.npmrc ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
LABEL org.opencontainers.image.title="OpenPrintHQ" \
      org.opencontainers.image.source="https://github.com/norjms/openprinthq" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends libxml2 libxslt1.1 curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=ui /src/frontend/build ./static
RUN groupadd -g 1000 hub && useradd -u 1000 -g 1000 -G 0 -M -d /app hub \
 && mkdir -p /data /srv/library /srv/orcaslicer-outbox/shared /srv/orcaslicer-outbox/people \
 && chown 1000:1000 /data /srv/library /srv/orcaslicer-outbox/shared /srv/orcaslicer-outbox/people
# These exist and belong to the hub user, so a new Docker volume mounted there is writable: data, the library and
# OrcaSlicer in the browser's outbox (HUB_ORCASLICER_OUTBOX), which the desktop writes as the same user.
USER 1000:1000
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD curl -fsS http://127.0.0.1:8080/healthz || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*", "--workers", "2"]
