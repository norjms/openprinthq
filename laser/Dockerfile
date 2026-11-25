# OpenPrintHQ Laser: the static page, served by nginx at /apps/laser/.
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts
COPY . .
RUN sh tools/build.sh

FROM nginx:1.27-alpine
LABEL org.opencontainers.image.title="OpenPrintHQ Laser" \
      org.opencontainers.image.source="https://github.com/norjms/openprinthq-laser" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html/apps/laser
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1/healthz || exit 1
