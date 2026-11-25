# OpenPrintHQ web (SvelteKit + adapter-node)
# SPDX-License-Identifier: AGPL-3.0-or-later
# Built from the repository root (docker build -f farm/web/Dockerfile .), since the shared look (openprinthq-ui) is
# ui/ in this repository; farm/web/Dockerfile.dockerignore keeps the context to these two folders.
FROM node:22-alpine AS build
COPY ui/ /src/ui/
WORKDIR /src/farm/web
COPY farm/web/package.json farm/web/.npmrc ./
RUN npm install --no-audit --no-fund
COPY farm/web/ ./
# Deployed commit SHAs for the /legal AGPL §13 source offer (optional; empty by default).
ARG OPHQ_APP_COMMIT=""
ARG OPHQ_ENGINE_COMMIT=""
# Path the site is served under (OpenPrintHQ Farm: /apps/farm). Empty = site root.
ARG BASE_PATH=""
ENV BASE_PATH=$BASE_PATH
ENV OPHQ_APP_COMMIT=$OPHQ_APP_COMMIT
ENV OPHQ_ENGINE_COMMIT=$OPHQ_ENGINE_COMMIT
RUN npm run build && npm prune --omit=dev

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000
ENV BODY_SIZE_LIMIT=0
COPY --from=build /src/farm/web/build ./build
COPY --from=build /src/farm/web/node_modules ./node_modules
COPY --from=build /src/farm/web/package.json ./package.json
EXPOSE 3000
CMD ["node", "build"]
