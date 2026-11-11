# SPDX-License-Identifier: AGPL-3.0-or-later
FROM nginx:1.27-alpine
# The path the web image is built for; the published images use /apps/farm.
# OPHQ_BASE_PATH can override it when the container starts.
ARG BASE_PATH=
ENV OPHQ_BASE_PATH=${BASE_PATH}
COPY nginx.conf /etc/nginx/ophq.conf.in
COPY --chmod=0755 15-ophq-base-path.sh /docker-entrypoint.d/15-ophq-base-path.sh
