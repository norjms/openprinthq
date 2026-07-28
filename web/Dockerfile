# OpenPrintHQ web (SvelteKit + adapter-node)
# SPDX-License-Identifier: AGPL-3.0-or-later
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json ./
RUN npm install --no-audit --no-fund
COPY . .
# Deployed commit SHAs for the /legal AGPL §13 source offer (optional; empty by default).
ARG OPHQ_APP_COMMIT=""
ARG OPHQ_ENGINE_COMMIT=""
ENV OPHQ_APP_COMMIT=$OPHQ_APP_COMMIT
ENV OPHQ_ENGINE_COMMIT=$OPHQ_ENGINE_COMMIT
RUN npm run build && npm prune --omit=dev

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000
ENV BODY_SIZE_LIMIT=0
COPY --from=build /app/build ./build
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/package.json ./package.json
EXPOSE 3000
CMD ["node", "build"]
