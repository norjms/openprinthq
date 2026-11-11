# ArtisanHub Farm's slicing service: OrcaSlicer's command line behind a small HTTP API.
#
# OrcaSlicer is the official release, unmodified, taken from its Linux AppImage and
# checked against the release's SHA-256. The version and checksums come from
# slicer/orcaslicer.env, passed in as build arguments by the build workflow.
ARG ORCASLICER_VERSION
ARG ORCA_SLICER_API_COMMIT

FROM node:22-bookworm AS api
ARG ORCA_SLICER_API_COMMIT
WORKDIR /src
RUN test -n "$ORCA_SLICER_API_COMMIT" \
 && git init -q . \
 && git remote add origin https://github.com/maziggy/orca-slicer-api.git \
 && git fetch -q --depth 1 origin "$ORCA_SLICER_API_COMMIT" \
 && git checkout -q FETCH_HEAD \
 && npm ci --no-audit --no-fund \
 && npm run build \
 && npm prune --omit=dev

FROM ubuntu:24.04 AS orca
ARG TARGETARCH
ARG ORCASLICER_VERSION
ARG ORCASLICER_AMD64_SHA256
ARG ORCASLICER_ARM64_SHA256
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /opt
RUN set -eu; \
    test -n "$ORCASLICER_VERSION"; \
    base="https://github.com/OrcaSlicer/OrcaSlicer/releases/download/v${ORCASLICER_VERSION}"; \
    case "$TARGETARCH" in \
      amd64) file="OrcaSlicer_Linux_AppImage_Ubuntu2404_V${ORCASLICER_VERSION}.AppImage"; sum="$ORCASLICER_AMD64_SHA256" ;; \
      arm64) file="OrcaSlicer_Linux_AppImage_Ubuntu2404_aarch64_V${ORCASLICER_VERSION}.AppImage"; sum="$ORCASLICER_ARM64_SHA256" ;; \
      *) echo "no OrcaSlicer build for $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o orca.AppImage "$base/$file"; \
    echo "$sum  orca.AppImage" | sha256sum -c -; \
    chmod +x orca.AppImage; \
    ./orca.AppImage --appimage-extract >/dev/null; \
    rm orca.AppImage

FROM ubuntu:24.04
ARG ORCASLICER_VERSION
ARG ORCA_SLICER_API_COMMIT
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl \
      libgl1 libgl1-mesa-dri libegl1 libopengl0 libgtk-3-0 libsm6 libice6 \
      libgstreamer1.0-0 libgstreamer-plugins-base1.0-0 libwebkit2gtk-4.1-0 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=api /usr/local/bin/node /usr/local/bin/node
COPY --from=api /src/dist/src /app/dist
COPY --from=api /src/node_modules /app/node_modules
COPY --from=orca /opt/squashfs-root /app/squashfs-root
RUN mkdir -p /app/data
ENV PORT=3000 \
    ORCASLICER_PATH=/app/squashfs-root/AppRun \
    DATA_PATH=/app/data \
    NODE_ENV=production \
    ORCASLICER_VERSION=${ORCASLICER_VERSION}
LABEL org.opencontainers.image.title="artisan-hub-farm-slicer" \
      org.opencontainers.image.version="${ORCASLICER_VERSION}" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later" \
      org.artisanhub.orcaslicer.version="${ORCASLICER_VERSION}" \
      org.artisanhub.orca-slicer-api.revision="${ORCA_SLICER_API_COMMIT}"
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://localhost:3000/health || exit 1
CMD ["node", "/app/dist/index.js"]
