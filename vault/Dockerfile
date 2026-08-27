# OpenPrintHQ's build of GyroidVault.
#
# GyroidVault (https://github.com/TeeCodeDev/GyroidVault, AGPL-3.0) is used
# unmodified. This image adds ONLY a theme so the embedded library matches the
# surrounding application: one stylesheet, and an entrypoint that selects it.
#
# Deliberately a derived image rather than a fork. GyroidVault's themes are CSS
# custom properties keyed off a [data-theme] attribute, so a matching look needs
# no source changes and upstream updates stay a base-image bump.
ARG BASE=ghcr.io/teecodedev/gyroidvault:latest
FROM ${BASE}

COPY ophq-theme.css /app/public/css/ophq-theme.css
COPY entrypoint-theme.sh /usr/local/bin/ophq-entrypoint

USER root
RUN chmod 0755 /usr/local/bin/ophq-entrypoint

ENTRYPOINT ["/usr/local/bin/ophq-entrypoint"]
CMD ["node", "server/index.js"]
