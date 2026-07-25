# OpenPrintHQ control-plane
# SPDX-License-Identifier: AGPL-3.0-or-later
FROM node:22-alpine
WORKDIR /app
# docker CLI so the provisioner can orchestrate per-tenant engine containers
RUN apk add --no-cache docker-cli
COPY package.json ./
RUN npm install --no-audit --no-fund --omit=dev
COPY . .
ENV NODE_ENV=production
ENV PORT=8080
EXPOSE 8080
CMD ["node", "src/server.js"]
